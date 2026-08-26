from __future__ import annotations

import base64
import io
import logging
import tempfile
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Loads the llama.cpp server URLs (LLAMA_SERVER_URL / LLAMA_EMBEDDING_SERVER_URL),
# and any API keys for the optional hosted providers, from a local .env file
# (gitignored) so they don't need to be exported in every shell that starts the
# server. The default local setup needs no keys at all.
load_dotenv()

from app.ai_matching.confirmer import LocalMatchConfirmer, MatchConfirmer
from app.ai_matching.embeddings import EmbeddingClient, LocalEmbeddingClient
from app.ai_matching.matcher import match_with_ai
from app.ai_matching.models import AIMatchResult
from app.anomaly.detector import detect_anomalies
from app.anomaly.models import AnomalyResult
from app.ingestion.bank_templates.base import BankPDFTemplate
from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.csv_parser import parse_csv
from app.ingestion.excel_parser import parse_excel
from app.ingestion.ledger_convention import choose_ledger_convention
from app.ingestion.ledger_templates.generic_ledger import (
    BankAccountLedgerTemplate,
    GenericLedgerTemplate,
)
from app.ingestion.pdf_parser import PDFExtractionError, parse_pdf
from app.ingestion.vision_client import LocalPDFExtractor, VisionExtractor
from app.matching.matcher import match
from app.matching.models import MatchResult
from app.matching.near_matcher import match_near
from app.report.builder import build_report
from app.report.classify import classify_unmatched, match_issue, reconciliation_summary
from app.schema import Transaction

logger = logging.getLogger(__name__)

# uvicorn owns the root logger and leaves it at WARNING, so the pipeline's INFO
# diagnostics — which file each side was parsed from, rows recovered from balance
# movements — would never reach the terminal. They are the first thing anyone needs
# when a report looks wrong, so the app's own loggers are wired up explicitly.
_app_logger = logging.getLogger("app")
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    _app_logger.addHandler(_handler)
_app_logger.setLevel(logging.INFO)

app = FastAPI(title="Reconciliation Engine")

# One entry per bank template built and validated so far (spec: add banks one at a
# time, not a generic parser). Separate from ledger templates because a bank
# statement's layout has nothing in common with a ledger export's layout.
_BANK_TEMPLATES: dict[str, BankPDFTemplate] = {"hdfc": HDFCBankTemplate()}
# Two ledger templates because the Debit/Credit convention differs by which account
# the ledger covers, and picking the wrong one inverts every amount in the file
# without failing loudly. "bank_account_ledger" is listed first as it is the usual
# counterpart to a bank statement.
# Offered in the UI ahead of the named conventions: picking the wrong one
# inverts every amount without failing loudly, so detection is the safe default.
AUTO_LEDGER_TEMPLATE = "auto"

_LEDGER_TEMPLATES: dict[str, BankPDFTemplate] = {
    "bank_account_ledger": BankAccountLedgerTemplate(),
    "generic_ledger": GenericLedgerTemplate(),
}


# Dependency providers for the model clients. Routes take these via Depends() rather
# than instantiating directly, so tests can override them with fakes instead of
# running a real model (see tests/test_main.py).
#
# All three default to a locally hosted Qwen model served by llama.cpp: the pipeline
# runs fully offline with no API key, which also keeps client financial data on the
# machine doing the reconciliation. Swap any one of these for its hosted counterpart
# (GeminiVisionExtractor / ClaudeMatchConfirmer / VoyageEmbeddingClient, all still in
# their respective modules) if you'd rather trade that for frontier-model accuracy.
def get_vision_extractor() -> VisionExtractor:
    return LocalPDFExtractor()


def get_embedding_client() -> EmbeddingClient:
    return LocalEmbeddingClient()


def get_match_confirmer() -> MatchConfirmer:
    return LocalMatchConfirmer()


