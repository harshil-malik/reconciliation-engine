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


_MULTIPAGE = """\
HDFC BANK LIMITED
      Account No             :  50100XXXXXX1234

  Date       Narration                        Chq./Ref.No.      Withdrawal Amt.     Deposit Amt.   Closing Balance

01/07/26     UPI/P2M/900000000001/SWIFTWAY    0000000000               1,180.00                        243,820.00
03/07/26     NEFT CR:HDFC0R0009992/NORTH      N032025070399999                         84,500.00       328,320.00
             SUPPLY PVT LTD/INV-1042

                                                          Page 1 of 2
                                        Statement continued on next page

HDFC BANK LIMITED
      Account No             :  50100XXXXXX1234

  Date       Narration                        Chq./Ref.No.      Withdrawal Amt.     Deposit Amt.   Closing Balance

05/07/26     CHQ PAID 000412                  000412                  22,000.00                        306,320.00

                                                          Page 2 of 2
"""


def test_repeated_headers_and_page_furniture_do_not_become_transactions() -> None:
    rows = parse_layout_table(_MULTIPAGE)
    assert rows is not None
    assert len(rows) == 3
    assert [r["date"] for r in rows] == ["01/07/26", "03/07/26", "05/07/26"]


def test_page_furniture_is_not_glued_onto_the_previous_narration() -> None:
    rows = parse_layout_table(_MULTIPAGE)
    assert rows is not None
    assert not any("continued" in r["description"].lower() for r in rows)
    assert not any("Page 1" in r["description"] for r in rows)


def test_a_wrapped_narration_containing_a_number_is_still_kept() -> None:
    """Invoice and order numbers routinely appear on the wrapped line, and they are
    exactly the text that ties a row to a ledger entry — a digit anywhere must not
    disqualify a continuation."""
    rows = parse_layout_table(_MULTIPAGE)
    assert rows is not None
    assert rows[1]["description"] == "NEFT CR:HDFC0R0009992/NORTH SUPPLY PVT LTD/INV-1042"


def test_rows_carry_the_line_they_were_read_from() -> None:
    """Every flagged row needs a path back to the line that produced it.

    Without this a reviewer has to take the engine's figures on trust, which in an
    audit makes them worth very little.
    """
    text = "\n".join([
        "HDFC BANK LTD",
        "  Date       Narration                    Chq./Ref.No.   Withdrawal Amt.   Deposit Amt.   Closing Balance",
        "  01/07/26   UPI-RAJESH TRADERS           UPI2241              12,450.00                     117,550.00",
        "  02/07/26   NEFT-GLOBEX SUPPLIES         NFT8340                              8,340.50      125,890.50",
    ])
    rows = parse_layout_table(text)

    assert rows is not None and len(rows) == 2
    # 1-based, as a person counts lines: the header sits on line 2.
    assert [r["source_line_start"] for r in rows] == [3, 4]
    assert all(r["source_line_end"] == r["source_line_start"] for r in rows)
    assert "UPI2241" in rows[0]["source_text"]
    assert "NFT8340" in rows[1]["source_text"]


def test_wrapped_narration_extends_the_cited_line_range() -> None:
    """A citation covering only the first line points at a partial narration, so the
    reviewer sees less than the engine read and cannot tell why the row matched."""
    text = "\n".join([
        "  Date       Narration                    Chq./Ref.No.   Withdrawal Amt.   Deposit Amt.   Closing Balance",
        "  01/07/26   UPI-RAJESH TRADERS           UPI2241              12,450.00                     117,550.00",
        "             INV-2241 SETTLEMENT",
    ])
    rows = parse_layout_table(text)

    assert rows is not None and len(rows) == 1
    assert rows[0]["source_line_start"] == 2
    assert rows[0]["source_line_end"] == 3
    assert "INV-2241 SETTLEMENT" in rows[0]["source_text"]
    assert rows[0]["source_text"].count("\n") == 1


def test_page_offsets_put_a_row_on_the_right_page() -> None:
    """Pages are flattened into one string so a table can be followed across a page
    break, which loses the page number a citation needs. The offsets hand it back."""
    text = "\n".join([
        "  Date       Narration                    Chq./Ref.No.   Withdrawal Amt.   Deposit Amt.   Closing Balance",
        "  01/07/26   UPI-RAJESH TRADERS           UPI2241              12,450.00                     117,550.00",
        "  02/07/26   NEFT-GLOBEX SUPPLIES         NFT8340                              8,340.50      125,890.50",
    ])
    # Second page begins at line index 2.
    rows = parse_layout_table(text, [0, 2])

    assert rows is not None
    assert [r["source_page"] for r in rows] == [1, 2]


