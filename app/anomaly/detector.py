from __future__ import annotations

from app.anomaly.config import AnomalyConfig
from app.anomaly.models import AnomalyResult
from app.anomaly.rules import (
    detect_duplicate_payments,
    detect_reversed_mirrored,
    detect_round_numbers,
    detect_threshold_avoidance,
    detect_timing_gaps,
)
from app.matching.models import MatchResult
from app.schema import Transaction


def detect_anomalies(
    match_result: MatchResult, *, config: AnomalyConfig | None = None
) -> AnomalyResult:
    """Runs on the full reconciled set — matched and unmatched rows from both sides —
    not just Stage 1/2 leftovers. An anomaly (e.g. a duplicate payment) can exist on
    an already-matched transaction, since it happened twice and both instances
    matched cleanly.

    Rule-based only, per the build order (AI judgment layers on top in a later pass).
    """
    config = config or AnomalyConfig()

    bank_txns: list[Transaction] = [
        pair.bank_transaction for pair in match_result.matched
    ] + match_result.unmatched_bank
    ledger_txns: list[Transaction] = [
        pair.ledger_transaction for pair in match_result.matched
    ] + match_result.unmatched_ledger
    all_txns = bank_txns + ledger_txns

    flags = []
    # Duplicate/mirrored/timing-gap patterns are compared within one source at a
    # time — a bank-side duplicate and a ledger-side duplicate are different events.
    for source_txns in (bank_txns, ledger_txns):
        flags += detect_duplicate_payments(
            source_txns, window_days=config.duplicate_window_days
        )
        flags += detect_reversed_mirrored(
            source_txns, window_days=config.mirrored_window_days
        )
        flags += detect_timing_gaps(source_txns, gap_days=config.timing_gap_days)

    # Threshold/round-number checks are properties of a single row, regardless of
    # which source it came from.
    flags += detect_threshold_avoidance(
        all_txns,
        thresholds=config.approval_thresholds,
        margin_pct=config.threshold_margin_pct,
    )
    flags += detect_round_numbers(
        all_txns,
        unit=config.round_number_unit,
        min_amount=config.round_number_min_amount,
        max_prevalence=config.round_number_max_prevalence,
    )

    return AnomalyResult(flags=flags)
