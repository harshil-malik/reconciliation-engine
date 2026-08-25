from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.ingestion.excel_parser import parse_excel


def test_parse_excel_round_trips_synthetic_ledger(tmp_path: Path, ledger_df: pd.DataFrame) -> None:
    xlsx_path = tmp_path / "ledger.xlsx"
    ledger_df.to_excel(xlsx_path, index=False)

    transactions = parse_excel(xlsx_path, source="ledger")

    assert len(transactions) == 2
    assert transactions[0].amount == Decimal("-15000")
    assert transactions[1].amount == Decimal("25000")
    assert transactions[0].source == "ledger"
    assert transactions[0].file_name == "ledger.xlsx"