# --- Layouts beyond HDFC's -----------------------------------------------------
# Each block below is one Indian bank's actual column arrangement. They are here
# because each one, before these cases existed, either sent a perfectly aligned
# statement to the model or — worse — parsed it into the wrong answer.

_SPACED_DATES = """\
STATE BANK OF INDIA

Txn Date      Value Date    Description                             Ref No./Cheque No.      Debit          Credit         Balance
-----------------------------------------------------------------------------------------------------------------------------------
01 Jul 2026   01 Jul 2026   TO TRANSFER-UPI/DR/500584440248/RAJESH  UPI2241              12,450.00                     1,17,550.00
02 Jul 2026   02 Jul 2026   BY TRANSFER-NEFT*HDFC0000456*AMAZON     NFT8340                             8,340.50       1,25,890.50
"""


def test_dates_printed_with_a_spaced_month_are_read() -> None:
    """SBI prints "01 Jul 2026", with spaces rather than separators. A date pattern
    requiring / or - sees no rows at all and sends the whole statement to the model."""
    rows = parse_layout_table(_SPACED_DATES)
    assert rows is not None

    assert [row["date"] for row in rows] == ["01 Jul 2026", "02 Jul 2026"]
    assert rows[0]["debit"] == "12,450.00" and rows[0]["credit"] == "0"
    assert rows[1]["credit"] == "8,340.50" and rows[1]["debit"] == "0"


_LEADING_SERIAL = """\
ICICI BANK LIMITED

No.   Transaction Date   Value Date   Cheque Number   Transaction Remarks             Withdrawal Amount (INR)   Deposit Amount (INR)   Balance (INR)
-------------------------------------------------------------------------------------------------------------------------------------------------
1     01-07-2026         01-07-2026   -               UPI/500584440248/RAJESH KUM                   12,450.00                            1,17,550.00
2     02-07-2026         02-07-2026   -               NEFT-NFT8340-AMAZON SELLER                                          8,340.50       1,25,890.50
"""


def test_rows_beginning_with_a_serial_number_are_read() -> None:
    """ICICI, PNB and Bank of Baroda all number their rows, so the date is not at
    the start of the line. Anchoring the search to the line start made every row on
    those statements invisible."""
    rows = parse_layout_table(_LEADING_SERIAL)
    assert rows is not None

    assert [row["date"] for row in rows] == ["01-07-2026", "02-07-2026"]
    assert rows[0]["debit"] == "12,450.00"
    assert rows[1]["credit"] == "8,340.50"


def test_a_placeholder_in_the_reference_column_is_not_a_reference() -> None:
    """ICICI prints "-" under Cheque Number on every non-cheque row. Kept as a
    value it reads as an exact reference match between unrelated transactions."""
    rows = parse_layout_table(_LEADING_SERIAL)
    assert rows is not None

    assert all(row["reference"] is None for row in rows)


_AMOUNT_AND_TYPE = """\
PUNJAB NATIONAL BANK

Sl. No.      Transaction Date      Instrument Id           Amount   Type            Balance   Remarks
------------------------------------------------------------------------------------------------------------------------
1            01-07-2026            UPI2241              12,450.00   Dr          1,17,550.00   UPI/500584440248/RAJESH KUMAR TRADERS
2            02-07-2026            NFT8340               8,340.50   Cr          1,25,890.50   NEFT/NFT8340/AMAZON SELLER SERVICES
"""


def test_one_amount_column_with_a_type_column_carries_the_direction() -> None:
    """PNB prints no debit and no credit column: a single Amount column holds every
    figure and a Type column says Dr or Cr. Column position carries no direction at
    all here, so the table was rejected outright."""
    rows = parse_layout_table(_AMOUNT_AND_TYPE)
    assert rows is not None

    assert rows[0]["debit"] == "12,450.00" and rows[0]["credit"] == "0"
    assert rows[1]["credit"] == "8,340.50" and rows[1]["debit"] == "0"


def test_a_narration_column_after_the_money_columns_still_reads() -> None:
    """PNB puts Remarks last, after Amount, Type and Balance. Bounding the narration
    at the money position there makes the slice run backwards and empties it."""
    rows = parse_layout_table(_AMOUNT_AND_TYPE)
    assert rows is not None

    assert rows[0]["description"] == "UPI/500584440248/RAJESH KUMAR TRADERS"
    assert rows[1]["description"] == "NEFT/NFT8340/AMAZON SELLER SERVICES"


