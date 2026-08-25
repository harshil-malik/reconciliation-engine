from __future__ import annotations

from app.ingestion.layout_table import parse_layout_table

# Column-aligned text as pypdf's layout mode produces it. Money columns are
# right-aligned under their headers, which is what makes debit vs credit a
# geometric fact rather than something a model has to infer.
_STATEMENT = """\
HDFC BANK
Statement of Account

Date               Narration                            Withdrawal (Dr)     Deposit (Cr)         Balance

01/07/2026         UPI-RAJESH KUMAR TRADERS-UPI                                 12,500.00     1,42,500.00
02/07/2026         NEFT-OFFICE RENT JULY                      40,000.00                       1,02,500.00
03/07/2026         SALARY CREDIT-EMPID4521                                                    1,77,500.00
05/07/2026         IMPS-SUNIL ENTERPRISES-REF7788                                3,210.00     1,80,710.00
                   ADDITIONAL NARRATION LINE
This is a computer-generated statement. Not an actual bank document.
"""


def test_deposits_and_withdrawals_are_assigned_by_column_position() -> None:
    rows = parse_layout_table(_STATEMENT)
    assert rows is not None

    assert rows[0]["credit"] == "12,500.00" and rows[0]["debit"] == "0"
    assert rows[1]["debit"] == "40,000.00" and rows[1]["credit"] == "0"
    assert rows[3]["credit"] == "3,210.00" and rows[3]["debit"] == "0"


def test_balance_is_never_mistaken_for_a_transaction_amount() -> None:
    """The failure this parser exists to prevent: the trailing running balance
    being reported as the amount."""
    rows = parse_layout_table(_STATEMENT)
    assert rows is not None

    assert rows[0]["balance"] == "1,42,500.00"
    for row in rows:
        assert row["debit"] != row["balance"]
        assert row["credit"] != row["balance"]


def test_row_with_a_balance_but_no_printed_amount_is_kept() -> None:
    """Its amount is recoverable from the balance movement, so the row must survive
    extraction rather than being dropped for having no figures."""
    rows = parse_layout_table(_STATEMENT)
    assert rows is not None

    salary = rows[2]
    assert salary["description"] == "SALARY CREDIT-EMPID4521"
    assert salary["debit"] == "0" and salary["credit"] == "0"
    assert salary["balance"] == "1,77,500.00"


def test_wrapped_narration_appends_and_footer_does_not() -> None:
    rows = parse_layout_table(_STATEMENT)
    assert rows is not None

    # indented continuation belongs to the row above
    assert rows[3]["description"] == "IMPS-SUNIL ENTERPRISES-REF7788 ADDITIONAL NARRATION LINE"
    # the left-margin footer is not narration and must not be appended anywhere
    assert not any("computer-generated" in row["description"] for row in rows)
    assert len(rows) == 4


def test_returns_none_when_there_is_no_aligned_table() -> None:
    """Signals the caller to fall back to the model rather than inventing rows."""
    assert parse_layout_table("Dear customer,\n\nYour statement is attached.\n") is None
