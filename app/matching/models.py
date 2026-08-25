from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from app.schema import Transaction

# exact_amount_date: amount matched exactly and dates fell within tolerance, with no
# other candidate to disambiguate from.
# exact_amount_date_fuzzy_desc: multiple same-amount/same-date candidates existed;
# reference or description similarity broke the tie.
MatchRule = Literal["exact_amount_date", "exact_amount_date_fuzzy_desc"]


class MatchedPair(BaseModel):
    bank_transaction: Transaction
    ledger_transaction: Transaction
    rule: MatchRule
    # Set only when the fuzzy tiebreak rule fired; the score that cleared the threshold.
    similarity: Optional[float] = None


class MatchResult(BaseModel):
    matched: list[MatchedPair]
    unmatched_bank: list[Transaction]
    unmatched_ledger: list[Transaction]
