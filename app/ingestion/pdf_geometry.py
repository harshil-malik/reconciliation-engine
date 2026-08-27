from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable, Optional

from app.schema import Box, Transaction

logger = logging.getLogger(__name__)

# How far above or below a row's anchor a word may sit and still belong to it.
# Not zero, and not a guess: measured on a real HDFC statement, a transaction's
# narration cell is set on a different baseline from the figures on the same logical
# row — the figures line and its narration sit 3-6pt apart while the next row starts
# 15pt later. Matching whole printed lines instead misses the narration entirely and
# boxes a third of the row.
_ROW_BAND_PT = 9.0

# Which raw_row keys become highlightable cells.
_CELL_FIELDS = ("date", "reference", "debit", "credit", "balance")


def _squash(text: str) -> str:
    """Whitespace-insensitive form for comparing text across two extractors.

    The layout extractor pads columns apart to preserve their geometry; the word
    extractor reports the same characters with no padding. Only the characters
    themselves are common to both, so they are what we compare.
    """
    return re.sub(r"\s+", "", text)


def _union(boxes: Iterable[Box]) -> Optional[Box]:
    boxes = list(boxes)
    if not boxes:
        return None
    return Box(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def _page_words(words: list[dict], width: float, height: float) -> list[dict]:
    """Flatten a page to words carrying both their printed position and a page-relative
    box. Coordinates are normalized to fractions of the page so a box can be overlaid
    on a rendering at any resolution; storing points would tie each box to one DPI.
    """
    return [
        {
            "text": word["text"],
            "key": _squash(word["text"]),
            "top": float(word["top"]),
            "x0": float(word["x0"]),
            "box": Box(
                x0=word["x0"] / width,
                y0=word["top"] / height,
                x1=word["x1"] / width,
                y1=word["bottom"] / height,
            ),
        }
        for word in words
    ]


def _locate(words: list[dict], line: str) -> list[dict]:
    """Find the printed words that produced one line of layout-extracted text.

    Works by token rather than by whole line, because a logical row is not always one
    printed line: the two extractors agree on the characters but not on how they are
    grouped. Each distinct baseline is scored by how many of the line's tokens appear
    within a band around it, and the best-scoring band wins — so a row split into a
    figures line and a narration line is recovered whole, and a token that also
    appears elsewhere on the page cannot drag the match away from the row where most
    of the line actually sits.
    """
    tokens = [_squash(t) for t in line.split() if _squash(t)]
    if not tokens:
        return []

    by_key: dict[str, list[dict]] = defaultdict(list)
    for word in words:
        by_key[word["key"]].append(word)

    candidates = [w for token in tokens for w in by_key.get(token, [])]
    if not candidates:
        return []

    best_score, best_words = 0, []
    for anchor in sorted({round(w["top"], 1) for w in candidates}):
        in_band = [w for w in candidates if abs(w["top"] - anchor) <= _ROW_BAND_PT]
        # Score by distinct tokens covered, not by word count: a row repeating one
        # token would otherwise outrank a row genuinely carrying more of the line.
        score = len({w["key"] for w in in_band})
        if score > best_score:
            best_score, best_words = score, in_band

    return sorted(best_words, key=lambda w: w["x0"])


def _cell_boxes(row_words: list[dict], raw_row: dict[str, Any]) -> dict[str, Box]:
    """Box each cell by finding the words that spell its value.

    Matching on the value rather than on a column's x-range needs no per-bank column
    geometry and cannot drift out of step with the table parser: if the parser read
    "12,450.00" out of this row, the box is wherever those characters are printed.
    Words are consumed as claimed, and the leftmost match wins — a statement prints
    the transaction date and the value date in the same format, and the transaction
    date is the one to its left.
    """
    claimed: set[int] = set()
    boxes: dict[str, Box] = {}

    for field in _CELL_FIELDS:
        value = raw_row.get(field)
        if value in (None, "", "0"):
            continue
        target = _squash(str(value))
        if not target:
            continue
        for index, word in enumerate(row_words):
            if index not in claimed and word["key"] == target:
                claimed.add(index)
                boxes[field] = word["box"]
                break

    # The narration is many words rather than one, so it is bounded rather than
    # matched: whatever is left, to the right of the date and left of the figures.
    remaining = [w for i, w in enumerate(row_words) if i not in claimed]
    date_box = boxes.get("date")
    money_x0 = min(
        (boxes[f].x0 for f in ("debit", "credit", "balance") if f in boxes), default=None
    )
    described = [
        w for w in remaining
        if (date_box is None or w["box"].x0 >= date_box.x1)
        and (money_x0 is None or w["box"].x1 <= money_x0)
    ]
    if described:
        boxes["description"] = _union(w["box"] for w in described)

    return boxes


def annotate_geometry(pdf_bytes: bytes, transactions: list[Transaction]) -> int:
    """Attach page-relative boxes to every transaction read from a PDF's text layer.

    Best effort by design. A missing highlight costs a reviewer a visual convenience;
    a failure here taking down ingestion would cost them the reconciliation. Anything
    that goes wrong is logged, leaves the boxes unset, and the citation still names
    the file, the page and the line.
    """
    wanted = [
        t for t in transactions
        if t.source_ref and t.source_ref.kind == "pdf_line" and t.source_ref.page
    ]
    if not wanted:
        return 0

    try:
        import io

        import pdfplumber
    except ImportError:  # noqa: BLE001 - geometry is optional, citations are not
        logger.info("pdfplumber not installed; source citations will have no boxes")
        return 0

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages, sizes = {}, {}
            for number, page in enumerate(pdf.pages, start=1):
                width, height = float(page.width), float(page.height)
                pages[number] = _page_words(page.extract_words(), width, height)
                sizes[number] = (width, height)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read PDF geometry; citations keep their line numbers")
        return 0

    located = 0
    for txn in wanted:
        ref = txn.source_ref
        words = pages.get(ref.page or 0)
        if not words:
            continue

        lines = [_locate(words, line) for line in ref.text.split("\n")]
        found = [w for line_words in lines for w in line_words]
        if not found:
            continue

        ref.box = _union(w["box"] for w in found)
        # Cells are boxed on the line carrying the figures — the first — since a
        # wrapped continuation holds narration only.
        ref.cell_boxes = _cell_boxes(lines[0] or found, txn.raw_row)
        ref.page_size = sizes.get(ref.page or 0)
        located += 1

    logger.info(
        "Located %d of %d PDF row(s) geometrically for source highlighting",
        located, len(wanted),
    )
    return located
