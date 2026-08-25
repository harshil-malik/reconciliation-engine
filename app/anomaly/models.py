from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schema import Transaction

AnomalyRule = Literal[
    "duplicate_payment",
    "just_below_approval_threshold",
    "round_number_entry",
    "reversed_mirrored_entry",
    "unusual_timing_gap",
]


class AnomalyFlag(BaseModel):
    rule: AnomalyRule
    # 1 transaction for round-number/threshold rules; 2 for duplicate/mirrored/timing-gap
    # pairs — kept as a list rather than separate fields so callers don't need to branch
    # on rule type to know what's implicated.
    transactions: list[Transaction]
    reason: str


class AnomalyResult(BaseModel):
    flags: list[AnomalyFlag]
