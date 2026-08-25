from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from app.anomaly.models import AnomalyFlag
from app.schema import Transaction


def _normalize_description(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def detect_duplicate_payments(
    transactions: list[Transaction], *, window_days: int
) -> list[AnomalyFlag]:
    """Same amount + description appearing more than once within `window_days` —
    the classic double-payment. Grouped within a single source (bank or ledger)."""
    groups: dict[tuple[Decimal, str], list[Transaction]] = defaultdict(list)
    for txn in transactions:
        groups[(txn.amount, _normalize_description(txn.description))].append(txn)

    flags: list[AnomalyFlag] = []
    for (amount, _), group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda t: t.date)
        cluster = [group[0]]
        for txn in group[1:]:
            if (txn.date - cluster[-1].date).days <= window_days:
                cluster.append(txn)
                continue
            if len(cluster) > 1:
                flags.append(_duplicate_flag(cluster, amount, window_days))
            cluster = [txn]
        if len(cluster) > 1:
            flags.append(_duplicate_flag(cluster, amount, window_days))
    return flags


def _duplicate_flag(
    cluster: list[Transaction], amount: Decimal, window_days: int
) -> AnomalyFlag:
    dates = ", ".join(t.date.isoformat() for t in cluster)
    return AnomalyFlag(
        rule="duplicate_payment",
        transactions=cluster,
        reason=(
            f"{len(cluster)} transactions with identical amount ({amount}) and matching "
            f"description within {window_days} day(s) of each other (dates: {dates}) — "
            "possible duplicate payment."
        ),
    )


def detect_threshold_avoidance(
    transactions: list[Transaction],
    *,
    thresholds: list[Decimal],
    margin_pct: Decimal,
) -> list[AnomalyFlag]:
    """Amount sits just under a known approval threshold — a common pattern for
    avoiding a required sign-off."""
    flags: list[AnomalyFlag] = []
    for txn in transactions:
        abs_amount = abs(txn.amount)
        for threshold in thresholds:
            lower_bound = threshold * (Decimal("1") - margin_pct)
            if lower_bound <= abs_amount < threshold:
                flags.append(
                    AnomalyFlag(
                        rule="just_below_approval_threshold",
                        transactions=[txn],
                        reason=(
                            f"Amount {abs_amount} is just below the {threshold} approval "
                            f"threshold (within {margin_pct:.0%})."
                        ),
                    )
                )
                break
    return flags


def detect_round_numbers(
    transactions: list[Transaction],
    *,
    unit: Decimal,
    min_amount: Decimal,
) -> list[AnomalyFlag]:
    """Amount is an exact multiple of `unit` and large enough to be notable — real
    transaction amounts rarely land on a round number by chance."""
    flags: list[AnomalyFlag] = []
    for txn in transactions:
        abs_amount = abs(txn.amount)
        if abs_amount >= min_amount and abs_amount % unit == 0:
            flags.append(
                AnomalyFlag(
                    rule="round_number_entry",
                    transactions=[txn],
                    reason=(
                        f"Amount {abs_amount} is an exact multiple of {unit} — round-number "
                        "entries can indicate a manual adjustment rather than a genuine "
                        "transaction."
                    ),
                )
            )
    return flags


def detect_reversed_mirrored(
    transactions: list[Transaction], *, window_days: int
) -> list[AnomalyFlag]:
    """A +X transaction and a -X transaction within `window_days` of each other, in
    the same source — looks like an entry that was reversed and possibly re-entered."""
    by_abs_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        by_abs_amount[abs(txn.amount)].append(txn)

    flags: list[AnomalyFlag] = []
    for abs_amount, group in by_abs_amount.items():
        if abs_amount == 0:
            continue
        positives = sorted((t for t in group if t.amount > 0), key=lambda t: t.date)
        negatives = sorted((t for t in group if t.amount < 0), key=lambda t: t.date)
        used_negative_ids: set[str] = set()

        for pos in positives:
            for neg in negatives:
                if neg.id in used_negative_ids:
                    continue
                if abs((pos.date - neg.date).days) <= window_days:
                    used_negative_ids.add(neg.id)
                    flags.append(
                        AnomalyFlag(
                            rule="reversed_mirrored_entry",
                            transactions=[pos, neg],
                            reason=(
                                f"Matching +{abs_amount}/-{abs_amount} entries "
                                f"{abs((pos.date - neg.date).days)} day(s) apart "
                                f"({pos.date.isoformat()} and {neg.date.isoformat()}) — "
                                "looks like a reversed/mirrored entry."
                            ),
                        )
                    )
                    break
    return flags


def detect_timing_gaps(
    transactions: list[Transaction], *, gap_days: int
) -> list[AnomalyFlag]:
    """A gap of `gap_days` or more between two consecutive transactions in the same
    source — worth checking for entries missing from that window."""
    sorted_txns = sorted(transactions, key=lambda t: t.date)
    flags: list[AnomalyFlag] = []
    for prev, curr in zip(sorted_txns, sorted_txns[1:]):
        gap = (curr.date - prev.date).days
        if gap >= gap_days:
            flags.append(
                AnomalyFlag(
                    rule="unusual_timing_gap",
                    transactions=[prev, curr],
                    reason=(
                        f"{gap}-day gap between transactions on {prev.date.isoformat()} and "
                        f"{curr.date.isoformat()} — longer than the {gap_days}-day expected "
                        "activity window; check for missing entries in between."
                    ),
                )
            )
    return flags
