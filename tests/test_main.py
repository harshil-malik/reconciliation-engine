from __future__ import annotations

import base64
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.ai_matching.confirmer import ConfirmationResult
from app.main import app, get_embedding_client, get_match_confirmer, get_vision_extractor

client = TestClient(app)


class _AllMatchEmbeddingClient:
    """Every text embeds to the same vector, so every candidate pair is shortlisted
    with similarity 1.0 — good enough for an HTTP-level wiring test where the
    similarity-ranking logic itself is already covered by tests/test_shortlist.py."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


class _AllConfirmConfirmer:
    def confirm(self, bank_txn, ledger_txn) -> ConfirmationResult:
        return ConfirmationResult(
            is_match=True, confidence=0.9, reasoning="test fixture: same vendor, fee explains the gap"
        )


class _FakeVisionExtractor:
    def __init__(self, response_text: str):
        self.response_text = response_text

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        return self.response_text


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_csv() -> None:
    csv_content = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT-VENDOR PAYMENT,N123,15000.00,\n"
    )

    response = client.post(
        "/ingest",
        files={"file": ("statement.csv", csv_content, "text/csv")},
        data={"source": "bank"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["amount"] == "-15000.00"
    assert body[0]["source"] == "bank"
    assert body[0]["file_name"] == "statement.csv"


def test_ingest_rejects_unsupported_file_type() -> None:
    response = client.post(
        "/ingest",
        files={"file": ("statement.txt", b"not a real format", "text/plain")},
        data={"source": "bank"},
    )
    assert response.status_code == 400


def test_ingest_pdf_without_template_returns_400() -> None:
    response = client.post(
        "/ingest",
        files={"file": ("statement.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"source": "bank"},
    )
    assert response.status_code == 400
    assert "template for bank PDF" in response.json()["detail"]


def test_ingest_ledger_pdf_without_template_returns_400() -> None:
    response = client.post(
        "/ingest",
        files={"file": ("ledger.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"source": "ledger"},
    )
    assert response.status_code == 400
    assert "template for ledger PDF" in response.json()["detail"]


def test_ledger_templates_endpoint_lists_generic_ledger() -> None:
    response = client.get("/ledger-templates")
    assert response.status_code == 200
    assert "generic_ledger" in response.json()["templates"]


def test_reconcile_runs_full_pipeline_and_returns_report() -> None:
    bank_csv = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT VENDOR PAYMENT,N123,15075.00,\n"
        b"05/04/24,NEFT CONSULTING FEE,,998.00,\n"
    )
    ledger_csv = (
        b"Txn Date,Particulars,Voucher No,Amount\n"
        b"01-04-2024,Vendor Payment - ABC,N123,-15075\n"
        b"05-04-2024,Consulting Fee Payment,,-1000\n"
    )

    app.dependency_overrides[get_embedding_client] = lambda: _AllMatchEmbeddingClient()
    app.dependency_overrides[get_match_confirmer] = lambda: _AllConfirmConfirmer()

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", bank_csv, "text/csv"),
            "ledger_file": ("ledger.csv", ledger_csv, "text/csv"),
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "reconciliation_report.xlsx" in response.headers["content-disposition"]

    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None)
    assert set(sheets.keys()) == {"Summary", "Matched", "Review - Amount", "Review - Date", "Review - Weak Evidence", "AI Matched", "Unmatched - Bank", "Unmatched - Ledger", "Anomalies"}

    matched = sheets["Matched"]
    rules = set(matched["rule"])

    # row 1 (₹15,075, same date both sides) clears Stage 1 deterministically
    assert "exact_amount_same_date" in rules

    # row 2 (₹998 bank vs ₹1000 ledger) fails Stage 1's exact-amount rule, but the
    # ₹2 gap is fee-sized and the descriptions correspond, so Stage 1.5 resolves it
    # without a model — leaving the AI net with nothing to do. The fake confirmer is
    # still wired up above and would happily have claimed this pair; that it no
    # longer gets the chance is the point.
    assert "near_amount_matching_description" in rules
    assert len(matched) == 2

    assert len(sheets["AI Matched"]) == 0
    assert len(sheets["Unmatched - Bank"]) == 0


def test_reconcile_preview_returns_json_matching_the_report() -> None:
    bank_csv = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT VENDOR PAYMENT,N123,15075.00,\n"
        b"05/04/24,NEFT CONSULTING FEE,,998.00,\n"
    )
    ledger_csv = (
        b"Txn Date,Particulars,Voucher No,Amount\n"
        b"01-04-2024,Vendor Payment - ABC,N123,-15075\n"
        b"05-04-2024,Consulting Fee Payment,,-1000\n"
    )

    app.dependency_overrides[get_embedding_client] = lambda: _AllMatchEmbeddingClient()
    app.dependency_overrides[get_match_confirmer] = lambda: _AllConfirmConfirmer()

    response = client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", bank_csv, "text/csv"),
            "ledger_file": ("ledger.csv", ledger_csv, "text/csv"),
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["summary"]["bank_count"] == 2
    assert body["summary"]["ledger_count"] == 2
    assert body["summary"]["unexplained"] == 0.0

    assert len(body["matched"]) == 2
    rules = {pair["rule"] for pair in body["matched"]}
    assert "exact_amount_same_date" in rules
    assert "near_amount_matching_description" in rules

    assert body["ai_matched"] == []
    assert body["unmatched_bank"] == []
    assert body["unmatched_ledger"] == []

    # The embedded workbook is the same bytes /reconcile would have streamed, so the
    # dashboard's "Download .xlsx" button never has to re-run the pipeline.
    report_bytes = base64.b64decode(body["report_base64"])
    sheets = pd.read_excel(io.BytesIO(report_bytes), sheet_name=None)
    assert len(sheets["Matched"]) == 2


def test_reconcile_accepts_pdf_ledger_with_generic_ledger_template() -> None:
    bank_csv = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT VENDOR PAYMENT,N123,15075.00,\n"
    )
    ledger_pdf_rows = [
        {
            "date": "01-04-2024",
            "description": "Vendor Payment - ABC Supplies",
            "reference": "N123",
            "debit": "15075.00",
            "credit": "0",
        }
    ]

    app.dependency_overrides[get_vision_extractor] = lambda: _FakeVisionExtractor(
        json.dumps(ledger_pdf_rows)
    )
    app.dependency_overrides[get_embedding_client] = lambda: _AllMatchEmbeddingClient()
    app.dependency_overrides[get_match_confirmer] = lambda: _AllConfirmConfirmer()

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", bank_csv, "text/csv"),
            "ledger_file": ("ledger.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
        data={"ledger_pdf_template": "generic_ledger"},
    )

    assert response.status_code == 200
    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None)
    assert len(sheets["Matched"]) == 1
    assert sheets["Matched"].iloc[0]["rule"] == "exact_amount_same_date"


def test_reconcile_rejects_the_same_file_on_both_sides() -> None:
    """Reconciling a file against itself matches every row with its own twin and
    empties the Unmatched tab — the most reassuring possible output, and nonsense."""
    csv_bytes = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT VENDOR PAYMENT,N123,15075.00,\n"
    )

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("statement.csv", csv_bytes, "text/csv"),
            "ledger_file": ("statement.csv", csv_bytes, "text/csv"),
        },
    )

    assert response.status_code == 422
    assert "same file" in response.json()["detail"]


# A bank statement and the client's own Bank A/c ledger recording the same two
# events from opposite sides: what the statement calls a Withdrawal, the ledger
# books as a Credit to the bank asset account.
_BANK_CSV = (
    b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
    b"01/04/24,NEFT VENDOR PAYMENT ABC SUPPLIES,N123,15075.00,\n"
    b"03/04/24,UPI CUSTOMER RECEIPT INV1042,U987,,25000.00\n"
)
_BANK_ACCOUNT_LEDGER_CSV = (
    b"Txn Date,Particulars,Voucher No,Debit,Credit\n"
    b"01-04-2024,Vendor Payment - ABC Supplies,N123,,15075\n"
    b"03-04-2024,Customer Receipt - Invoice 1042,U987,25000,\n"
)


def test_csv_ledger_gets_debit_credit_convention_detection() -> None:
    """A CSV/Excel ledger was always read as "Credit means money in".

    Detection ran for PDFs only, so the client's own Bank A/c ledger — where a Debit
    is money arriving, and which is the usual counterpart to a bank statement — came
    out with every amount inverted. Nothing errored: the amounts simply stopped
    agreeing and every row landed in Unmatched, which looks exactly like a client
    whose books are in poor shape.
    """
    response = client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", _BANK_CSV, "text/csv"),
            "ledger_file": ("ledger.csv", _BANK_ACCOUNT_LEDGER_CSV, "text/csv"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["matched"]) == 2
    assert body["unmatched_bank"] == []
    assert body["unmatched_ledger"] == []
    assert body["summary"]["unexplained"] == 0.0


def test_named_ledger_template_is_honoured_for_a_csv_ledger() -> None:
    """`ledger_pdf_template` was consulted only on the PDF path, so naming a
    convention for a CSV or Excel ledger was silently ignored and the default
    reading used regardless. Overriding detection is the documented escape hatch for
    a file too small or too disjoint for detection to call, so it has to work on
    every format."""
    response = client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", _BANK_CSV, "text/csv"),
            "ledger_file": ("ledger.csv", _BANK_ACCOUNT_LEDGER_CSV, "text/csv"),
        },
        data={"ledger_pdf_template": "bank_account_ledger"},
    )

    assert response.status_code == 200
    assert len(response.json()["matched"]) == 2


def test_anomaly_config_thresholds_can_be_set_per_client() -> None:
    """Stage 3's thresholds were hardcoded to their defaults — `AnomalyConfig`
    existed and `detect_anomalies` accepted one, but no route ever passed it. A
    client whose approval limit is not 50,000 got flags calibrated to someone
    else's business."""
    bank_csv = (
        b"Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n"
        b"01/04/24,NEFT VENDOR PAYMENT ABC,N123,19500.00,\n"
    )
    ledger_csv = (
        b"Txn Date,Particulars,Voucher No,Amount\n"
        b"01-04-2024,Vendor Payment - ABC,N123,-19500\n"
    )
    files = {
        "bank_file": ("bank.csv", bank_csv, "text/csv"),
        "ledger_file": ("ledger.csv", ledger_csv, "text/csv"),
    }

    def threshold_flags(data: dict) -> list[dict]:
        response = client.post("/reconcile/preview", files=files, data=data)
        assert response.status_code == 200
        return [
            flag
            for flag in response.json()["anomalies"]
            if flag["rule"] == "just_below_approval_threshold"
        ]

    # 19,500 sits under nobody's default threshold (50,000 / 1,00,000 / 2,00,000).
    assert threshold_flags({}) == []

    # For a client who requires sign-off above 20,000 it is the classic pattern.
    # Flagged twice because the payment is on both sides of a clean reconciliation
    # and the rule reads every row it is given, whichever book it came from.
    assert len(threshold_flags({
        "anomaly_config": json.dumps(
            {"approval_thresholds": ["20000"], "threshold_margin_pct": "0.05"}
        )
    })) == 2


