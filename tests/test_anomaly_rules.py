from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.anomaly.rules import (
    detect_duplicate_payments,
    detect_reversed_mirrored,
    detect_round_numbers,
    detect_threshold_avoidance,
    detect_timing_gaps,
)
from app.schema import Transaction


def _txn(*, amount: str, day: int, description: str = "generic") -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        source="bank",
        file_name="f.csv",
        raw_row={},
    )


def test_duplicate_payment_flagged_within_window() -> None:
    a = _txn(amount="5000", day=1, description="Vendor Payment")
    b = _txn(amount="5000", day=2, description="Vendor Payment")

    flags = detect_duplicate_payments([a, b], window_days=1)

    assert len(flags) == 1
    assert flags[0].rule == "duplicate_payment"
    assert {t.id for t in flags[0].transactions} == {a.id, b.id}


def test_duplicate_payment_not_flagged_outside_window() -> None:
    a = _txn(amount="5000", day=1, description="Vendor Payment")
    b = _txn(amount="5000", day=10, description="Vendor Payment")

    assert detect_duplicate_payments([a, b], window_days=1) == []


def test_duplicate_payment_not_flagged_when_description_differs() -> None:
    a = _txn(amount="5000", day=1, description="Vendor Payment")
    b = _txn(amount="5000", day=1, description="Unrelated Refund")

    assert detect_duplicate_payments([a, b], window_days=1) == []


def test_threshold_avoidance_flags_amount_just_below_threshold() -> None:
    txn = _txn(amount="49500", day=1)

    flags = detect_threshold_avoidance(
        [txn], thresholds=[Decimal("50000")], margin_pct=Decimal("0.02")
    )

    assert len(flags) == 1
    assert flags[0].rule == "just_below_approval_threshold"


def test_threshold_avoidance_ignores_amount_well_below_threshold() -> None:
    txn = _txn(amount="40000", day=1)

    flags = detect_threshold_avoidance(
        [txn], thresholds=[Decimal("50000")], margin_pct=Decimal("0.02")
    )

    assert flags == []


def test_threshold_avoidance_ignores_amount_at_or_above_threshold() -> None:
    txn = _txn(amount="50000", day=1)

    flags = detect_threshold_avoidance(
        [txn], thresholds=[Decimal("50000")], margin_pct=Decimal("0.02")
    )

    assert flags == []


def test_round_number_flagged_when_it_stands_out() -> None:
    """One round amount among ragged ones is the signal the rule is looking for."""
    round_txn = _txn(amount="20000", day=1)
    others = [_txn(amount=amount, day=2) for amount in ("13457", "28931", "17264")]

    flags = detect_round_numbers(
        [round_txn, *others], unit=Decimal("1000"), min_amount=Decimal("10000")
    )

    assert len(flags) == 1
    assert flags[0].rule == "round_number_entry"
    assert flags[0].transactions[0].id == round_txn.id


def test_round_number_stays_silent_when_round_amounts_are_the_norm() -> None:
    """If most of the book is round, roundness is this client's normal payment
    behaviour, not an anomaly — flagging it all buries the real findings."""
    txns = [_txn(amount=amount, day=1) for amount in ("20000", "45000", "12000", "13457")]

    flags = detect_round_numbers(
        [*txns], unit=Decimal("1000"), min_amount=Decimal("10000")
    )

    assert flags == []


def test_round_number_ignores_non_round_amount() -> None:
    txn = _txn(amount="20050", day=1)

    flags = detect_round_numbers(
        [txn], unit=Decimal("1000"), min_amount=Decimal("10000")
    )

    assert flags == []


def test_round_number_ignores_small_round_amount_below_minimum() -> None:
    txn = _txn(amount="500", day=1)

    flags = detect_round_numbers(
        [txn], unit=Decimal("1000"), min_amount=Decimal("10000")
    )

    assert flags == []


def test_reversed_mirrored_flagged_within_window() -> None:
    pos = _txn(amount="5000", day=1)
    neg = _txn(amount="-5000", day=2)

    flags = detect_reversed_mirrored([pos, neg], window_days=3)

    assert len(flags) == 1
    assert flags[0].rule == "reversed_mirrored_entry"
    assert {t.id for t in flags[0].transactions} == {pos.id, neg.id}


def test_reversed_mirrored_not_flagged_outside_window() -> None:
    pos = _txn(amount="5000", day=1)
    neg = _txn(amount="-5000", day=10)

    assert detect_reversed_mirrored([pos, neg], window_days=3) == []


def test_reversed_mirrored_not_flagged_without_opposite_sign_counterpart() -> None:
    pos_1 = _txn(amount="5000", day=1)
    pos_2 = _txn(amount="5000", day=2)

    assert detect_reversed_mirrored([pos_1, pos_2], window_days=3) == []


def test_timing_gap_flagged_when_gap_at_or_above_threshold() -> None:
    a = _txn(amount="100", day=1)
    b = _txn(amount="200", day=20)

    flags = detect_timing_gaps([a, b], gap_days=14)

    assert len(flags) == 1
    assert flags[0].rule == "unusual_timing_gap"
    assert {t.id for t in flags[0].transactions} == {a.id, b.id}


def test_timing_gap_not_flagged_for_normal_activity() -> None:
    a = _txn(amount="100", day=1)
    b = _txn(amount="200", day=5)

    assert detect_timing_gaps([a, b], gap_days=14) == []
