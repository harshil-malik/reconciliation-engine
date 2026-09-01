from __future__ import annotations

from typing import Literal

import pandas as pd

from app.ingestion.amounts import to_decimal
from app.ingestion.column_mapping import detect_columns
from app.schema import SourceCell, SourceRef, Transaction


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

    # Which canonical role each source column was given, so a citation can point at
    # the column that actually carries the money rather than making the reader guess.
    roles: dict[object, str] = {}
    for field in ("date", "description", "reference", "amount"):
        if field in mapping:
            roles[mapping[field]] = field
    if "debit_credit" in mapping:
        debit_column, credit_column = mapping["debit_credit"]
        roles[debit_column] = "debit"
        roles[credit_column] = "credit"

    transactions: list[Transaction] = []

    # Numbered the way the spreadsheet numbers them: the header occupies row 1, so
    # the first data row is row 2. Citing the DataFrame's own 0-based index would
    # send a reviewer two rows off, which is worse than citing nothing.
    for offset, (_, row) in enumerate(df.iterrows()):
        sheet_row = offset + 2
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
                source_ref=SourceRef(
                    kind="sheet_row",
                    line_start=sheet_row,
                    line_end=sheet_row,
                    # The cells as printed, in column order — the row the reviewer
                    # will see if they open the file and go to that line.
                    text=" | ".join(
                        f"{k}: {v}" for k, v in raw_row.items() if v not in (None, "")
                    ),
                    cells=[
                        SourceCell(
                            column=str(column),
                            value="" if value is None else str(value),
                            field=roles.get(column),
                        )
                        for column, value in raw_row.items()
                    ],
                ),
                raw_row=raw_row,
            )
        )

    return transactions