def _log_ingestion(
    bank_txns: list[Transaction], ledger_txns: list[Transaction]
) -> None:
    """Prove the two sides were parsed from genuinely different files.

    Cheap insurance against a whole class of silent failure: if the same upload were
    ever read twice, or one parse fell back to the other's data, every downstream tab
    would still look plausible while being nonsense. Logged on every run so the
    evidence is in the terminal rather than requiring a debugging session.
    """
    bank_files = {t.file_name for t in bank_txns}
    ledger_files = {t.file_name for t in ledger_txns}

    logger.info(
        "Stage 0: bank %d rows from %s | ledger %d rows from %s",
        len(bank_txns), sorted(bank_files), len(ledger_txns), sorted(ledger_files),
    )
    for label, txns in (("bank", bank_txns), ("ledger", ledger_txns)):
        for txn in txns[:3]:
            logger.info(
                "  %-6s %s %12s %r", label, txn.date, txn.amount, txn.description[:44]
            )

    if bank_files & ledger_files:
        logger.error(
            "SAME FILE ON BOTH SIDES: %s — the bank statement and the ledger were "
            "parsed from the same upload, so this reconciliation is meaningless",
            sorted(bank_files & ledger_files),
        )


@app.exception_handler(PDFExtractionError)
async def _pdf_extraction_error_handler(request, exc: PDFExtractionError):
    """Surface an unreadable statement as a 422, not a 500.

    Nothing is broken server-side — the uploaded PDF could not be read reliably, and
    the detail explains which row failed and why. Returning 500 both misreports whose
    fault it is and hides that explanation behind a generic error in the UI.
    """
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/bank-templates")
def bank_templates() -> dict:
    return {"templates": list(_BANK_TEMPLATES)}


@app.get("/ledger-templates")
def ledger_templates() -> dict:
    return {"templates": [AUTO_LEDGER_TEMPLATE, *_LEDGER_TEMPLATES]}


_TEMPLATE_REGISTRIES: dict[Literal["bank", "ledger"], dict[str, BankPDFTemplate]] = {
    "bank": _BANK_TEMPLATES,
    "ledger": _LEDGER_TEMPLATES,
}


async def _read_upload(file: UploadFile) -> bytes:
    return await file.read()


def _reject_identical_uploads(bank_bytes: bytes, ledger_bytes: bytes) -> None:
    """Refuse to reconcile a file against itself.

    Every row then matches its own twin, so the report comes back with a full
    Matched tab and an empty Unmatched tab — the most reassuring possible output,
    and complete nonsense. It is an easy slip to make in a two-box upload form, and
    nothing downstream can detect it, so it is caught here rather than presented as
    a clean reconciliation.
    """
    if bank_bytes == ledger_bytes:
        raise HTTPException(
            422,
            "The bank statement and the ledger are the same file. Reconciling a file "
            "against itself matches every row with itself and produces an empty "
            "Unmatched tab, which looks like a perfect result but means nothing. "
            "Upload the bank statement in one box and the ledger in the other.",
        )


async def _parse_upload(
    file: UploadFile,
    *,
    source: Literal["bank", "ledger"],
    pdf_template: str | None,
    vision_extractor: VisionExtractor,
    contents: bytes | None = None,
) -> list[Transaction]:
    suffix = Path(file.filename or "").suffix.lower()
    if contents is None:
        contents = await file.read()
    registry = _TEMPLATE_REGISTRIES[source]

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(contents)
        tmp.flush()
        tmp_path = Path(tmp.name)

        if suffix == ".csv":
            return parse_csv(tmp_path, source=source, file_name=file.filename)
        if suffix in (".xlsx", ".xls"):
            return parse_excel(tmp_path, source=source, file_name=file.filename)
        if suffix == ".pdf":
            if pdf_template is None or pdf_template not in registry:
                raise HTTPException(
                    400,
                    f"Unsupported or missing template for {source} PDF. "
                    f"Available: {list(registry)}",
                )
            return parse_pdf(
                tmp_path,
                source=source,
                template=registry[pdf_template],
                extractor=vision_extractor,
                file_name=file.filename,
            )

    raise HTTPException(400, f"Unsupported file type: {suffix!r}")


