#!/usr/bin/env python
"""Generate a coherent bank-statement + bank-ledger PDF pair for testing.

The synthetic samples this replaces were internally contradictory — the statement
disagreed with its own running balance on two rows, and the ledger booked "Salary
Received" as a Credit (money out of a bank account) and "Purchase Invoice" as a
Debit (money in). Nothing can reconcile documents that disagree with each other, so
poor results told you nothing about whether the engine works.

Here every ledger line is the correct double-entry mirror of a statement line:

    money IN   -> statement Deposit    / ledger Debit  (bank asset increases)
    money OUT  -> statement Withdrawal / ledger Credit (bank asset decreases)

Running balances are computed, not invented, so each document is self-consistent.

The data deliberately exercises the pipeline rather than being trivially matchable:

  * clearing lag        ACME is dated 15-Jul on the statement, 17-Jul in the ledger
  * bank fee difference Mahesh 33,050.00 vs 33,000.00, so Stage 1 cannot match it
  * abbreviated payee   "RTGS-MAHESH ELECTRICALS PVT LTD" vs "Mahesh Elec"
  * bank-only row       SMS alert charges never reach the ledger
  * ledger-only row     a cheque issued but not yet presented
  * ragged amounts      so the round-number rule correctly stays quiet

Expected on a correct run: 11 matched by Stage 1, the Mahesh pair left to Stage 2,
and exactly one unmatched row on each side — both legitimately unmatched.

    python scripts/make_sample_data.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"
OPENING_BALANCE = Decimal("130000.00")

# (day, narration, reference, amount) — amount signed: positive is money into the
# account. The ledger mirrors these, so both files derive from one source of truth.
TRANSACTIONS: list[tuple[int, str, str, str, str]] = [
    # day, bank narration, reference, ledger particulars, signed amount
    (1, "UPI-RAJESH KUMAR TRADERS-INV2241", "UPI2241",
     "Rajesh Kumar Traders - purchase invoice #INV-2241", "-12450.00"),
    (2, "NEFT-AMAZON SELLER SERVICES PVT LTD", "NFT8340",
     "Amazon Marketplace - settlement received", "8340.50"),
    (3, "SALARY CREDIT-EMPID4521", "SAL4521",
     "Salary credited - August", "74850.00"),
    (5, "IMPS-SUNIL ENTERPRISES-REF7788", "IMP7788",
     "Sunil Enterprises - freight charges", "-3215.75"),
    (7, "CHQ DEP-000452", "CHQ0452",
     "Cheque deposit - client advance", "24900.00"),
    (9, "NEFT-GST REFUND CPIN9982", "GST9982",
     "GST refund received", "12480.00"),
    (12, "UPI-SWIGGY INSTAMART", "UPI5512",
     "Swiggy Instamart - office supplies", "-1187.50"),
    (15, "NEFT-VENDOR PAYMENT ACME LTD", "NFT1520",
     "Vendor payment - ACME Ltd (consulting)", "-15320.00"),
    (18, "ATM WDL-KORAMANGALA BR", "",
     "Cash withdrawal - petty cash", "-10000.00"),
    (21, "RTGS-MAHESH ELECTRICALS PVT LTD", "RTG3300",
     "Mahesh Elec - equipment purchase", "-33050.00"),
    (25, "NEFT-OFFICE RENT JULY", "NFT4000",
     "Office rent - July", "-39750.00"),
    (28, "NEFT-VERMA & ASSOCIATES", "NFT5525",
     "Verma & Associates - consultancy fee", "-5525.00"),
]

# The ledger records this one two days later than the bank (clearing lag), and one
# rupee figure differs by a bank fee so Stage 1 cannot resolve it deterministically.
LEDGER_DATE_OVERRIDES = {"NFT1520": 17}
LEDGER_AMOUNT_OVERRIDES = {"RTG3300": "-33000.00"}
# Mahesh is referenceless in the ledger, so the fuzzy tiebreak cannot lean on it.
LEDGER_REFERENCE_OVERRIDES = {"RTG3300": ""}

BANK_ONLY = (30, "BANK CHARGES-SMS ALERTS Q2", "", "-59.00")
LEDGER_ONLY = (31, "Cheque issued - Kumar Stationers (not presented)", "CHQ0461", "-4200.00")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _build_bank_lines() -> list[str]:
    lines = [
        "HDFC BANK LTD",
        "Statement of Account",
        "",
        "Account Holder   : SAMPLE ENTERPRISES PVT LTD",
        "Account No       : 5020 0012 3456 78",
        "Statement Period : 01-Jul-2026 to 31-Jul-2026",
        f"Opening Balance  : {_money(OPENING_BALANCE)}",
        "",
        f"{'Date':<12}{'Narration':<44}{'Chq./Ref.No.':<16}"
        f"{'Withdrawal Amt.':>18}{'Deposit Amt.':>18}{'Closing Balance':>18}",
        "",
    ]

    balance = OPENING_BALANCE
    rows = [(day, narration, ref, amount) for day, narration, ref, _, amount in TRANSACTIONS]
    rows.append(BANK_ONLY)
    for day, narration, reference, amount_text in sorted(rows, key=lambda r: r[0]):
        amount = Decimal(amount_text)
        balance += amount
        withdrawal = _money(-amount) if amount < 0 else ""
        deposit = _money(amount) if amount > 0 else ""
        lines.append(
            f"{f'{day:02d}/07/2026':<12}{narration:<44}{reference:<16}"
            f"{withdrawal:>18}{deposit:>18}{_money(balance):>18}"
        )
    lines += ["", f"Closing Balance  : {_money(balance)}", "",
              "This is a synthetic statement generated for software testing."]
    return lines


def _build_ledger_lines() -> list[str]:
    lines = [
        "SAMPLE ENTERPRISES PVT LTD",
        "Internal Ledger - Bank Account Book",
        "",
        "Ledger Name : HDFC Bank Current A/c",
        "Period      : 01-Jul-2026 to 31-Jul-2026",
        "",
        # Debit increases the bank asset (money in); Credit decreases it (money out).
        f"{'Date':<12}{'Particulars':<52}{'Voucher No.':<14}"
        f"{'Debit':>16}{'Credit':>16}{'Balance':>18}",
        "",
    ]

    rows: list[tuple[int, str, str, str]] = []
    for day, _, reference, particulars, amount_text in TRANSACTIONS:
        rows.append(
            (
                LEDGER_DATE_OVERRIDES.get(reference, day),
                particulars,
                LEDGER_REFERENCE_OVERRIDES.get(reference, reference),
                LEDGER_AMOUNT_OVERRIDES.get(reference, amount_text),
            )
        )
    rows.append(LEDGER_ONLY)

    balance = OPENING_BALANCE
    for day, particulars, reference, amount_text in sorted(rows, key=lambda r: r[0]):
        amount = Decimal(amount_text)
        balance += amount
        debit = _money(amount) if amount > 0 else ""
        credit = _money(-amount) if amount < 0 else ""
        lines.append(
            f"{f'{day:02d}-Jul-2026':<12}{particulars:<52}{reference:<14}"
            f"{debit:>16}{credit:>16}{_money(balance):>18}"
        )
    lines += ["", "This is a synthetic ledger generated for software testing."]
    return lines


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_text_pdf(lines: list[str], path: Path) -> None:
    """Write monospaced text to a landscape PDF.

    Hand-rolled rather than pulling in reportlab: the pipeline needs a PDF with a
    real text layer and aligned columns, and Courier at a fixed pitch gives exactly
    that in a few lines with no extra dependency.
    """
    font_size, leading, left, top = 8, 11, 20, 560
    content = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", f"{left} {top} Td"]
    for line in lines:
        content.append(f"({_escape(line)}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    path.write_bytes(bytes(out))


def main() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    bank_path = OUTPUT_DIR / "bank_statement.pdf"
    ledger_path = OUTPUT_DIR / "internal_ledger.pdf"

    write_text_pdf(_build_bank_lines(), bank_path)
    write_text_pdf(_build_ledger_lines(), ledger_path)

    print(f"wrote {bank_path}")
    print(f"wrote {ledger_path}")
    print(
        "\nExpected on a correct run: 11 matched by Stage 1, the Mahesh Electricals "
        "pair left to Stage 2 (33,050.00 vs 33,000.00),\nand exactly one legitimately "
        "unmatched row per side (SMS charges on the bank, an unpresented cheque in "
        "the ledger)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
