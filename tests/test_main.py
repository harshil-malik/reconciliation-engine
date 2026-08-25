from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

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
    assert set(sheets.keys()) == {"Matched", "AI Matched", "Unmatched", "Anomalies"}

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
    assert len(sheets["Unmatched"]) == 0


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
