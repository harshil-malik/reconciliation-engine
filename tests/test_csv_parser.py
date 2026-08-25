from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.ingestion.csv_parser import parse_csv


def test_parse_csv_round_trips_synthetic_bank_statement(
    tmp_path: Path, bank_df: pd.DataFrame
) -> None:
    csv_path = tmp_path / "hdfc_statement.csv"
    bank_df.to_csv(csv_path, index=False)

    transactions = parse_csv(csv_path, source="bank")

    assert len(transactions) == 2
    assert transactions[0].amount == Decimal("-15000.00")
    assert transactions[0].source == "bank"
    assert transactions[0].file_name == "hdfc_statement.csv"


def test_parse_csv_respects_explicit_file_name_override(
    tmp_path: Path, ledger_df: pd.DataFrame
) -> None:
    csv_path = tmp_path / "tmp_upload_123.csv"
    ledger_df.to_csv(csv_path, index=False)

    transactions = parse_csv(csv_path, source="ledger", file_name="internal_ledger.csv")

    assert transactions[0].file_name == "internal_ledger.csv"
