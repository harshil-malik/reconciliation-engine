from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.pdf_parser import parse_pdf


class FakeVisionExtractor:
    """Stands in for ClaudeVisionExtractor so PDF-pipeline tests don't hit a real
    API — they exercise prompt->parse->normalize wiring with a canned response."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[tuple[bytes, str]] = []

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        self.calls.append((pdf_bytes, prompt))
        return self.response_text


_CANNED_ROWS = [
    {
        "date": "01/04/24",
        "description": "NEFT-AXISBANK-VENDOR PAYMENT",
        "reference": "N123456789",
        "debit": "15,000.00",
        "credit": "0",
    },
    {
        "date": "03/04/24",
        "description": "UPI-CUSTOMER-INV1042",
        "reference": None,
        "debit": "0",
        "credit": "25000.00",
    },
]


def test_parse_pdf_builds_transactions_from_vision_response(tmp_path: Path) -> None:
    pdf_path = tmp_path / "hdfc_statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes")

    extractor = FakeVisionExtractor(json.dumps(_CANNED_ROWS))
    transactions = parse_pdf(
        pdf_path, source="bank", template=HDFCBankTemplate(), extractor=extractor
    )

    assert len(transactions) == 2
    assert transactions[0].amount == Decimal("-15000.00")
    assert transactions[0].reference == "N123456789"
    assert transactions[1].amount == Decimal("25000.00")
    assert transactions[1].reference is None
    assert all(t.source == "bank" and t.file_name == "hdfc_statement.pdf" for t in transactions)

    # the template's prompt (not raw instructions) is what got sent to the extractor
    assert extractor.calls[0][1] == HDFCBankTemplate().build_prompt()


def test_hdfc_template_strips_markdown_fences_around_json() -> None:
    fenced = f"```json\n{json.dumps(_CANNED_ROWS)}\n```"
    rows = HDFCBankTemplate().parse_response(fenced)
    assert rows == _CANNED_ROWS


def test_hdfc_template_raises_on_response_with_no_json_array() -> None:
    with pytest.raises(ValueError, match="No JSON array found"):
        HDFCBankTemplate().parse_response("Sorry, I could not read this statement.")
