from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class AnomalyConfig(BaseModel):
    """Tunable thresholds for the rule-based detectors. Defaults are reasonable
    starting points for an Indian CA engagement, not fixed policy — expect these to
    be adjusted per client (e.g. a client with a 25,00,000 approval threshold instead
    of the defaults below)."""

    # duplicate_payment: same amount + description within this many days counts as
    # the same transaction happening twice.
    duplicate_window_days: int = 1

    # just_below_approval_threshold: flag amounts within `threshold_margin_pct` below
    # one of these thresholds (classic structuring-to-avoid-approval pattern).
    approval_thresholds: list[Decimal] = Field(
        default_factory=lambda: [Decimal("50000"), Decimal("100000"), Decimal("200000")]
    )
    threshold_margin_pct: Decimal = Decimal("0.02")

    # round_number_entry: flag amounts that are an exact multiple of this unit and at
    # least this large — small round numbers (e.g. a ₹500 UPI transfer) are normal.
    round_number_unit: Decimal = Decimal("1000")
    round_number_min_amount: Decimal = Decimal("10000")

    # A round number is only suspicious if round numbers are UNUSUAL in this book.
    # Plenty of businesses pay round amounts as a matter of course — rent, salaries,
    # transfers — and on a real statement this rule fired on 48% of rows, which tells
    # a CA nothing and buries the flags that matter. When more than this share of
    # eligible transactions are round, the pattern is this client's normal and the
    # rule stays silent.
    round_number_max_prevalence: Decimal = Decimal("0.30")

    # reversed_mirrored_entry: a +X and a -X transaction within this many days of each
    # other, in the same source, are treated as a possible reversal pair.
    mirrored_window_days: int = 3

    # unusual_timing_gap: flag when two consecutive transactions in the same source
    # are at least this many days apart.
    timing_gap_days: int = 14