_MERGED_MONEY_COLUMN = """\
KOTAK MAHINDRA BANK LTD

Date           Narration                              Chq/Ref No      Withdrawal(Dr)/Deposit(Cr)             Balance
-------------------------------------------------------------------------------------------------------------------------
01-07-2026     UPI/500584440248/RAJESH KUMAR          UPI2241                      12,450.00(Dr)     1,17,550.00(Cr)
02-07-2026     NEFT/NFT8340/AMAZON SELLER             NFT8340                       8,340.50(Cr)     1,25,890.50(Cr)
"""


def test_a_single_money_column_takes_its_direction_from_the_inline_tag() -> None:
    """Kotak merges withdrawal and deposit into one column and tags each figure
    (Dr) or (Cr). First-keyword-wins classified that heading as a debit column,
    which booked every deposit as a withdrawal — a wrong answer, not a fallback."""
    rows = parse_layout_table(_MERGED_MONEY_COLUMN)
    assert rows is not None

    assert rows[0]["debit"] == "12,450.00" and rows[0]["credit"] == "0"
    assert rows[1]["credit"] == "8,340.50" and rows[1]["debit"] == "0"
    assert rows[1]["balance"] == "1,25,890.50"


_CHEQUE_COLUMN_BEFORE_PARTICULARS = """\
AXIS BANK LTD

Tran Date     Chq No        Particulars                                    Debit         Credit         Balance   Init. Br
--------------------------------------------------------------------------------------------------------------------------
01-07-2026                  UPI/P2A/500584440248/RAJESH KUMAR          12,450.00                    1,17,550.00   KORMNGL
07-07-2026    CHQ0452       CHQ DEP-CHQ0452                                          24,900.00      1,42,450.00   KORMNGL
"""


def test_an_empty_cheque_column_does_not_borrow_the_narration() -> None:
    """Axis puts Chq No BEFORE Particulars and leaves it blank on non-cheque rows.
    Reading the cell all the way to the figures swallowed the narration and returned
    its first word as the reference."""
    rows = parse_layout_table(_CHEQUE_COLUMN_BEFORE_PARTICULARS)
    assert rows is not None

    assert rows[0]["reference"] is None
    assert rows[0]["description"] == "UPI/P2A/500584440248/RAJESH KUMAR"
    assert rows[1]["reference"] == "CHQ0452"


_VALUE_DATE_AFTER_REFERENCE = """\
HDFC BANK LTD

Date        Narration                          Chq./Ref.No.      Value Dt     Withdrawal Amt.     Deposit Amt.     Closing Balance
-----------------------------------------------------------------------------------------------------------------------------------
18/07/26    ATW-421345XXXXXX1234-KORAMANGALA                     18/07/26           10,000.00                          2,08,397.25
21/07/26    RTGS DR-BARB0000890-MAHESH ELEC    RTG3300           21/07/26           33,050.00                          1,75,347.25
"""


def test_a_value_date_never_becomes_the_reference() -> None:
    """"Value Dt" is not a heading this recognises, so on a row carrying no
    reference the value date fell into the reference cell — giving every
    transaction dated that day an identical "reference", which is the strongest
    false-match signal the matcher has."""
    rows = parse_layout_table(_VALUE_DATE_AFTER_REFERENCE)
    assert rows is not None

    assert rows[0]["reference"] is None
    assert rows[1]["reference"] == "RTG3300"


_TALLY_LEDGER = """\
SAMPLE ENTERPRISES PVT LTD
Ledger Account

Date           Particulars                                     Vch Type      Vch No.         Debit             Credit
----------------------------------------------------------------------------------------------------------------------
1-Jul-2026     Rajesh Kumar Traders - purchase invoice         Payment       UPI2241                        12,450.00
2-Jul-2026     Amazon Marketplace - settlement received        Receipt       NFT8340       8,340.50
"""


def test_tally_voucher_columns_are_read_and_kept_out_of_the_narration() -> None:
    """TallyPrime abbreviates to "Vch Type" and "Vch No.". Neither was recognised,
    so the voucher number was lost and its type ("Payment", "Receipt") ended up
    inside the description — noise in exactly the text Stage 1 and Stage 2 match on."""
    rows = parse_layout_table(_TALLY_LEDGER)
    assert rows is not None

    assert rows[0]["description"] == "Rajesh Kumar Traders - purchase invoice"
    assert rows[0]["reference"] == "UPI2241"
    assert rows[1]["description"] == "Amazon Marketplace - settlement received"
    assert rows[1]["reference"] == "NFT8340"
