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
        if not name:
            continue
        if name == "reference":
            # Rightmost wins for references. A ledger can carry both an internal
            # "Voucher No." and the "Ref./UTR No." that actually appears on the bank
            # side; the matching one sits nearest the money columns, and taking the
            # first would grab the voucher number — useless for reconciliation.
            columns[name] = _Column(name, match.start(), match.end())
        elif name not in columns:
            # First occurrence wins otherwise: a trailing "Value Dt" must not
            # displace the leading "Date" column.
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


_TOTAL_PATTERNS = {
    # \D cannot cross a digit, so a generous gap is safe: summary blocks pad the
    # label out to the amount column with 30+ spaces.
    "opening": re.compile(r"opening\s+balance\D{0,80}?([\d,]+\.\d{2})", re.I),
    "closing": re.compile(r"closing\s+balance\D{0,80}?([\d,]+\.\d{2})", re.I),
    "withdrawals": re.compile(r"total\s+(?:withdrawals?|debits?|payments?)\D{0,80}?([\d,]+\.\d{2})", re.I),
    "deposits": re.compile(r"total\s+(?:deposits?|credits?|receipts?)\D{0,80}?([\d,]+\.\d{2})", re.I),
}


def _printed_totals(lines: list[str]) -> dict[str, str]:
    """The summary figures a statement prints about itself.

    Deliberately takes the LAST occurrence of each: "Opening Balance" appears both
    in the header block and again in the closing summary, and the summary is the
    authoritative one.
    """
    totals: dict[str, str] = {}
    for line in lines:
        for name, pattern in _TOTAL_PATTERNS.items():
            match = pattern.search(line)
            if match:
                totals[name] = match.group(1)
    return totals


def _reference_at(
    line: str, columns: dict[str, _Column], first_money_start: int
) -> Optional[str]:
    """Read the cheque/reference cell, whole and without swallowing its neighbour.

    Two traps, both seen on a real HDFC statement. Slicing at the header's own width
    truncates the value — the "Chq./Ref.No." heading is 12 characters but the UTRs
    beneath it are 16, so references came out cut short. Widening to the next
    detected column instead swallows whatever sits between, such as an undetected
    "Value Dt" column. Taking the first whitespace-delimited token from the cell's
    start avoids both.

    A reference of all zeros is the bank's way of printing "none" — 11 rows carried
    `0000000000` on that statement. Kept as a value it would read as an exact
    reference match between unrelated transactions, which is the strongest matching
    signal there is, so it is treated as absent.
    """
    if "reference" not in columns:
        return None

    cell = line[columns["reference"].start : first_money_start]
    token = cell.split(maxsplit=1)[0] if cell.split() else ""
    if not token or set(token) <= {"0"}:
        return None
    return token


# Rows that carry a balance but are not transactions: the statement's own opening
# and closing markers. Emitting these as zero-amount transactions pollutes the
# reconciliation, and the opening one is more useful as the starting balance that
# lets the first real row be audited.
# Furniture that appears between transaction blocks on a multi-page statement. It is
# indented like a narration and carries no amount, so without this it qualifies as a
# continuation line and gets glued onto the last transaction's description — observed
# as "NEFT CR:.../NORTH Statement continued on next page".
_PAGE_FURNITURE = re.compile(
    r"\b(page\s+\d+\s*(of|/)\s*\d+"
    r"|continued\s+on\s+next\s+page|statement\s+continued|continued\.{0,3}$"
    r"|statement\s+of\s+account"
    r"|computer[\s-]?generated"
    r"|account\s+(no|number|branch|holder|type)\s*:"
    r"|ifsc|cust\s*id|statement\s+(from|period|to)\s*:)\b",
    re.I,
)

_OPENING_ROW = re.compile(r"\b(opening\s+balance|balance\s+b/?f|brought\s+forward)\b", re.I)
_CLOSING_ROW = re.compile(
    r"\b(closing\s+balance|balance\s+c/?f|carried\s+forward|total\s+(withdrawals?|deposits?))\b",
    re.I,
)


def _page_of(line_index: int, page_starts: Optional[list[int]]) -> Optional[int]:
    """Which 1-based page a line index falls on, given each page's first line."""
    if not page_starts:
        return None
    page = 1
    for number, start in enumerate(page_starts, start=1):
        if line_index >= start:
            page = number
        else:
            break
    return page


