from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.anomaly.config import AnomalyConfig
from app.anomaly.detector import detect_anomalies
from app.matching.models import MatchedPair, MatchResult
from app.schema import Transaction


def _txn(*, source: str, amount: str, day: int, description: str = "generic") -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_anomaly_can_be_flagged_on_an_already_matched_pair() -> None:
    """A duplicate payment that happened twice matches cleanly on both sides — the
    spec calls this out explicitly as the reason Stage 3 must scan matched rows too,
    not just Stage 1/2 leftovers."""
    bank_1 = _txn(source="bank", amount="5000", day=1, description="Vendor Payment")
    bank_2 = _txn(source="bank", amount="5000", day=2, description="Vendor Payment")
    ledger_1 = _txn(source="ledger", amount="5000", day=1, description="Vendor Payment")
    ledger_2 = _txn(source="ledger", amount="5000", day=2, description="Vendor Payment")

    match_result = MatchResult(
        matched=[
            MatchedPair(bank_transaction=bank_1, ledger_transaction=ledger_1, rule="exact_amount_same_date"),
            MatchedPair(bank_transaction=bank_2, ledger_transaction=ledger_2, rule="exact_amount_same_date"),
        ],
        unmatched_bank=[],
        unmatched_ledger=[],
    )

    result = detect_anomalies(match_result, config=AnomalyConfig(duplicate_window_days=1))

    duplicate_flags = [f for f in result.flags if f.rule == "duplicate_payment"]
    # both the bank-side pair and the ledger-side pair get flagged independently
    assert len(duplicate_flags) == 2
    flagged_ids = {t.id for flag in duplicate_flags for t in flag.transactions}
    assert flagged_ids == {bank_1.id, bank_2.id, ledger_1.id, ledger_2.id}


def test_anomaly_flags_unmatched_rows_too() -> None:
    unmatched_bank = _txn(source="bank", amount="49500", day=1)

    match_result = MatchResult(matched=[], unmatched_bank=[unmatched_bank], unmatched_ledger=[])

    result = detect_anomalies(
        match_result,
        config=AnomalyConfig(approval_thresholds=[Decimal("50000")], threshold_margin_pct=Decimal("0.02")),
    )

    assert any(f.rule == "just_below_approval_threshold" for f in result.flags)


def test_bank_and_ledger_duplicates_are_not_cross_matched() -> None:
    """A single bank duplicate shouldn't be paired against a single ledger duplicate
    as if they were the same-source event — duplicate detection is per source."""
    bank_1 = _txn(source="bank", amount="5000", day=1, description="Vendor Payment")
    bank_2 = _txn(source="bank", amount="5000", day=2, description="Vendor Payment")
    ledger_1 = _txn(source="ledger", amount="5000", day=1, description="Vendor Payment")

    match_result = MatchResult(
        matched=[
            MatchedPair(bank_transaction=bank_1, ledger_transaction=ledger_1, rule="exact_amount_same_date")
        ],
        unmatched_bank=[bank_2],
        unmatched_ledger=[],
    )

    result = detect_anomalies(match_result, config=AnomalyConfig(duplicate_window_days=1))

    duplicate_flags = [f for f in result.flags if f.rule == "duplicate_payment"]
    assert len(duplicate_flags) == 1
    assert {t.id for t in duplicate_flags[0].transactions} == {bank_1.id, bank_2.id}


def test_duplicate_payment_is_flagged_even_though_both_legs_reconcile() -> None:
    """The case the spec singles out: paying an invoice twice produces two genuine
    transactions that appear on BOTH sides, so they match perfectly and matching
    alone can never surface the problem. Anomaly detection runs over the full
    reconciled set precisely so this is still caught."""
    bank_first = _txn(source="bank", amount="-18750", day=19, description="NEFT-KUMAR STATIONERS INV-3312")
    bank_second = _txn(source="bank", amount="-18750", day=20, description="NEFT-KUMAR STATIONERS INV-3312")
    ledger_first = _txn(source="ledger", amount="-18750", day=19, description="NEFT-KUMAR STATIONERS INV-3312")
    ledger_second = _txn(source="ledger", amount="-18750", day=20, description="NEFT-KUMAR STATIONERS INV-3312")

    match_result = MatchResult(
        matched=[
            MatchedPair(bank_transaction=bank_first, ledger_transaction=ledger_first, rule="exact_amount_same_date"),
            MatchedPair(bank_transaction=bank_second, ledger_transaction=ledger_second, rule="exact_amount_same_date"),
        ],
        unmatched_bank=[],
        unmatched_ledger=[],
    )

    flags = detect_anomalies(match_result).flags
    duplicates = [f for f in flags if f.rule == "duplicate_payment"]

    assert duplicates, "a duplicate that reconciles cleanly must still be flagged"
    assert all(len(f.transactions) == 2 for f in duplicates)
