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
# "Opening Balance : 1,30,000.00" / "Balance B/F 1,30,000.00" and similar.
_OPENING_BALANCE = re.compile(
    r"(?:opening\s+balance|balance\s+b/?f|brought\s+forward)\D{0,12}([\d,]+\.\d{2})",
    re.IGNORECASE,
)

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

    # The first transaction has no preceding row, so its amount cannot be checked
    # against a balance movement — unless the statement prints an opening balance,
    # which supplies the missing starting point. Worth hunting for: it is the
    # difference between the first row being audited and being taken on trust.
    opening_balance = None
    for line in lines[:header_index]:
        opening_match = _OPENING_BALANCE.search(line)
        if opening_match:
            opening_balance = opening_match.group(1)
            break

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
            # Must line up with the description column, give or take a couple of
            # characters. A generous tolerance here lets left-margin footers
            # ("This is a synthetic statement...") qualify as narration and get
            # glued onto the final transaction's description.
            if (
                rows
                and not _AMOUNT.search(line)
                and description_start > 0
                and indent >= description_start - 2
            ):
                extra = line[:first_money_start].strip()
                if extra:
                    rows[-1]["description"] = f"{rows[-1]['description']} {extra}".strip()
            continue

        # Candidate money tokens: a real table cell is whitespace-delimited, so a run
        # of digits welded onto other text ("#INV-2241") is part of the narration
        # however close to a money column it happens to fall.
        candidates = [
            m
            for m in _AMOUNT.finditer(line)
            if m.start() >= first_money_start - _COLUMN_TOLERANCE
            and (m.start() == 0 or line[m.start() - 1].isspace())
        ]

        # Assign per column rather than per token, taking whichever candidate aligns
        # best with that column's right edge. Iterating tokens and letting the first
        # one claim a column lets a stray value seize the slot the real figure wants.
        values: dict[str, str] = {}
        claimed: set[int] = set()
        for column in money_columns:
            best, best_distance = None, _COLUMN_TOLERANCE + 1
            for index, m in enumerate(candidates):
                if index in claimed:
                    continue
                distance = abs(m.end() - column.end)
                if distance < best_distance:
                    best, best_distance = index, distance
            if best is not None:
                claimed.add(best)
                values[column.name] = candidates[best].group()

        earliest_money_start = (
            min(candidates[i].start() for i in claimed) if claimed else None
        )

        description_start = (
            columns["description"].start if "description" in columns else date_match.end()
        )
        # Stop at the reference column when there is one, otherwise the voucher /
        # cheque number gets appended to the narration — which both looks wrong in
        # the report and degrades the fuzzy description matching in Stage 1 and the
        # embeddings in Stage 2.
        # Where the narration ends: at the reference column when the table has one,
        # otherwise immediately before the first figure actually assigned to a money
        # column. Using the money column's HEADER position instead would cut through
        # a long narration, because a value wider than its heading starts to the left
        # of it — and would leave that value's leading digits on the narration when
        # the narration is short ("...#INV-2241  1250").
        if "reference" in columns:
            description_end = columns["reference"].start
        else:
            description_end = (
                earliest_money_start if earliest_money_start is not None else first_money_start
            )
        description = line[description_start:description_end].strip()

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

    if rows and opening_balance is not None:
        rows[0]["opening_balance"] = opening_balance

    return rows or None
