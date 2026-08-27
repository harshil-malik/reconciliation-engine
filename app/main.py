from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

# Loads the llama.cpp server URLs (LLAMA_SERVER_URL / LLAMA_EMBEDDING_SERVER_URL),
# and any API keys for the optional hosted providers, from a local .env file
# (gitignored) so they don't need to be exported in every shell that starts the
# server. The default local setup needs no keys at all.
load_dotenv()

from app.ai_matching.confirmer import LocalMatchConfirmer, MatchConfirmer
from app.ai_matching.embeddings import EmbeddingClient, LocalEmbeddingClient
from app.ai_matching.matcher import match_with_ai
from app.ai_matching.models import AIMatchResult
from app.anomaly.config import AnomalyConfig
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


@app.get("/anomaly-config")
def anomaly_config_defaults() -> AnomalyConfig:
    """The thresholds a run uses when none are supplied.

    Exposed so a caller can read the defaults, change the one field this engagement
    needs and post the result back, rather than having to restate the whole config
    or read them out of the source.
    """
    return AnomalyConfig()


def _parse_anomaly_config(raw: str | None) -> AnomalyConfig:
    """Build the Stage 3 config from a JSON form field, falling back to defaults.

    Thresholds are per-engagement, not fixed policy — a client whose approval limit
    is 25,00,000 gets nothing useful from flags calibrated to 50,000, and one whose
    every payment is a round number needs the round-number rule tuned or silent.
    Sent as one JSON field rather than a form field per threshold so the config can
    gain settings without the API changing shape, and so pydantic does the
    validating.

    Unknown or malformed settings are a 422, never a silent fallback to defaults: a
    typo'd field name that quietly reverted to 50,000 would show a reviewer flags
    they had explicitly asked not to see, with nothing to indicate why.
    """
    if raw is None or not raw.strip():
        return AnomalyConfig()
    try:
        return AnomalyConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            422, f"Invalid anomaly_config: {exc.error_count()} problem(s) — {exc}"
        ) from exc


_TEMPLATE_REGISTRIES: dict[Literal["bank", "ledger"], dict[str, BankPDFTemplate]] = {
    "bank": _BANK_TEMPLATES,
    "ledger": _LEDGER_TEMPLATES,
}


# An upload is held in memory whole — extraction needs random access to the file,
# and both sides are kept at once so the identical-upload check can compare them. A
# cap keeps a mis-drop (a video, a disk image, a 2 GB export) from taking the box
# down with it instead of returning an error. Generous by design: statements and
# ledger exports are well under a megabyte, so anything near this is already a
# mistake. Override with MAX_UPLOAD_MB for an unusually large multi-year export.
MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024)

