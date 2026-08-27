from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from app.ingestion.normalize import normalize_dataframe


def test_normalizes_bank_statement_with_debit_credit_columns(bank_df: pd.DataFrame) -> None:
    transactions = normalize_dataframe(bank_df, source="bank", file_name="hdfc_apr.csv")

    assert len(transactions) == 2

    debit_txn, credit_txn = transactions
    assert debit_txn.date == date(2024, 4, 1)
    assert debit_txn.amount == Decimal("-15000.00")
    assert debit_txn.reference == "N123456789"
    assert debit_txn.source == "bank"
    assert debit_txn.file_name == "hdfc_apr.csv"
    assert debit_txn.raw_row["Narration"] == "NEFT-AXISBANK-VENDOR PAYMENT"

    assert credit_txn.amount == Decimal("25000.00")


def test_normalizes_ledger_with_single_signed_amount_column(ledger_df: pd.DataFrame) -> None:
    transactions = normalize_dataframe(ledger_df, source="ledger", file_name="ledger_apr.xlsx")

    assert len(transactions) == 2
    assert transactions[0].amount == Decimal("-15000")
    assert transactions[0].date == date(2024, 4, 1)
    assert transactions[1].amount == Decimal("25000")
    assert all(t.source == "ledger" for t in transactions)


def test_matching_bank_and_ledger_rows_have_equal_signed_amounts(
    bank_df: pd.DataFrame, ledger_df: pd.DataFrame
) -> None:
    bank_txns = normalize_dataframe(bank_df, source="bank", file_name="bank.csv")
    ledger_txns = normalize_dataframe(ledger_df, source="ledger", file_name="ledger.csv")

    bank_amounts = {t.amount for t in bank_txns}
    ledger_amounts = {t.amount for t in ledger_txns}
    assert bank_amounts == ledger_amounts


def test_blank_cells_treated_as_zero_amount() -> None:
    df = pd.DataFrame(
        [{"Date": "01/04/24", "Narration": "x", "Debit": "", "Credit": "500"}]
    )
    transactions = normalize_dataframe(df, source="bank", file_name="f.csv")
    assert transactions[0].amount == Decimal("500")


def test_raw_row_preserved_for_traceability(bank_df: pd.DataFrame) -> None:
    transactions = normalize_dataframe(bank_df, source="bank", file_name="f.csv")
    assert transactions[0].raw_row == {
        "Date": "01/04/24",
        "Narration": "NEFT-AXISBANK-VENDOR PAYMENT",
        "Chq/Ref No": "N123456789",
        "Withdrawal Amt": "15000.00",
        "Deposit Amt": "",
    }


def test_debit_is_inflow_mirrors_the_signs_of_a_split_debit_credit_sheet() -> None:
    """A CSV/Excel ledger was always read as "Credit means money in".

    That is right for a party or expense ledger and backwards for the client's own
    Bank A/c ledger, where a Debit to the bank asset account is money arriving — the
    usual counterpart to a bank statement. Read the wrong way every amount in the
    file is inverted and nothing errors; the reconciliation simply collapses.
    """
    df = pd.DataFrame(
        [
            {"Date": "01/04/24", "Particulars": "Receipt from customer",
             "Debit": "25000", "Credit": ""},
            {"Date": "02/04/24", "Particulars": "Payment to supplier",
             "Debit": "", "Credit": "15075"},
        ]
    )

    party = normalize_dataframe(df, source="ledger", file_name="l.csv")
    assert [t.amount for t in party] == [Decimal("-25000"), Decimal("15075")]

    bank_account = normalize_dataframe(
        df, source="ledger", file_name="l.csv", debit_is_inflow=True
    )
    assert [t.amount for t in bank_account] == [Decimal("25000"), Decimal("-15075")]


def test_debit_is_inflow_does_nothing_to_a_single_signed_amount_column(
    ledger_df: pd.DataFrame,
) -> None:
    """A sheet that already carries one signed Amount column states its own
    direction, so there is no convention left to choose. Callers rely on this: it is
    what lets convention detection recognise an unambiguous file instead of
    reporting the two identical readings as a tie."""
    default = normalize_dataframe(ledger_df, source="ledger", file_name="l.csv")
    flipped = normalize_dataframe(
        ledger_df, source="ledger", file_name="l.csv", debit_is_inflow=True
    )
    assert [t.amount for t in default] == [t.amount for t in flipped]


def test_source_ref_numbers_rows_the_way_the_spreadsheet_does(bank_df: pd.DataFrame) -> None:
    """A citation two rows off is worse than no citation.

    The reviewer opens the file and goes to the row the report named; the header
    occupies row 1, so the first data row is row 2. Citing the DataFrame's own
    0-based index would send them somewhere else entirely.
    """
    transactions = normalize_dataframe(bank_df, source="bank", file_name="hdfc.csv")

    assert [t.source_ref.line_start for t in transactions] == [2, 3]
    assert [t.source_ref.label for t in transactions] == ["row 2", "row 3"]
    assert all(t.source_ref.kind == "sheet_row" for t in transactions)
    # The cited text must carry the row's own figures, not a summary of them.
    assert "15000.00" in transactions[0].source_ref.text
    assert "NEFT-AXISBANK-VENDOR PAYMENT" in transactions[0].source_ref.text
