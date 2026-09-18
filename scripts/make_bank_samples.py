#!/usr/bin/env python
"""Generate bank-statement + ledger PDF pairs in the layout of seven Indian banks.

`make_sample_data.py` emits one pair in HDFC's layout. That is enough to prove the
pipeline works end to end, but not enough to build the rest of `bank_templates/`
against: every bank prints the same seven facts in a different shape, and the
shapes are what a template has to survive. The interesting variation is not
cosmetic —

  SBI     writes direction into the narration itself: TO TRANSFER / BY TRANSFER.
  HDFC    splits the amount across Withdrawal and Deposit columns, balance last.
  ICICI   leads with a serial number and labels its money columns "(INR)".
  PNB     has NO debit/credit columns at all — one Amount column and a Type
          column holding Dr or Cr, so column position tells you nothing.
  BoB     suffixes the running balance with Cr, which a naive number parser will
          happily swallow into the figure.
  Axis    trails an Init. Br column AFTER the balance, so "last number on the
          row is the balance" stops being true.
  Kotak   merges withdrawal and deposit into ONE column, tagging each amount
          (Dr) or (Cr) inline.

Every file uses Indian digit grouping (1,30,000.00, not 130,000.00), which is what
these banks actually print and which breaks a parser expecting thousands groups.

The transaction data is deliberately IDENTICAL to `make_sample_data.py` — same
fourteen transactions, same clearing lag, same bank-fee difference, same duplicate
payment, same bank-only and ledger-only rows. Only the rendering differs. That is
the point: the expected reconciliation outcome documented there holds for all seven
pairs, so a wrong answer is a rendering the parser mishandled, never a change in
the underlying facts.

The ledgers are TallyPrime's columnar ledger layout — Date, Particulars, Vch Type,
Vch No., Debit, Credit, with opening and closing balance lines and no running
balance column, which is how Tally actually prints it and which exercises the
`balance: null` path that the existing fixture never reaches.

These are synthetic fixtures: fictional account holder, fictional account numbers,
and a footer on every page saying so. They imitate layout, not authenticity.

    python scripts/make_bank_samples.py
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"
OPENING_BALANCE = Decimal("130000.00")
HOLDER = "SAMPLE ENTERPRISES PVT LTD"
FOOTER = "SYNTHETIC SAMPLE generated for software testing. Not a bank document."


# --------------------------------------------------------------------------- money


def inr(value: Decimal) -> str:
    """Format with Indian digit grouping: 1,30,000.00 rather than 130,000.00.

    The last three digits group together, everything above them groups in twos.
    Indian banks and Tally both print this way, so a fixture that used western
    grouping would quietly excuse a parser that cannot read the real thing.
    """
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


# -------------------------------------------------------------------- transactions


@dataclass(frozen=True)
class Txn:
    day: int
    kind: str          # upi | neft | imps | rtgs | chq | atm | salary | charges
    payee: str         # as the bank prints it, upper case
    ref: str           # cheque/UTR reference in the bank's reference column
    purpose: str       # short purpose token some banks embed in the narration
    vpa: str           # UPI handle, where the rail is UPI
    ifsc: str          # counterparty IFSC, where the rail carries one
    particulars: str   # the ledger side's description
    vch_type: str      # Tally voucher type
    amount: str        # signed; positive is money INTO the account

    @property
    def value(self) -> Decimal:
        return Decimal(self.amount)

    @property
    def inflow(self) -> bool:
        return self.value > 0

    @property
    def utr(self) -> str:
        """A stable 12-digit transaction id derived from the reference.

        Derived rather than stored so the same row always carries the same id in
        every bank's rendering — which is what lets a matcher be tested on the id
        surviving seven different narration grammars.
        """
        seed = zlib.crc32((self.ref or self.payee).encode()) % 10**11
        return f"5{seed:011d}"


TRANSACTIONS: list[Txn] = [
    Txn(1, "upi", "RAJESH KUMAR TRADERS", "UPI2241", "INV2241", "rajeshtraders@okaxis",
        "UTIB0000123", "Rajesh Kumar Traders - purchase invoice #INV-2241",
        "Payment", "-12450.00"),
    Txn(2, "neft", "AMAZON SELLER SERVICES PVT LTD", "NFT8340", "SETTLEMENT", "",
        "HDFC0000456", "Amazon Marketplace - settlement received",
        "Receipt", "8340.50"),
    Txn(3, "salary", "SALARY AUG EMPID4521", "SAL4521", "SALARY", "", "",
        "Salary credited - August", "Receipt", "74850.00"),
    Txn(5, "imps", "SUNIL ENTERPRISES", "IMP7788", "FREIGHT", "", "PUNB0000789",
        "Sunil Enterprises - freight charges", "Payment", "-3215.75"),
    Txn(7, "chq", "CLIENT ADVANCE", "CHQ0452", "ADVANCE", "", "",
        "Cheque deposit - client advance", "Receipt", "24900.00"),
    Txn(9, "neft", "GST REFUND CPIN9982", "GST9982", "GSTREFUND", "", "SBIN0000001",
        "GST refund received", "Receipt", "12480.00"),
    Txn(12, "upi", "SWIGGY INSTAMART", "UPI5512", "SUPPLIES", "swiggy@ybl",
        "ICIC0000234", "Swiggy Instamart - office supplies", "Payment", "-1187.50"),
    Txn(15, "neft", "ACME LTD", "NFT1520", "CONSULTING", "", "KKBK0000567",
        "Vendor payment - ACME Ltd (consulting)", "Payment", "-15320.00"),
    Txn(18, "atm", "ATM KORAMANGALA", "", "CASH", "", "",
        "Cash withdrawal - petty cash", "Contra", "-10000.00"),
    Txn(21, "rtgs", "MAHESH ELECTRICALS PVT LTD", "RTG3300", "EQUIPMENT", "",
        "BARB0000890", "Mahesh Elec - equipment purchase", "Payment", "-33050.00"),
    Txn(25, "neft", "OFFICE RENT JULY", "NFT4000", "RENT", "", "UTIB0000123",
        "Office rent - July", "Payment", "-39750.00"),
    Txn(28, "neft", "VERMA & ASSOCIATES", "NFT5525", "CONSULTANCY", "",
        "HDFC0000456", "Verma & Associates - consultancy fee", "Payment", "-5525.00"),
    # The same invoice paid twice, a day apart. Both legs reconcile perfectly; only
    # anomaly detection can see the duplication. Separate UTRs, as two real NEFT
    # payments would carry, so the matcher pairs them one-to-one.
    Txn(19, "neft", "KUMAR STATIONERS", "NFT3312A", "INV3312", "", "PUNB0000789",
        "Kumar Stationers - invoice INV-3312", "Payment", "-18750.00"),
    Txn(20, "neft", "KUMAR STATIONERS", "NFT3312B", "INV3312", "", "PUNB0000789",
        "Kumar Stationers - invoice INV-3312", "Payment", "-18750.00"),
]

BANK_ONLY = Txn(30, "charges", "SMS ALERT CHARGES Q2", "", "CHARGES", "", "",
                "", "", "-59.00")
LEDGER_ONLY = Txn(31, "chq", "KUMAR STATIONERS", "CHQ0461", "INV3312", "", "",
                  "Cheque issued - Kumar Stationers (not presented)",
                  "Payment", "-4200.00")

# The ledger books ACME two days after the bank cleared it (clearing lag), records
# Mahesh net of a bank fee so Stage 1 cannot match it on amount, and drops Mahesh's
# reference so the fuzzy tiebreak cannot lean on it either.
LEDGER_DATE_OVERRIDES = {"NFT1520": 17}
LEDGER_AMOUNT_OVERRIDES = {"RTG3300": "-33000.00"}
LEDGER_REFERENCE_OVERRIDES = {"RTG3300": ""}


# ------------------------------------------------------------------ narration ----
# One builder per bank. These are the grammars the banks print, and they are the
# whole reason for generating seven files instead of one.


def _sbi(t: Txn) -> str:
    lead = "BY" if t.inflow else "TO"
    dr_cr = "CR" if t.inflow else "DR"
    if t.kind == "upi":
        return f"{lead} TRANSFER-UPI/{dr_cr}/{t.utr}/{t.payee[:18]}/{t.ifsc[:4]}/{t.vpa}/{t.purpose}"
    if t.kind == "neft":
        return f"{lead} TRANSFER-NEFT*{t.ifsc}*{t.ref}*{t.payee}"
    if t.kind == "imps":
        return f"{lead} TRANSFER-IMPS/{t.utr}/{t.payee}"
    if t.kind == "rtgs":
        return f"{lead} TRANSFER-RTGS*{t.ifsc}*{t.ref}*{t.payee}"
    if t.kind == "chq":
        return f"{lead} CLEARING-CHEQUE NO {t.ref}"
    if t.kind == "atm":
        return "TO ATM WDL-ATM CASH/S1CA1234/KORAMANGALA BLR"
    if t.kind == "salary":
        return f"BY TRANSFER-INB SALARY JUL/{t.ref}"
    return f"TO CHARGES-{t.payee} INCL GST"


def _hdfc(t: Txn) -> str:
    if t.kind == "upi":
        return f"UPI-{t.payee}-{t.vpa}-{t.ifsc}-{t.utr}-{t.purpose}"
    if t.kind == "neft":
        return f"NEFT {'CR' if t.inflow else 'DR'}-{t.ifsc}-{t.payee}-{t.ref}"
    if t.kind == "imps":
        return f"IMPS-{t.utr}-{t.payee}-{t.ifsc}"
    if t.kind == "rtgs":
        return f"RTGS DR-{t.ifsc}-{t.payee}-{t.ref}"
    if t.kind == "chq":
        return f"{'CHQ DEP' if t.inflow else 'CHQ PAID'}-{t.ref}"
    if t.kind == "atm":
        return "ATW-421345XXXXXX1234-S1CA1234-KORAMANGALA"
    if t.kind == "salary":
        return f"SALARY CREDIT-{t.ref}"
    return "SMS ALERT CHARGES Q2 INCL GST"


def _icici(t: Txn) -> str:
    if t.kind == "upi":
        return f"UPI/{t.utr}/{t.purpose}/{t.payee[:10]}/{t.ifsc[:4]}/{t.vpa[:12]}"
    if t.kind == "neft":
        return f"NEFT-{t.ref}-{t.payee}"
    if t.kind == "imps":
        return f"MMT/IMPS/{t.utr}/{t.purpose[:12]}/{t.payee[:10]}"
    if t.kind == "rtgs":
        return f"RTGS/{t.ref}/{t.payee}"
    if t.kind == "chq":
        return f"{'CLG' if t.inflow else 'CHQ PAID'}/{t.ref}"
    if t.kind == "atm":
        return "ATM/S1CA1234/KORAMANGALA/BLR"
    if t.kind == "salary":
        return f"SAL-{t.ref}-JUL26"
    return f"BIL/ONL/{t.utr[:9]}/SMS ALERT CHG"


def _pnb(t: Txn) -> str:
    if t.kind == "upi":
        return f"UPI/{t.utr}/{t.payee}"
    if t.kind == "neft":
        return f"NEFT/{t.ref}/{t.payee}"
    if t.kind == "imps":
        return f"IMPS/{t.utr}/{t.payee}"
    if t.kind == "rtgs":
        return f"RTGS/{t.ref}/{t.payee}"
    if t.kind == "chq":
        return f"CHEQUE {'DEPOSIT' if t.inflow else 'PAID'} {t.ref}"
    if t.kind == "atm":
        return "ATM CASH WITHDRAWAL KORAMANGALA"
    if t.kind == "salary":
        return f"SALARY CREDIT {t.ref}"
    return "SMS ALERT CHARGES Q2"


def _bob(t: Txn) -> str:
    if t.kind == "upi":
        return f"UPI/{t.utr}/{t.payee}/{t.vpa}"
    if t.kind == "neft":
        return f"NEFT/{t.ifsc}/{t.ref}/{t.payee}"
    if t.kind == "imps":
        return f"IMPS/{t.utr}/{t.payee}"
    if t.kind == "rtgs":
        return f"RTGS/{t.ifsc}/{t.ref}/{t.payee}"
    if t.kind == "chq":
        return f"CHEQUE {'DEPOSITED' if t.inflow else 'PAID'}"
    if t.kind == "atm":
        return "ATM CASH WDL KORAMANGALA BLR"
    if t.kind == "salary":
        return f"SALARY CREDIT {t.ref}"
    return "SMS ALERT CHG Q2 INCL GST"


def _axis(t: Txn) -> str:
    if t.kind == "upi":
        # P2M for a merchant, P2A for a person or firm — Axis distinguishes them.
        leg = "P2M" if "SWIGGY" in t.payee else "P2A"
        return f"UPI/{leg}/{t.utr}/{t.payee[:22]}"
    if t.kind == "neft":
        return f"NEFT/{t.ref}/{t.payee}"
    if t.kind == "imps":
        return f"IMPS/{t.utr}/{t.payee}"
    if t.kind == "rtgs":
        return f"RTGS/{t.ref}/{t.payee}"
    if t.kind == "chq":
        return f"{'CHQ DEP' if t.inflow else 'CHQ PAID'}-{t.ref}"
    if t.kind == "atm":
        return "ATM-CASH/KORAMANGALA/BLR"
    if t.kind == "salary":
        return f"NEFT/{t.ref}/SALARY JUL"
    return "CONS CHG SMS ALERT Q2"


def _kotak(t: Txn) -> str:
    if t.kind == "upi":
        return f"UPI/{t.utr}/{t.payee}/{t.vpa}"
    if t.kind == "neft":
        return f"NEFT/{t.ref}/{t.payee}/{t.ifsc}"
    if t.kind == "imps":
        return f"IMPS/{t.utr}/{t.payee}"
    if t.kind == "rtgs":
        return f"RTGS/{t.ref}/{t.payee}"
    if t.kind == "chq":
        return f"CHQ {'DEP' if t.inflow else 'PAID'} {t.ref}"
    if t.kind == "atm":
        return "ATM WDL KORAMANGALA BLR"
    if t.kind == "salary":
        return f"SALARY {t.ref}"
    return "SMS ALERT CHARGES Q2"


# ---------------------------------------------------------------------- layouts --

Column = tuple[str, int, str]  # heading, width, alignment ("l" or "r")


@dataclass(frozen=True)
class BankLayout:
    slug: str
    name: str
    account_no: str
    ifsc: str
    branch: str
    columns: list[Column]
    date: Callable[[int], str]
    narration: Callable[[Txn], str]
    # Given the row, its running balance and its 1-based serial, produce one cell
    # per column. Kept per-bank because the money columns are exactly where the
    # banks disagree with each other.
    cells: Callable[[Txn, Decimal, int, "BankLayout"], list[str]]
    # Index of the free-text column, which is the only one long enough to need
    # wrapping onto continuation lines.
    narration_col: int
    ledger_name: str


def _pad(text: str, width: int, align: str) -> str:
    """Place a cell in its column, always leaving at least one space of gutter.

    Without it two columns fuse into one token in the extracted text —
    "12,450.00Dr", "BalanceRemarks" — which no parser can split back apart. A
    left-aligned cell needs the spare column only on its right; a right-aligned
    one needs it on BOTH sides, because its content ends flush against whatever
    column follows, and the column that follows an amount is often a Type or
    branch code that starts flush against it in turn.
    """
    if align == "r":
        # TWO trailing spaces, not one. Layout-mode extraction separates table cells
        # on runs of 2+ spaces, and a right-aligned heading ends flush against its
        # column edge — so a single space leaves "Amount Type" reading as one cell,
        # and the Type column simply disappears from the parsed header.
        return text[:width - 3].rjust(width - 2) + "  "
    return text[:width - 1].ljust(width)


def _wrap(text: str, width: int) -> list[str]:
    """Split an over-long narration into a first line plus continuation lines.

    Statements wrap; they do not truncate. Breaking on a space where one is near
    the margin and hard-splitting where none is (long slash-joined UPI strings
    have none) is what the banks themselves print, and it exercises the
    continuation-line rule every template carries.
    """
    limit = width - 1
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _debit_credit(t: Txn) -> tuple[str, str]:
    """The classic two-column split: an amount lands in one column, never both."""
    return ("", inr(t.value)) if t.inflow else (inr(-t.value), "")


def _sbi_cells(t, bal, serial, layout):
    debit, credit = _debit_credit(t)
    return [layout.date(t.day), layout.date(t.day), layout.narration(t),
            t.ref, debit, credit, inr(bal)]


def _hdfc_cells(t, bal, serial, layout):
    withdrawal, deposit = _debit_credit(t)
    return [layout.date(t.day), layout.narration(t), t.ref, layout.date(t.day),
            withdrawal, deposit, inr(bal)]


def _icici_cells(t, bal, serial, layout):
    withdrawal, deposit = _debit_credit(t)
    cheque = t.ref if t.kind == "chq" else "-"
    return [str(serial), layout.date(t.day), layout.date(t.day), cheque,
            layout.narration(t), withdrawal, deposit, inr(bal)]


def _pnb_cells(t, bal, serial, layout):
    # One Amount column and a Type column. Column position carries no direction
    # here, so a parser must read Dr/Cr or get every sign wrong.
    return [str(serial), layout.date(t.day), t.ref or "-",
            inr(abs(t.value)), "Cr" if t.inflow else "Dr", inr(bal),
            layout.narration(t)]


def _bob_cells(t, bal, serial, layout):
    debit, credit = _debit_credit(t)
    # BoB tags the running balance Cr — a suffix a number parser must not absorb.
    return [str(serial), layout.date(t.day), layout.date(t.day),
            layout.narration(t), t.ref if t.kind == "chq" else "",
            debit, credit, f"{inr(bal)} Cr"]


def _axis_cells(t, bal, serial, layout):
    debit, credit = _debit_credit(t)
    # Init. Br trails the balance, so "the last number on the row is the balance"
    # is false here — the row ends with a branch code.
    return [layout.date(t.day), t.ref if t.kind == "chq" else "",
            layout.narration(t), debit, credit, inr(bal), "KORMNGL"]


def _kotak_cells(t, bal, serial, layout):
    # One merged money column, direction tagged inline.
    amount = f"{inr(abs(t.value))}({'Cr' if t.inflow else 'Dr'})"
    return [layout.date(t.day), layout.narration(t), t.ref or "-", amount,
            f"{inr(bal)}(Cr)"]


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _d_slash_short(day: int) -> str:      # 01/07/26
    return f"{day:02d}/07/26"


def _d_slash_full(day: int) -> str:       # 01/07/2026
    return f"{day:02d}/07/2026"


def _d_dash_full(day: int) -> str:        # 01-07-2026
    return f"{day:02d}-07-2026"


def _d_spaced(day: int) -> str:           # 01 Jul 2026
    return f"{day:02d} Jul 2026"


def _d_tally(day: int) -> str:            # 1-Jul-2026
    return f"{day}-Jul-2026"


LAYOUTS: list[BankLayout] = [
    BankLayout(
        slug="sbi", name="STATE BANK OF INDIA",
        account_no="3812 3456 7890", ifsc="SBIN0001234", branch="KORAMANGALA, BENGALURU",
        columns=[("Txn Date", 12, "l"), ("Value Date", 12, "l"),
                 ("Description", 64, "l"), ("Ref No./Cheque No.", 20, "l"),
                 ("Debit", 15, "r"), ("Credit", 15, "r"), ("Balance", 17, "r")],
        date=_d_spaced, narration=_sbi, cells=_sbi_cells, narration_col=2,
        ledger_name="SBI Current A/c 38123456790"),
    BankLayout(
        slug="hdfc", name="HDFC BANK LTD",
        account_no="5020 0012 3456 78", ifsc="HDFC0000123", branch="KORAMANGALA, BENGALURU",
        columns=[("Date", 10, "l"), ("Narration", 60, "l"),
                 ("Chq./Ref.No.", 16, "l"), ("Value Dt", 10, "l"),
                 ("Withdrawal Amt.", 18, "r"), ("Deposit Amt.", 16, "r"),
                 ("Closing Balance", 18, "r")],
        date=_d_slash_short, narration=_hdfc, cells=_hdfc_cells, narration_col=1,
        ledger_name="HDFC Bank Current A/c"),
    BankLayout(
        slug="icici", name="ICICI BANK LIMITED",
        account_no="0012 3456 7890", ifsc="ICIC0000012", branch="KORAMANGALA, BENGALURU",
        columns=[("No.", 5, "l"), ("Transaction Date", 18, "l"),
                 ("Value Date", 12, "l"), ("Cheque Number", 15, "l"),
                 ("Transaction Remarks", 46, "l"),
                 ("Withdrawal Amount (INR)", 26, "r"),
                 ("Deposit Amount (INR)", 23, "r"), ("Balance (INR)", 17, "r")],
        date=_d_dash_full, narration=_icici, cells=_icici_cells, narration_col=4,
        ledger_name="ICICI Bank Current A/c"),
    BankLayout(
        slug="pnb", name="PUNJAB NATIONAL BANK",
        account_no="1234 0011 2233 44", ifsc="PUNB0123400", branch="KORAMANGALA, BENGALURU",
        columns=[("Sl. No.", 12, "l"), ("Transaction Date", 20, "l"),
                 ("Instrument Id", 18, "l"), ("Amount", 18, "r"),
                 ("Type", 8, "l"), ("Balance", 19, "r"), ("Remarks", 58, "l")],
        date=_d_dash_full, narration=_pnb, cells=_pnb_cells, narration_col=6,
        ledger_name="PNB Current A/c"),
    BankLayout(
        slug="bob", name="BANK OF BARODA",
        account_no="4455 0100 0012 34", ifsc="BARB0KORAMA", branch="KORAMANGALA, BENGALURU",
        columns=[("Sr", 4, "l"), ("Tran Date", 12, "l"), ("Value Date", 12, "l"),
                 ("Description", 56, "l"), ("Cheque No", 12, "l"),
                 ("Debit", 15, "r"), ("Credit", 15, "r"), ("Balance", 20, "r")],
        date=_d_slash_full, narration=_bob, cells=_bob_cells, narration_col=3,
        ledger_name="Bank of Baroda Current A/c"),
    BankLayout(
        slug="axis", name="AXIS BANK LTD",
        account_no="9180 1002 3456 78", ifsc="UTIB0000918", branch="KORAMANGALA, BENGALURU",
        columns=[("Tran Date", 12, "l"), ("Chq No", 12, "l"),
                 ("Particulars", 58, "l"), ("Debit", 15, "r"), ("Credit", 15, "r"),
                 ("Balance", 17, "r"), ("Init. Br", 10, "l")],
        date=_d_dash_full, narration=_axis, cells=_axis_cells, narration_col=2,
        ledger_name="Axis Bank Current A/c"),
    BankLayout(
        slug="kotak", name="KOTAK MAHINDRA BANK LTD",
        account_no="7311 2345 6789", ifsc="KKBK0000731", branch="KORAMANGALA, BENGALURU",
        columns=[("Date", 12, "l"), ("Narration", 62, "l"),
                 ("Chq/Ref No", 16, "l"),
                 ("Withdrawal(Dr)/Deposit(Cr)", 29, "r"), ("Balance", 20, "r")],
        date=_d_dash_full, narration=_kotak, cells=_kotak_cells, narration_col=1,
        ledger_name="Kotak Mahindra Current A/c"),
]


# ------------------------------------------------------------------- documents ---


def build_statement(layout: BankLayout) -> list[str]:
    rows = sorted(list(TRANSACTIONS) + [BANK_ONLY], key=lambda t: t.day)
    header = "".join(_pad(title, width, "l" if align == "l" else "r")
                     for title, width, align in layout.columns)
    lines = [
        layout.name,
        "Statement of Account",
        "",
        f"Account Holder   : {HOLDER}",
        f"Account Number   : {layout.account_no}",
        f"IFSC / Branch    : {layout.ifsc}  {layout.branch}",
        "Statement Period : 01-Jul-2026 to 31-Jul-2026",
        f"Opening Balance  : {inr(OPENING_BALANCE)}",
        "",
        header,
        "-" * len(header),
    ]
    def render(cells: list[str]) -> str:
        return "".join(
            _pad(cell, width, align)
            for cell, (_, width, align) in zip(cells, layout.columns)
        ).rstrip()

    balance = OPENING_BALANCE
    for serial, txn in enumerate(rows, start=1):
        balance += txn.value
        cells = layout.cells(txn, balance, serial, layout)
        index = layout.narration_col
        chunks = _wrap(cells[index], layout.columns[index][1])
        cells[index] = chunks[0]
        lines.append(render(cells))
        # A continuation carries no date, no reference and no amount — only the
        # rest of the narration, which is what marks it as a continuation.
        for chunk in chunks[1:]:
            continuation = [""] * len(layout.columns)
            continuation[index] = chunk
            lines.append(render(continuation))
    lines += [
        "-" * len(header),
        "",
        f"Closing Balance  : {inr(balance)}",
        "",
        FOOTER,
    ]
    return lines


def build_ledger(layout: BankLayout) -> list[str]:
    """TallyPrime columnar ledger for the bank account, in the client's own books.

    Debit increases the bank asset (money in), Credit decreases it (money out) —
    the mirror image of the statement, which is written from the bank's side.
    """
    columns: list[Column] = [("Date", 13, "l"), ("Particulars", 54, "l"),
                             ("Vch Type", 12, "l"), ("Vch No.", 14, "l"),
                             ("Debit", 18, "r"), ("Credit", 18, "r")]
    header = "".join(_pad(title, width, "l" if align == "l" else "r")
                     for title, width, align in columns)

    rows: list[tuple[int, str, str, str, Decimal]] = []
    for txn in list(TRANSACTIONS) + [LEDGER_ONLY]:
        rows.append((
            LEDGER_DATE_OVERRIDES.get(txn.ref, txn.day),
            txn.particulars,
            txn.vch_type,
            LEDGER_REFERENCE_OVERRIDES.get(txn.ref, txn.ref),
            Decimal(LEDGER_AMOUNT_OVERRIDES.get(txn.ref, txn.amount)),
        ))

    lines = [
        HOLDER,
        "Ledger Account",
        f"{layout.ledger_name}",
        "1-Jul-2026 to 31-Jul-2026",
        "",
        header,
        "-" * len(header),
        "".join([_pad("", 13, "l"), _pad("Opening Balance", 54, "l"),
                 _pad("", 12, "l"), _pad("", 14, "l"),
                 _pad(inr(OPENING_BALANCE), 18, "r"), _pad("", 18, "r")]),
    ]

    total_debit = OPENING_BALANCE
    total_credit = Decimal("0.00")
    balance = OPENING_BALANCE
    for day, particulars, vch_type, reference, amount in sorted(rows, key=lambda r: r[0]):
        balance += amount
        debit = inr(amount) if amount > 0 else ""
        credit = inr(-amount) if amount < 0 else ""
        total_debit += amount if amount > 0 else Decimal("0.00")
        total_credit += -amount if amount < 0 else Decimal("0.00")
        lines.append("".join([
            _pad(_d_tally(day), 13, "l"), _pad(particulars, 54, "l"),
            _pad(vch_type, 12, "l"), _pad(reference, 14, "l"),
            _pad(debit, 18, "r"), _pad(credit, 18, "r"),
        ]))

    lines += [
        "-" * len(header),
        "".join([_pad("", 13, "l"), _pad("Closing Balance", 54, "l"),
                 _pad("", 12, "l"), _pad("", 14, "l"),
                 _pad("", 18, "r"), _pad(inr(balance), 18, "r")]),
        "".join([_pad("", 13, "l"), _pad("", 54, "l"), _pad("", 12, "l"),
                 _pad("", 14, "l"), _pad(inr(total_debit), 18, "r"),
                 _pad(inr(total_credit + balance), 18, "r")]),
        "",
        FOOTER,
    ]
    return lines


# ------------------------------------------------------------------- pdf writer --


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_text_pdf(lines: list[str], path: Path, *, font_size: int = 7,
                   leading: int = 10) -> None:
    """Write monospaced text to a landscape PDF, paginating as needed.

    Hand-rolled for the same reason `make_sample_data.py` hand-rolls its own: the
    pipeline needs a real text layer with column alignment preserved, and Courier
    at a fixed pitch gives exactly that with no extra dependency. This version
    paginates, because a seven-bank sweep will not always fit on one page.
    """
    width, height = 842, 595
    left, top, bottom_margin = 18, height - 30, 24
    per_page = max(1, int((top - bottom_margin) // leading))
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    page_count = len(pages)
    font_number = 3 + 2 * page_count
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        font_number: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    }
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode()

    for index, page_lines in enumerate(pages):
        page_number = 3 + 2 * index
        content_number = page_number + 1
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Contents {content_number} 0 R /Resources << /Font << /F1 "
            f"{font_number} 0 R >> >> >>"
        ).encode()
        content = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", f"{left} {top} Td"]
        for line in page_lines:
            content.append(f"({_escape(line)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1", "replace")
        objects[content_number] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number in range(1, font_number + 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {font_number + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {font_number + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    path.write_bytes(bytes(out))


def main() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for layout in LAYOUTS:
        directory = OUTPUT_DIR / layout.slug
        directory.mkdir(exist_ok=True)
        statement = directory / f"{layout.slug}_bank_statement.pdf"
        ledger = directory / f"{layout.slug}_ledger.pdf"
        write_text_pdf(build_statement(layout), statement)
        write_text_pdf(build_ledger(layout), ledger)
        print(f"{layout.name:<26} {statement.relative_to(OUTPUT_DIR.parent)}")
        print(f"{'':<26} {ledger.relative_to(OUTPUT_DIR.parent)}")

    print(
        "\nAll seven pairs carry identical transactions, so the expected outcome is "
        "the same for each:\n"
        "  13 matched by Stage 1, the Mahesh Electricals pair recovered by Stage 1.5 "
        "(33,050.00 vs 33,000.00),\n"
        "  one legitimately unmatched row per side (SMS charges on the bank, an "
        "unpresented cheque in the ledger),\n"
        "  and the Kumar Stationers duplicate flagged though both its legs reconcile."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