def test_invalid_anomaly_config_is_rejected_rather_than_silently_defaulted() -> None:
    """Falling back to defaults on a malformed config would show a reviewer the
    flags they had explicitly asked not to see, with nothing to say why."""
    response = client.post(
        "/reconcile/preview",
        files={
            "bank_file": ("bank.csv", _BANK_CSV, "text/csv"),
            "ledger_file": ("ledger.csv", _BANK_ACCOUNT_LEDGER_CSV, "text/csv"),
        },
        data={"anomaly_config": json.dumps({"duplicate_window_days": "not a number"})},
    )

    assert response.status_code == 422
    assert "anomaly_config" in response.json()["detail"]


def test_anomaly_config_endpoint_returns_the_defaults() -> None:
    response = client.get("/anomaly-config")
    assert response.status_code == 200
    body = response.json()
    assert body["approval_thresholds"] == ["50000", "100000", "200000"]
    assert body["duplicate_window_days"] == 1


def test_oversized_upload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An upload was read into memory with no ceiling, so a mis-dropped file — a
    video, a disk image, a multi-gigabyte export — was buffered whole before
    anything looked at it. A statement is far under a megabyte, so a large one is
    already a mistake and should say so rather than exhaust the machine."""
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"x" * 5000, "text/csv"),
            "ledger_file": ("ledger.csv", _BANK_ACCOUNT_LEDGER_CSV, "text/csv"),
        },
    )

    assert response.status_code == 413
    assert "larger than the" in response.json()["detail"]