@app.post("/ingest", response_model=list[Transaction])
async def ingest(
    file: UploadFile = File(...),
    source: Literal["bank", "ledger"] = Form(...),
    template: str | None = Form(default=None),
    vision_extractor: VisionExtractor = Depends(get_vision_extractor),
) -> list[Transaction]:
    return await _parse_upload(
        file, source=source, pdf_template=template, vision_extractor=vision_extractor
    )


async def _run_reconciliation(
    *,
    bank_file: UploadFile,
    ledger_file: UploadFile,
    bank_pdf_template: str | None,
    ledger_pdf_template: str | None,
    vision_extractor: VisionExtractor,
    embedding_client: EmbeddingClient,
    confirmer: MatchConfirmer,
) -> tuple[MatchResult, AIMatchResult, AnomalyResult, bytes]:
    """Full pipeline, end to end: ingest both files (Stage 0), deterministic match
    (Stage 1), AI matching net on whatever Stage 1 couldn't resolve (Stage 2),
    anomaly detection across the full reconciled set (Stage 3), then build the
    audit-ready .xlsx report bytes. Shared by every route that runs the pipeline, so
    the .xlsx download and the JSON dashboard preview are always the same result.
    """
    bank_bytes = await _read_upload(bank_file)
    ledger_bytes = await _read_upload(ledger_file)
    _reject_identical_uploads(bank_bytes, ledger_bytes)

    bank_txns = await _parse_upload(
        bank_file,
        source="bank",
        pdf_template=bank_pdf_template,
        vision_extractor=vision_extractor,
        contents=bank_bytes,
    )

    if ledger_pdf_template in (None, AUTO_LEDGER_TEMPLATE) and Path(
        ledger_file.filename or ""
    ).suffix.lower() == ".pdf":
        # Parse the ledger under every convention and keep whichever reconciles.
        # Cheap: extraction is deterministic and runs off the already-read bytes, so
        # this costs a second table parse, not a second model call.
        candidates = {}
        for name in _LEDGER_TEMPLATES:
            candidates[name] = await _parse_upload(
                ledger_file,
                source="ledger",
                pdf_template=name,
                vision_extractor=vision_extractor,
                contents=ledger_bytes,
            )
        ledger_txns = choose_ledger_convention(bank_txns, candidates).transactions
    else:
        ledger_txns = await _parse_upload(
            ledger_file,
            source="ledger",
            pdf_template=ledger_pdf_template,
            vision_extractor=vision_extractor,
            contents=ledger_bytes,
        )

    _log_ingestion(bank_txns, ledger_txns)

    match_result = match(bank_txns, ledger_txns)
    # Stage 1.5: deterministic fee/lag-tolerant recovery before any model is asked.
    match_result = match_near(match_result)
    ai_result = match_with_ai(
        match_result, embedding_client=embedding_client, confirmer=confirmer
    )
    anomaly_result = detect_anomalies(match_result)
    report_bytes = build_report(match_result, ai_result, anomaly_result)

    return match_result, ai_result, anomaly_result, report_bytes


