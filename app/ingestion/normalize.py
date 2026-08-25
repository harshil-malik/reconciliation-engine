from __future__ import annotations

from typing import Literal

import pandas as pd

from app.ingestion.amounts import to_decimal
from app.ingestion.column_mapping import detect_columns
from app.schema import Transaction


def _clean_cell(value: object) -> object:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def normalize_dataframe(
    df: pd.DataFrame, *, source: Literal["bank", "ledger"], file_name: str
) -> list[Transaction]:
    """Turn a raw bank/ledger DataFrame into canonical Transaction rows.

    Column names are auto-detected via `detect_columns` — statements from different
    banks/ledgers use different headers (Narration vs Particulars, single Amount vs
    split Debit/Credit, etc.), and this is what absorbs that variance.
    """
    mapping = detect_columns(df)
    transactions: list[Transaction] = []

    for _, row in df.iterrows():
        raw_row = {k: _clean_cell(v) for k, v in row.items()}

        parsed_date = pd.to_datetime(
            row[mapping["date"]], dayfirst=True, errors="raise"
        ).date()

        if "amount" in mapping:
            amount = to_decimal(row[mapping["amount"]])
        else:
            debit_col, credit_col = mapping["debit_credit"]
            amount = to_decimal(row[credit_col]) - to_decimal(row[debit_col])

        reference = None
        if "reference" in mapping:
            ref_value = row[mapping["reference"]]
            cleaned = _clean_cell(ref_value)
            reference = None if cleaned is None else str(cleaned).strip()

        transactions.append(
            Transaction(
                date=parsed_date,
                amount=amount,
                description=str(row[mapping["description"]]).strip(),
                reference=reference,
                source=source,
                file_name=file_name,
                raw_row=raw_row,
            )
        )

    return transactions
