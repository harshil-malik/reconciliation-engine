from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.ingestion import vision_client
from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.vision_client import LocalPDFExtractor
from app.local_llm import LlamaCppClient, LlamaCppServerError

_MODEL_ROWS = {
    "rows": [
        {
            "date": "01/04/24",
            "description": "NEFT-AXISBANK-VENDOR PAYMENT",
            "reference": "N123456789",
            "debit": "15,000.00",
            "credit": "0",
        }
    ]
}


def _extractor(handler, **kwargs) -> LocalPDFExtractor:
    return LocalPDFExtractor(
        LlamaCppClient(
            base_url="http://127.0.0.1:8080",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        **kwargs,
    )


def _blank_pdf_bytes() -> bytes:
    """A real PDF with no text layer — stands in for a scanned statement."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_extract_sends_template_prompt_and_pdf_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vision_client,
        "_pdf_text",
        # (text, page_starts) — the offsets are what let a citation name a page.
        lambda _: ("01/04/24  VENDOR PAYMENT  15,000.00", [0]),
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_MODEL_ROWS)}}]}
        )

    template_prompt = HDFCBankTemplate().build_prompt()
    result = _extractor(handler).extract(b"%PDF-fake", template_prompt)

    sent = captured["body"]["messages"][0]["content"]
    # the per-bank template drives extraction unchanged; only the transport differs
    assert template_prompt in sent
    assert "01/04/24  VENDOR PAYMENT  15,000.00" in sent
    assert json.loads(result) == _MODEL_ROWS


def test_extractor_output_parses_through_the_bank_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The contract that matters: what the local extractor returns must flow through
    the existing template + normalization path into canonical Transactions."""
    monkeypatch.setattr(
        vision_client, "_pdf_text", lambda _: ("some statement text", [0])
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_MODEL_ROWS)}}]}
        )

    pdf_path = tmp_path / "hdfc_statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    transactions = parse_pdf(
        pdf_path,
        source="bank",
        template=HDFCBankTemplate(),
        extractor=_extractor(handler),
    )

    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("-15000.00")
    assert transactions[0].reference == "N123456789"
    assert transactions[0].source == "bank"


def test_scanned_pdf_with_no_text_layer_raises_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not reach the model for a scanned PDF")

    with pytest.raises(LlamaCppServerError, match="scanned/image-based"):
        _extractor(handler).extract(_blank_pdf_bytes(), "prompt")
