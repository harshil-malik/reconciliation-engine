from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.ingestion.ledger_convention import choose_ledger_convention
from app.schema import Transaction


def _txn(source: str, amount: str, day: int, description: str) -> Transaction:
    return Transaction(
        date=date(2026, 7, day),
        amount=Decimal(amount),
        description=description,
        source=source,
        file_name=f"{source}.pdf",
        raw_row={},
    )


def test_picks_the_convention_whose_descriptions_actually_correspond() -> None:
    """Match count alone is not enough. A statement carrying same-day offsetting
    legs lets the inverted convention line up with the nameless leg and score just
    as well, so a pairing only counts when the two sides also agree on the payee."""
    bank = [
        _txn("bank", "25000", 7, "CHQ DEP-000452 CLIENT ADVANCE"),
        _txn("bank", "-25000", 7, "(no narration)"),
        _txn("bank", "12500", 9, "NEFT-GST REFUND CPIN9982"),
        _txn("bank", "-12500", 9, "(no narration)"),
    ]
    # Correct reading: the ledger records the same events with the same signs.
    correct = [
        _txn("ledger", "25000", 7, "Cheque Deposit - Client Advance"),
        _txn("ledger", "12500", 9, "GST Refund"),
    ]
    # Inverted reading: same amounts, opposite signs — these still find a partner in
    # the nameless legs, so a naive count cannot tell the two conventions apart.
    inverted = [
        _txn("ledger", "-25000", 7, "Cheque Deposit - Client Advance"),
        _txn("ledger", "-12500", 9, "GST Refund"),
    ]

    choice = choose_ledger_convention(bank, {"correct": correct, "inverted": inverted})

    assert choice.name == "correct"
    assert choice.transactions == correct
    assert choice.scores["correct"] > choice.scores["inverted"]


def test_reports_ambiguity_rather_than_asserting_a_coin_toss() -> None:
    bank = [_txn("bank", "5000", 1, "NEFT ACME SUPPLIES")]
    same = [_txn("ledger", "5000", 1, "ACME Supplies invoice")]

    choice = choose_ledger_convention(bank, {"a": same, "b": list(same)})

    assert choice.is_ambiguous
