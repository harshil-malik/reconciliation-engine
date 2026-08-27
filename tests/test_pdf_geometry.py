from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.pdf_geometry import annotate_geometry
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.pdf_render import MAX_RENDERED_PAGES, render_pages
from app.ingestion.vision_client import LocalPDFExtractor
from scripts.make_sample_data import write_text_pdf

_LINES = [
    "HDFC BANK LTD",
    "Statement of Account",
    "Opening Balance  : 100,000.00",
    "",
    "Date        Narration                                   Chq./Ref.No.       Withdrawal Amt.      Deposit Amt.   Closing Balance",
    "01/07/2026  UPI-RAJESH KUMAR TRADERS-INV2241            UPI2241                  12,450.00                          87,550.00",
    "02/07/2026  NEFT-GLOBEX SUPPLIES PVT LTD                NFT8340                                     8,340.50         95,890.50",
]


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "statement.pdf"
    write_text_pdf(_LINES, path)
    return path


def _parse(path: Path):
    return parse_pdf(
        path, source="bank", template=HDFCBankTemplate(),
        extractor=LocalPDFExtractor(), file_name=path.name,
    )


def test_every_row_gets_a_box_inside_the_page(statement: Path) -> None:
    """Tier 2 of source grounding: a citation that can only name a line number still
    leaves the reviewer to find it. A box lets the row be shown."""
    txns = _parse(statement)
    assert txns

    for txn in txns:
        box = txn.source_ref.box
        assert box is not None, txn.source_ref.label
        # Normalized to the page, so the box survives being rendered at any DPI.
        assert 0.0 <= box.x0 < box.x1 <= 1.0
        assert 0.0 <= box.y0 < box.y1 <= 1.0


def test_cell_box_contains_that_cell_and_nothing_else(statement: Path) -> None:
    """The box is only worth having if it is right. Cropping the PDF to each stored
    cell box and re-reading it must return that cell's own value — a box that merely
    exists would point a reviewer at the wrong figure with full confidence.
    """
    txns = _parse(statement)

    with pdfplumber.open(statement) as pdf:
        for txn in txns:
            page = pdf.pages[txn.source_ref.page - 1]
            width, height = float(page.width), float(page.height)
            for field, box in txn.source_ref.cell_boxes.items():
                if field == "description":
                    continue
                crop = page.crop(
                    (box.x0 * width - 1, box.y0 * height - 1,
                     box.x1 * width + 1, box.y1 * height + 1)
                )
                found = (crop.extract_text() or "").replace(" ", "")
                assert str(txn.raw_row[field]).replace(" ", "") in found, field


def test_row_box_encloses_its_own_cells(statement: Path) -> None:
    """A cell drawn outside its row's highlight would look like a rendering bug and,
    worse, make the reviewer doubt the pairing."""
    for txn in _parse(statement):
        row = txn.source_ref.box
        for field, cell in txn.source_ref.cell_boxes.items():
            assert row.x0 <= cell.x0 and cell.x1 <= row.x1, field
            assert row.y0 <= cell.y0 and cell.y1 <= row.y1, field


def test_unreadable_pdf_loses_the_boxes_but_never_the_citation(statement: Path) -> None:
    """Geometry is a convenience; the reconciliation is not. A failure reading it
    must leave the page-and-line citation standing rather than fail ingestion."""
    txns = _parse(statement)
    for txn in txns:
        txn.source_ref.box = None

    assert annotate_geometry(b"not a pdf at all", txns) == 0
    assert all(t.source_ref.box is None for t in txns)
    assert all(t.source_ref.line_start for t in txns), "line citations must survive"


def test_render_returns_only_the_pages_asked_for(statement: Path) -> None:
    """Pages nothing was flagged on are never rendered — a reviewer will not open
    them, and each one costs the response payload."""
    data = statement.read_bytes()

    images = render_pages(data, [1])
    assert set(images) == {1}
    assert images[1].startswith("data:image/png;base64,")

    assert render_pages(data, []) == {}
    assert render_pages(data, [99]) == {}


def test_render_is_capped(statement: Path) -> None:
    """A mis-dropped file could be hundreds of pages, and rendering lands in the HTTP
    response. Beyond the cap the citation keeps its page and line — only the picture
    is dropped."""
    images = render_pages(statement.read_bytes(), range(1, MAX_RENDERED_PAGES + 50))
    assert len(images) <= MAX_RENDERED_PAGES
