from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.ledger_templates.generic_ledger import (
    BankAccountLedgerTemplate,
    GenericLedgerTemplate,
)
from app.ingestion.pdf_parser import PDFExtractionError, parse_pdf


class _CannedExtractor:
    def __init__(self, rows: list[dict]):
        self._payload = json.dumps({"rows": rows})

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        return self._payload


def _row(date, desc, debit="0", credit="0", balance=None, reference=None) -> dict:
    return {
        "date": date,
        "description": desc,
        "reference": reference,
        "debit": debit,
        "credit": credit,
        "balance": balance,
    }


def _parse(rows, template, tmp_path: Path, source="bank"):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    return parse_pdf(
        pdf_path, source=source, template=template, extractor=_CannedExtractor(rows)
    )


def test_running_balance_corrects_a_misread_debit_credit_column(tmp_path: Path) -> None:
    """The failure that motivated this check: a deposit read into the debit column.

    The balance rises by 45,000, so the row is money in — regardless of which column
    the model claimed it sat in.
    """
    rows = [
        _row("01/08/26", "OPENING TXN", debit="12500.00", balance="142500.00"),
        # misread: 45,000 was a Deposit, but came back as a withdrawal
        _row("05/08/26", "UPI-CUSTOMER-INV1042", debit="45000.00", balance="187500.00"),
    ]
    transactions = _parse(rows, HDFCBankTemplate(), tmp_path)

    assert transactions[1].amount == Decimal("45000.00")  # corrected to money IN


def test_running_balance_rejects_a_balance_read_as_the_amount(tmp_path: Path) -> None:
    """A closing balance reported as the transaction amount must fail loudly.

    The magnitude matches nothing plausible against the balance movement, so there is
    no way to adjudicate between the two readings and a report must not be built.
    """
    rows = [
        _row("01/08/26", "NEFT VENDOR PAYMENT", debit="12500.00", balance="142500.00"),
        # 130000 is neither the real amount nor consistent with the balance delta
        _row("05/08/26", "RTGS TRANSFER", debit="130000.00", balance="187500.00"),
    ]
    with pytest.raises(PDFExtractionError, match="disagree"):
        _parse(rows, HDFCBankTemplate(), tmp_path)


def test_wrapped_narration_line_is_not_a_phantom_transaction(tmp_path: Path) -> None:
    rows = [
        _row("09/08/26", "RTGS TRANSFER TO SUPPLIER", debit="75250.00", balance="112250.00"),
        # continuation line leaked out as its own row, with a number for a description
        _row("", "45,000.00", debit="0", credit="0"),
        _row("", "MAHESH ELECTRICALS PVT LTD", debit="0", credit="0"),
    ]
    transactions = _parse(rows, HDFCBankTemplate(), tmp_path)

    assert len(transactions) == 1
    assert transactions[0].description == "RTGS TRANSFER TO SUPPLIER"


def test_bank_account_ledger_mirrors_the_statement_sign_convention(tmp_path: Path) -> None:
    """Debit to the client's Bank A/c is money IN — the mirror of a statement."""
    rows = [_row("01/08/26", "Receipt from customer", debit="25000.00")]

    ledger = _parse(rows, BankAccountLedgerTemplate(), tmp_path, source="ledger")
    party = _parse(rows, GenericLedgerTemplate(), tmp_path, source="ledger")

    assert ledger[0].amount == Decimal("25000.00")
    assert party[0].amount == Decimal("-25000.00")
    assert ledger[0].amount == -party[0].amount


def test_opening_balance_lets_the_first_row_be_audited(tmp_path: Path) -> None:
    """Without an opening balance the first row has no predecessor and rests on its
    column position alone. With one, it is checked like every other row — here the
    column says deposit while the balance fell, and the balance wins."""
    rows = [
        _row("01/08/26", "UPI-RAJESH KUMAR TRADERS", credit="12500.00", balance="117500.00"),
        _row("02/08/26", "NEFT-AMAZON", credit="8340.00", balance="125840.00"),
    ]
    rows[0]["opening_balance"] = "130000.00"

    transactions = _parse(rows, HDFCBankTemplate(), tmp_path)

    # 130000 -> 117500 is a fall of 12500, so this was money out despite the column
    assert transactions[0].amount == Decimal("-12500.00")
    assert transactions[1].amount == Decimal("8340.00")


def test_rows_without_balances_are_left_alone(tmp_path: Path) -> None:
    """A ledger with no balance column still parses — the audit is best-effort."""
    rows = [
        _row("01/08/26", "Vendor payment", debit="15075.00"),
        _row("02/08/26", "Customer receipt", credit="25000.00"),
    ]
    transactions = _parse(rows, HDFCBankTemplate(), tmp_path)

    assert [t.amount for t in transactions] == [Decimal("-15075.00"), Decimal("25000.00")]