@app.post("/reconcile")
async def reconcile(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    bank_pdf_template: str | None = Form(default=None),
    ledger_pdf_template: str | None = Form(default=None),
    vision_extractor: VisionExtractor = Depends(get_vision_extractor),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    confirmer: MatchConfirmer = Depends(get_match_confirmer),
) -> StreamingResponse:
    """Run the pipeline and return the audit-ready report as a downloadable .xlsx."""
    _, _, _, report_bytes = await _run_reconciliation(
        bank_file=bank_file,
        ledger_file=ledger_file,
        bank_pdf_template=bank_pdf_template,
        ledger_pdf_template=ledger_pdf_template,
        vision_extractor=vision_extractor,
        embedding_client=embedding_client,
        confirmer=confirmer,
    )

    return StreamingResponse(
        io.BytesIO(report_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=reconciliation_report.xlsx"},
    )


def _txn_summary(txn: Transaction) -> dict:
    return {
        "date": txn.date.isoformat(),
        "amount": float(txn.amount),
        "description": txn.description,
        "reference": txn.reference,
        "source": txn.source,
    }


@app.post("/reconcile/preview")
async def reconcile_preview(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    bank_pdf_template: str | None = Form(default=None),
    ledger_pdf_template: str | None = Form(default=None),
    vision_extractor: VisionExtractor = Depends(get_vision_extractor),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    confirmer: MatchConfirmer = Depends(get_match_confirmer),
) -> dict:
    """Run the pipeline once and return it as JSON for the in-browser results
    dashboard, with the .xlsx workbook embedded as base64 so a subsequent download
    doesn't have to run the pipeline (and any live model calls) a second time.
    """
    match_result, ai_result, anomaly_result, report_bytes = await _run_reconciliation(
        bank_file=bank_file,
        ledger_file=ledger_file,
        bank_pdf_template=bank_pdf_template,
        ledger_pdf_template=ledger_pdf_template,
        vision_extractor=vision_extractor,
        embedding_client=embedding_client,
        confirmer=confirmer,
    )

    matched = match_result.matched
    bank_txns_all = [p.bank_transaction for p in matched] + ai_result.unmatched_bank + [
        p.bank_transaction for p in ai_result.ai_matched
    ]
    ledger_txns_all = [p.ledger_transaction for p in matched] + ai_result.unmatched_ledger + [
        p.ledger_transaction for p in ai_result.ai_matched
    ]

    summary_by_item = {
        row["item"]: row
        for row in reconciliation_summary(
            bank_txns_all, ledger_txns_all, matched,
            ai_result.unmatched_bank, ai_result.unmatched_ledger,
        )
        if row["item"]
    }

    return {
        "summary": {
            "bank_total": summary_by_item["Bank transactions"]["amount"],
            "bank_count": summary_by_item["Bank transactions"]["count"],
            "ledger_total": summary_by_item["Ledger transactions"]["amount"],
            "ledger_count": summary_by_item["Ledger transactions"]["count"],
            "difference": summary_by_item["Difference to explain"]["amount"],
            "unexplained": summary_by_item["UNEXPLAINED"]["amount"],
        },
        "matched": [
            {
                "bank": _txn_summary(pair.bank_transaction),
                "ledger": _txn_summary(pair.ledger_transaction),
                "rule": pair.rule,
                "corroboration": pair.corroboration,
                "issue": match_issue(pair),
            }
            for pair in matched
        ],
        "ai_matched": [
            {
                "bank": _txn_summary(pair.bank_transaction),
                "ledger": _txn_summary(pair.ledger_transaction),
                "confidence": pair.confidence,
                "reasoning": pair.reasoning,
            }
            for pair in ai_result.ai_matched
        ],
        "unmatched_bank": [
            {**_txn_summary(txn), "reason": classify_unmatched(txn, ledger_txns_all)}
            for txn in ai_result.unmatched_bank
        ],
        "unmatched_ledger": [
            {**_txn_summary(txn), "reason": classify_unmatched(txn, bank_txns_all)}
            for txn in ai_result.unmatched_ledger
        ],
        "anomalies": [
            {
                "rule": flag.rule,
                "reason": flag.reason,
                "transactions": [_txn_summary(txn) for txn in flag.transactions],
            }
            for flag in anomaly_result.flags
        ],
        "report_base64": base64.b64encode(report_bytes).decode("ascii"),
    }


# Mounted last so it only catches requests that don't match an API route above
# (e.g. "/" -> index.html) — the CA-facing drag-and-drop upload page.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
