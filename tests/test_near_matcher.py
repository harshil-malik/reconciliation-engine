from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.matching.matcher import match
from app.matching.models import MatchResult
from app.matching.near_matcher import match_near
from app.schema import NO_NARRATION, Transaction


def _txn(source: str, amount: str, day: int, description: str, reference=None) -> Transaction:
    return Transaction(
        date=date(2026, 7, day),
        amount=Decimal(amount),
        description=description,
        reference=reference,
        source=source,
        file_name=f"{source}.pdf",
        raw_row={},
    )


def _leftovers(bank, ledger) -> MatchResult:
    return MatchResult(matched=[], unmatched_bank=bank, unmatched_ledger=ledger)


def test_recovers_a_fee_sized_gap_with_a_matching_payee() -> None:
    """The case Stage 1 is too strict for: a payment that reached the bank net of a
    fee. Deterministic and explainable — no model needed."""
    bank = _txn("bank", "-33050", 21, "RTGS-MAHESH ELECTRICALS PVT LTD")
    ledger = _txn("ledger", "-33000", 21, "Mahesh Elec - equipment purchase")

    result = match_near(_leftovers([bank], [ledger]))

    assert len(result.matched) == 1
    assert result.matched[0].rule == "near_amount_matching_description"
    assert result.unmatched_bank == [] and result.unmatched_ledger == []


def test_rejects_a_gap_too_large_to_be_a_fee() -> None:
    bank = _txn("bank", "-30000", 6, "NEFT TO APEX TOOLS")
    ledger = _txn("ledger", "-24000", 6, "Apex Tools purchase")

    result = match_near(_leftovers([bank], [ledger]))

    assert result.matched == []


def test_rejects_opposite_directions() -> None:
    """A receipt is never a payment, however alike the wording."""
    bank = _txn("bank", "25000", 7, "CHQ DEP CLIENT ADVANCE")
    ledger = _txn("ledger", "-25000", 7, "Cheque deposit - client advance")

    assert match_near(_leftovers([bank], [ledger])).matched == []


def test_rejects_rows_with_nothing_identifying_them() -> None:
    bank = _txn("bank", "-25050", 7, NO_NARRATION)
    ledger = _txn("ledger", "-25000", 7, "Cheque deposit - client advance")

    assert match_near(_leftovers([bank], [ledger])).matched == []


def test_best_described_candidate_wins_rather_than_input_order() -> None:
    """Assignment is global: a weak candidate listed first must not claim a partner
    that another row matches far better."""
    weak = _txn("bank", "-15010", 15, "NEFT PAYMENT MISC")
    strong = _txn("bank", "-15020", 15, "NEFT-VENDOR PAYMENT ACME LTD")
    ledger = _txn("ledger", "-15000", 15, "Vendor payment - ACME Ltd consulting")

    result = match_near(_leftovers([weak, strong], [ledger]))

    assert len(result.matched) == 1
    assert result.matched[0].bank_transaction.id == strong.id
    assert result.unmatched_bank == [weak]


def test_leaves_stage_1_matches_untouched() -> None:
    bank = _txn("bank", "-15075", 1, "NEFT VENDOR PAYMENT", "N123")
    ledger = _txn("ledger", "-15075", 1, "Vendor Payment - ABC", "N123")
    stage_1 = match([bank], [ledger])

    result = match_near(stage_1)

    assert len(result.matched) == 1
    assert result.matched[0].rule == "exact_amount_same_date"