_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    """Read an upload into memory, refusing anything over MAX_UPLOAD_BYTES.

    Read in chunks and checked as it goes, so an oversized file is rejected partway
    rather than after it has already been buffered — checking `file.size` or the
    Content-Length header instead would mean trusting the client about the very
    thing being limited.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"{file.filename or 'The upload'} is larger than the "
                # :g so a fractional override reads as "0.5 MB", not "0 MB".
                f"{MAX_UPLOAD_BYTES / (1024 * 1024):g} MB limit. Bank statements and "
                "ledger exports are far smaller than this, so check that the right "
                "file was selected; raise MAX_UPLOAD_MB if it genuinely is this big.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
    debit_is_inflow: bool = False,
) -> list[Transaction]:
    suffix = Path(file.filename or "").suffix.lower()
    if contents is None:
        contents = await _read_upload(file)
    registry = _TEMPLATE_REGISTRIES[source]

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(contents)
        tmp.flush()
        tmp_path = Path(tmp.name)

        if suffix == ".csv":
            return parse_csv(
                tmp_path,
                source=source,
                file_name=file.filename,
                debit_is_inflow=debit_is_inflow,
            )
        if suffix in (".xlsx", ".xls"):
            return parse_excel(
                tmp_path,
                source=source,
                file_name=file.filename,
                debit_is_inflow=debit_is_inflow,
            )
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
    # A named ledger template sets the Debit/Credit convention for the tabular
    # formats too, not only for PDFs. There is no bank side here to detect against,
    # so on this route an unnamed spreadsheet ledger keeps the default reading —
    # /reconcile is where detection happens.
    ledger_template = _LEDGER_TEMPLATES.get(template or "") if source == "ledger" else None
    return await _parse_upload(
        file,
        source=source,
        pdf_template=template,
        vision_extractor=vision_extractor,
        debit_is_inflow=bool(ledger_template and ledger_template.debit_is_inflow),
    )


# Formats read into a DataFrame rather than through a PDF template. Their
# Debit/Credit convention is just as ambiguous as a PDF's, so they get the same
# detection — the hazard belongs to double-entry bookkeeping, not to the file format.
_TABULAR_SUFFIXES = (".csv", ".xlsx", ".xls")


async def _parse_ledger(
    ledger_file: UploadFile,
    *,
    bank_txns: list[Transaction],
    ledger_template: str | None,
    vision_extractor: VisionExtractor,
    contents: bytes,
) -> list[Transaction]:
    """Parse the ledger under the Debit/Credit convention that actually reconciles.

    Whether a ledger's Debit means money in depends on which account the ledger
    covers, and the file rarely says. Reading it backwards inverts every amount
    without failing loudly. Unless the caller names a convention, both readings are
    produced and `choose_ledger_convention` keeps whichever corroborates more
    matches against the bank.

    This used to run for PDFs only, so a CSV or Excel ledger was always read as
    "Credit means money in" — correct for a party ledger, backwards for the client's
    own Bank A/c ledger, which is the usual counterpart to a bank statement. That
    silently inverted every amount in the file, and a named ledger template was
    ignored outright on those formats.
    """
    suffix = Path(ledger_file.filename or "").suffix.lower()

    async def parse_as(name: str | None) -> list[Transaction]:
        template = _LEDGER_TEMPLATES.get(name or "")
        return await _parse_upload(
            ledger_file,
            source="ledger",
            pdf_template=name,
            vision_extractor=vision_extractor,
            contents=contents,
            # Only consulted for the tabular formats; a PDF carries the convention
            # in the template itself.
            debit_is_inflow=template.debit_is_inflow if template else False,
        )

    detectable = suffix == ".pdf" or suffix in _TABULAR_SUFFIXES
    if ledger_template not in (None, AUTO_LEDGER_TEMPLATE) or not detectable:
        return await parse_as(ledger_template)

    # Parse under every convention and keep whichever reconciles. Cheap: extraction
    # is deterministic and runs off the already-read bytes, so this costs a second
    # table parse, not a second model call.
    candidates = {name: await parse_as(name) for name in _LEDGER_TEMPLATES}

    # A sheet with one signed Amount column reads identically either way, so there
    # is nothing to choose. Saying so beats reporting a tie as an ambiguous call.
    readings = list(candidates.values())
    if all(
        [t.amount for t in reading] == [t.amount for t in readings[0]]
        for reading in readings
    ):
        logger.info(
            "Ledger has no Debit/Credit split to interpret — amounts are unambiguous"
        )
        return readings[0]

    return choose_ledger_convention(bank_txns, candidates).transactions


async def _run_reconciliation(
    *,
    bank_file: UploadFile,
    ledger_file: UploadFile,
    bank_pdf_template: str | None,
    ledger_pdf_template: str | None,
    vision_extractor: VisionExtractor,
    embedding_client: EmbeddingClient,
    confirmer: MatchConfirmer,
    anomaly_config: AnomalyConfig | None = None,
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

    ledger_txns = await _parse_ledger(
        ledger_file,
        bank_txns=bank_txns,
        ledger_template=ledger_pdf_template,
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
    anomaly_result = detect_anomalies(match_result, config=anomaly_config)
    report_bytes = build_report(match_result, ai_result, anomaly_result)

    return match_result, ai_result, anomaly_result, report_bytes


@app.post("/reconcile")
async def reconcile(
    bank_file: UploadFile = File(...),
    ledger_file: UploadFile = File(...),
    bank_pdf_template: str | None = Form(default=None),
    ledger_pdf_template: str | None = Form(default=None),
    anomaly_config: str | None = Form(default=None),
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
        anomaly_config=_parse_anomaly_config(anomaly_config),
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
    anomaly_config: str | None = Form(default=None),
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
        anomaly_config=_parse_anomaly_config(anomaly_config),
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
