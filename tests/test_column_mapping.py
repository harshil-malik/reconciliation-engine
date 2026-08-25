from __future__ import annotations

import pandas as pd
import pytest

from app.ingestion.column_mapping import detect_columns


def test_detects_split_debit_credit_columns(bank_df: pd.DataFrame) -> None:
    mapping = detect_columns(bank_df)

    assert mapping["date"] == "Date"
    assert mapping["description"] == "Narration"
    assert mapping["reference"] == "Chq/Ref No"
    assert mapping["debit_credit"] == ("Withdrawal Amt", "Deposit Amt")
    assert "amount" not in mapping


def test_detects_single_signed_amount_column(ledger_df: pd.DataFrame) -> None:
    mapping = detect_columns(ledger_df)

    assert mapping["date"] == "Txn Date"
    assert mapping["description"] == "Particulars"
    assert mapping["reference"] == "Voucher No"
    assert mapping["amount"] == "Amount"
    assert "debit_credit" not in mapping


def test_raises_when_no_date_column_found() -> None:
    df = pd.DataFrame([{"Description": "x", "Amount": "100"}])

    with pytest.raises(ValueError, match="date column"):
        detect_columns(df)


def test_raises_when_no_amount_or_debit_credit_found() -> None:
    df = pd.DataFrame([{"Date": "01/04/24", "Narration": "x"}])

    with pytest.raises(ValueError, match="amount column"):
        detect_columns(df)
