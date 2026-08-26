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


_WITH_REFERENCE = """\
Date        Narration                        Chq./Ref.No.     Withdrawal Amt.   Deposit Amt.        Balance

01/07/2026  NEFT-VENDOR PAYMENT ACME LTD     NFT1520                15,320.00                    1,14,680.00
This is a synthetic statement generated for software testing.
"""


def test_reference_is_not_appended_to_the_description() -> None:
    """A voucher number left in the narration reads wrong in the report and degrades
    both the Stage 1 fuzzy tiebreak and the Stage 2 embeddings."""
    rows = parse_layout_table(_WITH_REFERENCE)
    assert rows is not None

    assert rows[0]["description"] == "NEFT-VENDOR PAYMENT ACME LTD"
    assert rows[0]["reference"] == "NFT1520"


def test_left_margin_footer_is_not_glued_to_the_last_transaction() -> None:
    rows = parse_layout_table(_WITH_REFERENCE)
    assert rows is not None

    assert len(rows) == 1
    assert "synthetic" not in rows[0]["description"]


def test_returns_none_when_there_is_no_aligned_table() -> None:
    """Signals the caller to fall back to the model rather than inventing rows."""
    assert parse_layout_table("Dear customer,\n\nYour statement is attached.\n") is None


_WIDE_VALUE = """\
    Date          Particulars                                     Debit          Credit

    01-Jul-2026   Rajesh Kumar Traders - Purchase Invoice #INV-2241   12,500.00
"""


def test_amount_wider_than_its_header_does_not_bleed_into_the_description() -> None:
    """Money values are right-aligned, so a value wider than its own column heading
    starts to the LEFT of it. Slicing the description at the header position leaves
    the value's leading digits stuck on the narration."""
    rows = parse_layout_table(_WIDE_VALUE)
    assert rows is not None

    assert rows[0]["description"] == "Rajesh Kumar Traders - Purchase Invoice #INV-2241"
    assert rows[0]["debit"] == "12,500.00"


# Layouts below are anonymized reproductions of a real HDFC statement and a real
# Tally-style bank ledger. Each encodes a quirk that broke extraction on first
# contact with the genuine files.

_REAL_STATEMENT = """\
  Date       Narration                              Chq./Ref.No.           Value Dt   Withdrawal Amt.         Deposit Amt.     Closing Balance

01/07/26     OPENING BALANCE                                               01/07/26                                                   245,000.00
02/07/26     UPI/P2M/900000000001/SWIFTWAY          0000000000             02/07/26            1,180.00                               243,820.00
             EXPRESS/Courier chg
03/07/26     NEFT CR:HDFC0R00099920250703/NORTH     N032025070399999       03/07/26                               84,500.00           328,320.00
             SUPPLY PVT LTD/INV-1042
"""


def test_opening_balance_row_is_not_a_transaction() -> None:
    """It is dated and sits in the table like a transaction, but moved no money.
    Emitting it adds a zero-amount row; capturing its balance instead is what lets
    the first real transaction be audited."""
    rows = parse_layout_table(_REAL_STATEMENT)
    assert rows is not None

    assert len(rows) == 2
    assert not any("OPENING BALANCE" in r["description"] for r in rows)
    assert rows[0]["opening_balance"] == "245,000.00"


def test_all_zero_reference_is_treated_as_absent() -> None:
    """`0000000000` is how the statement prints "no reference". Kept as a value it
    reads as an exact reference match between unrelated rows — the strongest
    matching signal there is."""
    rows = parse_layout_table(_REAL_STATEMENT)
    assert rows is not None

    assert rows[0]["reference"] is None


def test_reference_longer_than_its_heading_is_not_truncated() -> None:
    """"Chq./Ref.No." is 12 characters; the UTRs beneath it are 16."""
    rows = parse_layout_table(_REAL_STATEMENT)
    assert rows is not None

    assert rows[1]["reference"] == "N032025070399999"


def test_value_date_column_is_not_read_as_a_reference_or_an_amount() -> None:
    rows = parse_layout_table(_REAL_STATEMENT)
    assert rows is not None

    assert rows[1]["credit"] == "84,500.00"
    assert rows[1]["balance"] == "328,320.00"
    assert "07/26" not in (rows[1]["reference"] or "")


_LEDGER_WITH_VOUCHER_FIRST = """\
  Date         Voucher No.      Particulars                                  Ref./UTR No.        Debit (Receipt)   Credit (Payment)      Balance

02-Jul-26        PV-0451        Swiftway Express - Courier charges           900000000001                                   1,180.00    243,820.00
03-Jul-26        RV-0212        North Supply Pvt Ltd - INV-1042 realised     HDFC0R0009992025        84,500.00                          328,320.00
"""


def test_voucher_column_before_particulars_does_not_empty_the_description() -> None:
    """A ledger can carry an internal voucher number BEFORE the narration. Treating
    it as the reference column made the description slice run backwards, emptying
    every narration in the file."""
    rows = parse_layout_table(_LEDGER_WITH_VOUCHER_FIRST)
    assert rows is not None

    assert rows[0]["description"] == "Swiftway Express - Courier charges"
    assert rows[1]["description"] == "North Supply Pvt Ltd - INV-1042 realised"


def test_rightmost_reference_column_wins_over_an_internal_voucher_number() -> None:
    """The voucher number is internal to the ledger; the UTR is what appears on the
    bank side, so it is the one worth matching on."""
    rows = parse_layout_table(_LEDGER_WITH_VOUCHER_FIRST)
    assert rows is not None

    assert rows[0]["reference"] == "900000000001"
    assert rows[1]["reference"] == "HDFC0R0009992025"
