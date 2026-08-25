from __future__ import annotations

import pandas as pd

# Candidate header names (normalized: lowercased, punctuation/whitespace-collapsed)
# seen across CA ledgers and the top Indian bank statement export formats.
_DATE_ALIASES = {"date", "txn date", "transaction date", "value date", "posting date"}
_DESCRIPTION_ALIASES = {
    "description",
    "narration",
    "particulars",
    "details",
    "transaction details",
    "remarks",
}
_REFERENCE_ALIASES = {
    "reference",
    "reference no",
    "ref no",
    "cheque no",
    "chq no",
    "chq/ref no",
    "utr",
    "voucher no",
    "cheque/reference no",
}
_AMOUNT_ALIASES = {"amount", "txn amount", "transaction amount"}
_DEBIT_ALIASES = {"debit", "withdrawal", "withdrawal amt", "dr"}
_CREDIT_ALIASES = {"credit", "deposit", "deposit amt", "cr"}


def _normalize_header(header: object) -> str:
    return " ".join(str(header).strip().lower().replace(".", "").split())


def detect_columns(df: pd.DataFrame) -> dict[str, object]:
    """Map canonical field names to actual DataFrame column names.

    Returns a dict with keys among: date, description, reference, amount,
    debit_credit (a (debit_col, credit_col) tuple, when the sheet splits money into
    two columns instead of one signed amount column).

    Raises ValueError when a required field (date, description, and amount or
    debit/credit) can't be found — the message lists the actual headers seen, since
    that's what a caller needs to fix a template or add a new alias.
    """
    normalized = {_normalize_header(c): c for c in df.columns}

    def find(aliases: set[str]) -> str | None:
        for alias in aliases:
            if alias in normalized:
                return normalized[alias]
        return None

    mapping: dict[str, object] = {}

    date_col = find(_DATE_ALIASES)
    if date_col is None:
        raise ValueError(f"Could not detect a date column among: {list(df.columns)}")
    mapping["date"] = date_col

    desc_col = find(_DESCRIPTION_ALIASES)
    if desc_col is None:
        raise ValueError(f"Could not detect a description column among: {list(df.columns)}")
    mapping["description"] = desc_col

    ref_col = find(_REFERENCE_ALIASES)
    if ref_col is not None:
        mapping["reference"] = ref_col

    amount_col = find(_AMOUNT_ALIASES)
    debit_col = find(_DEBIT_ALIASES)
    credit_col = find(_CREDIT_ALIASES)

    if amount_col is not None:
        mapping["amount"] = amount_col
    elif debit_col is not None and credit_col is not None:
        mapping["debit_credit"] = (debit_col, credit_col)
    else:
        raise ValueError(
            "Could not detect an amount column, or a debit+credit pair, among: "
            f"{list(df.columns)}"
        )

    return mapping