def parse_layout_table(
    text: str, page_starts: Optional[list[int]] = None
) -> Optional[list[dict]]:
    """Parse an aligned transaction table out of layout-extracted PDF text.

    Each row also carries where it was read from — `source_page`, `source_line_start`,
    `source_line_end` (1-based, and a range when a narration wraps) and `source_text`,
    the line(s) verbatim. That provenance is what lets a reviewer click a flagged row
    and land on the line in the statement that produced it, instead of taking the
    figure on trust. `page_starts` gives the first line index of each page, since the
    caller flattens the document to one string before parsing.

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

    # Statements print their own totals in a summary block. Captured here so
    # extraction can be footed against them: the per-row balance audit proves each
    # amount is right, but only totals prove no ROW was dropped.
    printed_totals = _printed_totals(lines)
    if opening_balance is None:
        opening_balance = printed_totals.get("opening")

    money_columns = [columns[name] for name in ("debit", "credit", "balance") if name in columns]
    if not money_columns:
        return None
    first_money_start = min(column.start for column in money_columns)

    rows: list[dict] = []
    for line_index, line in enumerate(lines[header_index + 1 :], start=header_index + 1):
        if not line.strip():
            continue

        date_match = _DATE_START.match(line)
        if not date_match:
            # A line with no leading date is a wrapped narration belonging to the row
            # above — never its own transaction. It must be indented into the
            # description column to qualify: page footers and disclaimers start at
            # the left margin, and appending those to the last transaction corrupts
            # its description.
            # A repeated column header, or page furniture between blocks, is not
            # narration. On a multi-page statement both sit exactly where a wrapped
            # narration would.
            if _PAGE_FURNITURE.search(line) or _header_columns(line) == columns:
                continue

            indent = len(line) - len(line.lstrip())
            description_start = columns["description"].start if "description" in columns else 0
            # A continuation must carry no figure in the MONEY columns. Testing for
            # a digit anywhere instead would discard the invoice and order numbers
            # that wrapped narrations routinely carry ("SUPPLY PVT LTD/INV-1042") —
            # precisely the text worth keeping, since it is what ties the row to a
            # ledger entry.
            carries_money = any(
                m.start() >= first_money_start - _COLUMN_TOLERANCE
                and (m.start() == 0 or line[m.start() - 1].isspace())
                for m in _AMOUNT.finditer(line)
            )
            # Must line up with the description column, give or take a couple of
            # characters. A generous tolerance here lets left-margin footers
            # ("This is a synthetic statement...") qualify as narration and get
            # glued onto the final transaction's description.
            if (
                rows
                and not carries_money
                and description_start > 0
                and indent >= description_start - 2
            ):
                extra = line[:first_money_start].strip()
                if extra:
                    rows[-1]["description"] = f"{rows[-1]['description']} {extra}".strip()
                    # The citation must cover every line the description was built
                    # from, otherwise it points at a partial narration and the
                    # reviewer sees less than the engine read.
                    rows[-1]["source_line_end"] = line_index + 1
                    rows[-1]["source_text"] = f"{rows[-1]['source_text']}\n{line}"
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
        # The narration runs until the next column to its RIGHT — not simply until
        # the reference column, which on some ledgers ("Voucher No." before
        # "Particulars") sits to the LEFT and would make the slice run backwards,
        # emptying every description.
        boundaries = [
            column.start
            for name, column in columns.items()
            # Money columns are bounded by where their VALUE starts, below — a value
            # wider than its heading begins to the left of it, so using the heading
            # position here would cut the narration short.
            if name not in ("debit", "credit", "balance") and column.start > description_start
        ]
        boundaries.append(
            earliest_money_start if earliest_money_start is not None else first_money_start
        )
        description = line[description_start : min(boundaries)].strip()

        reference = _reference_at(line, columns, first_money_start)

        has_amount = "debit" in values or "credit" in values
        # An opening-balance line is dated and sits in the table like a transaction,
        # but it moved no money. Capture its balance as the starting point — that is
        # what lets the first real row be checked arithmetically — and drop the row.
        if not has_amount and _OPENING_ROW.search(description):
            if opening_balance is None:
                opening_balance = values.get("balance")
            continue
        if not has_amount and _CLOSING_ROW.search(description):
            continue

        rows.append(
            {
                "date": date_match.group(1),
                "description": description,
                "reference": reference,
                "debit": values.get("debit", "0"),
                "credit": values.get("credit", "0"),
                "balance": values.get("balance"),
                "source_page": _page_of(line_index, page_starts),
                "source_line_start": line_index + 1,
                "source_line_end": line_index + 1,
                "source_text": line,
            }
        )

    if rows:
        if opening_balance is not None:
            rows[0]["opening_balance"] = opening_balance
        if printed_totals:
            rows[0]["printed_totals"] = printed_totals

    return rows or None
