from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from app.schema import Transaction

# The rule name goes into the audit report, so it states what actually fired rather
# than lumping the date cases together — a pair dated a day apart reported as
# "exact_amount_date" reads as an exact-date match to whoever reviews the workbook.
#
# exact_amount_same_date: amounts equal and both sides dated the same day.
# exact_amount_near_date:  amounts equal, dates differ but fall inside the tolerance
#                          window (bank clearing lag vs ledger entry date).
# ..._fuzzy_desc variants: several candidates shared amount and date, and reference
#                          or description similarity broke the tie.
# near_amount_matching_description: Stage 1.5 — amounts differ by no more than a
#                          bank fee or rounding, dates fall in a wider window, and
#                          the descriptions or references correspond. Deterministic
#                          and explainable, unlike an AI-confirmed match.
MatchRule = Literal[
    "exact_amount_same_date",
    "exact_amount_near_date",
    "exact_amount_same_date_fuzzy_desc",
    "exact_amount_near_date_fuzzy_desc",
    "near_amount_matching_description",
]


# How much evidence backs a match, beyond the amount and date agreeing:
#   reference            — both sides carry the same cheque/UTR/voucher number, which
#                          is as close to proof as reconciliation gets
#   description          — the payees or narrations correspond
#   amount_and_date_only — nothing but the figures line up. Usually still right, but
#                          two unrelated payments of the same size on the same day
#                          look identical to this rule, so these are the rows worth a
#                          CA's eye. Reported rather than withheld: measured on real
#                          data, no similarity threshold separates the wrong ones from
#                          the right ones, so refusing them would lose genuine matches
#                          while keeping the bad one.
Corroboration = Literal["reference", "description", "amount_and_date_only"]


class MatchedPair(BaseModel):
    bank_transaction: Transaction
    ledger_transaction: Transaction
    rule: MatchRule
    # Description/reference similarity for the pair, recorded on every match so the
    # weakly-evidenced ones can be told apart in the report.
    similarity: Optional[float] = None
    corroboration: Corroboration = "amount_and_date_only"


class MatchResult(BaseModel):
    matched: list[MatchedPair]
    unmatched_bank: list[Transaction]
    unmatched_ledger: list[Transaction]
