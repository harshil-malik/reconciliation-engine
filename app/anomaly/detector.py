from __future__ import annotations

from app.anomaly.config import AnomalyConfig
from app.anomaly.models import AnomalyFlag, AnomalyResult
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

    return AnomalyResult(flags=_collapse_mirrored_duplicates(flags, match_result))


def _collapse_mirrored_duplicates(
    flags: list[AnomalyFlag], match_result: MatchResult
) -> list[AnomalyFlag]:
    """Report a double payment once, not once per set of books.

    Duplicate detection runs separately over the statement and the ledger, so an
    invoice genuinely paid twice is found on both sides and reported twice — the
    same event, split across two flags, which a reviewer then has to recognise as
    one. Where the two groups are the same transactions seen from either side (every
    row in one is matched to a row in the other), they are merged into a single
    finding carrying all the evidence.

    A duplicate found on only ONE side is deliberately left alone. That is a
    different and more serious finding — the bank debited twice while the books
    recorded it once, or the reverse — and collapsing it into the mirrored case
    would hide exactly the discrepancy worth chasing.
    """
    bank_to_ledger = {
        pair.bank_transaction.id: pair.ledger_transaction.id for pair in match_result.matched
    }

    duplicates = [f for f in flags if f.rule == "duplicate_payment"]
    others = [f for f in flags if f.rule != "duplicate_payment"]
    ledger_flags = [f for f in duplicates if all(t.source == "ledger" for t in f.transactions)]

    merged: list[AnomalyFlag] = []
    consumed: set[int] = set()
    for flag in duplicates:
        if id(flag) in consumed:
            continue
        if not all(t.source == "bank" for t in flag.transactions):
            continue

        counterparts = {bank_to_ledger.get(t.id) for t in flag.transactions}
        if None in counterparts:
            continue  # not every leg reconciled, so the two sides are not equivalent

        for candidate in ledger_flags:
            if id(candidate) in consumed:
                continue
            if {t.id for t in candidate.transactions} != counterparts:
                continue
            consumed.update({id(flag), id(candidate)})
            merged.append(
                AnomalyFlag(
                    rule="duplicate_payment",
                    transactions=[*flag.transactions, *candidate.transactions],
                    reason=(
                        f"{len(flag.transactions)} payments of the same amount and "
                        "description, recorded identically in the statement and the "
                        "ledger — the books agree, so this reconciles cleanly and is "
                        "only visible as a possible double payment. "
                        + flag.reason.split(" — ")[0]
                    ),
                )
            )
            break

    untouched = [f for f in duplicates if id(f) not in consumed]
    return [*merged, *untouched, *others]
