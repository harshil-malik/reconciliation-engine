from __future__ import annotations

import re
from typing import Optional

# Canonical column -> header keywords seen across Indian bank statement and ledger
# exports. Matched against whole header cells, so "Withdrawal (Dr)" matches "withdrawal".
_COLUMN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "value date"),
    "description": ("narration", "particulars", "description", "details", "remarks"),
    "reference": ("chq", "cheque", "ref", "reference", "voucher", "utr", "instrument"),
    "debit": ("withdrawal", "debit", "dr", "payments", "paid"),
    "credit": ("deposit", "credit", "cr", "receipts", "received"),
    "balance": ("balance", "closing balance", "running balance"),
}

_DATE_START = re.compile(r"^\s*(\d{1,2}[/-][A-Za-z0-9]{2,3}[/-]\d{2,4})")
_AMOUNT = re.compile(r"\d[\d,]*\.\d{2}|\d[\d,]*(?=\s|$)")

# A numeric value may sit slightly off its header's right edge depending on how the
# PDF was typeset, so tokens are matched to the nearest column within this many
# characters. Wide enough to absorb typesetting drift, narrow enough that the ~24
# character gap between adjacent money columns stays unambiguous.
_COLUMN_TOLERANCE = 12


class _Column:
    __slots__ = ("name", "start", "end")

    def __init__(self, name: str, start: int, end: int):
        self.name, self.start, self.end = name, start, end


def _classify(cell_text: str) -> Optional[str]:
    normalized = cell_text.strip().lower()
    if not normalized:
        return None
    for column, keywords in _COLUMN_KEYWORDS.items():
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", normalized):
                return column
    return None


def _header_columns(line: str) -> Optional[dict[str, _Column]]:
    """Map canonical column names to their character spans in a header line.

    Cells are split on runs of 2+ spaces, which is how layout-mode extraction
    separates table columns.
    """
    columns: dict[str, _Column] = {}
    for match in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", line):
        name = _classify(match.group())
        # First occurrence wins: "Date" in a "Value Date" trailing column should not
        # displace the real leading date column.
        if name and name not in columns:
            columns[name] = _Column(name, match.start(), match.end())
    return columns or None


def _find_header(lines: list[str]) -> Optional[tuple[int, dict[str, _Column]]]:
    for index, line in enumerate(lines):
        columns = _header_columns(line)
        if not columns:
            continue
        # A real transaction table needs a date, something to describe the row, and
        # at least one money column. Without those it is a metadata block, not a table.
        if "date" in columns and ("debit" in columns or "credit" in columns):
            if "description" in columns or "balance" in columns:
                return index, columns
    return None


def _assign_amount(token_end: int, money_columns: list[_Column]) -> Optional[str]:
    """Assign a numeric token to a column by right-edge proximity.

    Money columns are right-aligned in these statements, so a value's right edge
    lands at its header's right edge — this is the geometric fact that makes the
    debit/credit distinction unambiguous without a model guessing at it.
    """
    best, best_distance = None, _COLUMN_TOLERANCE + 1
    for column in money_columns:
        distance = abs(token_end - column.end)
        if distance < best_distance:
            best, best_distance = column.name, distance
    return best


def parse_layout_table(text: str) -> Optional[list[dict]]:
    """Parse an aligned transaction table out of layout-extracted PDF text.

    Returns row dicts in the same shape the model is asked to produce, or None when
    no aligned table is found — in which case the caller should fall back to the
    model. Preferred over the model whenever it succeeds: column position is a hard
    geometric fact here, and reading it in code is exact, instant and free, whereas
    a small model guesses and has been observed reporting a running balance as a
    transaction amount.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    found = _find_header(lines)
    if found is None:
        return None
    header_index, columns = found

    money_columns = [columns[name] for name in ("debit", "credit", "balance") if name in columns]
    if not money_columns:
        return None
    first_money_start = min(column.start for column in money_columns)

    rows: list[dict] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            continue

        date_match = _DATE_START.match(line)
        if not date_match:
            # A line with no leading date is a wrapped narration belonging to the row
            # above — never its own transaction. It must be indented into the
            # description column to qualify: page footers and disclaimers start at
            # the left margin, and appending those to the last transaction corrupts
            # its description.
            indent = len(line) - len(line.lstrip())
            description_start = columns["description"].start if "description" in columns else 0
            if (
                rows
                and not _AMOUNT.search(line)
                and indent >= description_start - _COLUMN_TOLERANCE
            ):
                extra = line[:first_money_start].strip()
                if extra:
                    rows[-1]["description"] = f"{rows[-1]['description']} {extra}".strip()
            continue

        values: dict[str, str] = {}
        for match in _AMOUNT.finditer(line):
            if match.start() < first_money_start - _COLUMN_TOLERANCE:
                continue  # part of the narration (e.g. an invoice number), not money
            column_name = _assign_amount(match.end(), money_columns)
            if column_name and column_name not in values:
                values[column_name] = match.group()

        description_start = columns["description"].start if "description" in columns else date_match.end()
        description = line[description_start:first_money_start].strip()

        reference = None
        if "reference" in columns:
            reference_column = columns["reference"]
            reference = line[reference_column.start : reference_column.end].strip() or None

        rows.append(
            {
                "date": date_match.group(1),
                "description": description,
                "reference": reference,
                "debit": values.get("debit", "0"),
                "credit": values.get("credit", "0"),
                "balance": values.get("balance"),
            }
        )

    return rows or None
