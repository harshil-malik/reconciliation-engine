from __future__ import annotations

import logging
from decimal import Decimal

from app.matching.models import MatchedPair, MatchResult
from app.matching.text_similarity import candidate_similarity, references_match
from app.matching.tolerance import fee_gap_is_plausible, same_direction
from app.schema import NO_NARRATION, Transaction

logger = logging.getLogger(__name__)


def _is_identifiable(txn: Transaction) -> bool:
    """Does this row say anything about WHO the transaction was with?"""
    if txn.reference:
        return True
    description = (txn.description or "").strip()
    return bool(description) and description != NO_NARRATION


def match_near(
    match_result: MatchResult,
    *,
    date_tolerance_days: int = 7,
    # Measured, not guessed. Scoring on distinctive tokens rather than on raw
    # characters widened the gap this threshold sits in: on the sample statements the
    # genuine near-amount pair now scores 0.50, while spurious pairings among the
    # leftovers share no identifying word at all and score 0.00. 0.35 sits well
    # inside that gap.
    #
    # The history is worth keeping, because it is an argument against tuning this
    # number again. Under character similarity the same genuine pair scored 0.3488
    # and spurious ones 0.22-0.24, leaving almost no room: 0.45 missed the real pair,
    # 0.35 missed it too once a ledger abbreviated the payee, and each miss dropped a
    # correct match through to Stage 2 and left its fee in UNEXPLAINED. The fix was
    # never a better threshold — it was comparing the right thing.
    similarity_threshold: float = 0.35,
) -> MatchResult:
    """Stage 1.5: deterministic recovery of pairs Stage 1 was too strict to see.

    Stage 1 demands exactly equal amounts, so a payment that reached the bank net of
    a fee, or landed outside its narrow date window, falls through even when the two
    rows plainly describe the same thing. That is not a judgement call needing a
    language model — it is a fee-sized difference plus a recognisable payee, both of
    which code can check exactly and explain afterwards.

    A pair must clear every one of these:
      * same direction — a receipt is never a payment
      * a gap a fee or rounding could explain (see matching/tolerance.py)
      * dates within a wider window than Stage 1, for clearing lag
      * both sides identifiable, and their descriptions or references actually
        corresponding above `similarity_threshold`

    Assignment is global rather than first-come-first-served: every candidate pair is
    scored and the strongest claims its rows first. Iterating in input order lets a
    weakly-matching row seize a partner that another row matches far better, which is
    how a nameless leg ends up claiming an entry its properly-narrated twin should
    have had.
    """
    candidates: list[tuple[float, Decimal, int, Transaction, Transaction]] = []

    for bank_txn in match_result.unmatched_bank:
        if not _is_identifiable(bank_txn):
            continue
        for ledger_txn in match_result.unmatched_ledger:
            if not _is_identifiable(ledger_txn):
                continue
            if not same_direction(bank_txn, ledger_txn):
                continue
            date_gap = abs((bank_txn.date - ledger_txn.date).days)
            if date_gap > date_tolerance_days:
                continue
            if not fee_gap_is_plausible(bank_txn, ledger_txn):
                continue
            similarity = candidate_similarity(bank_txn, ledger_txn)
            if similarity < similarity_threshold:
                continue
            amount_gap = abs(bank_txn.amount - ledger_txn.amount)
            candidates.append((similarity, amount_gap, date_gap, bank_txn, ledger_txn))

    # Strongest evidence first: closest description, then smallest money difference,
    # then closest dates.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    matched = list(match_result.matched)
    used_bank: set[str] = set()
    used_ledger: set[str] = set()

    for similarity, amount_gap, _date_gap, bank_txn, ledger_txn in candidates:
        if bank_txn.id in used_bank or ledger_txn.id in used_ledger:
            continue
        used_bank.add(bank_txn.id)
        used_ledger.add(ledger_txn.id)
        matched.append(
            MatchedPair(
                bank_transaction=bank_txn,
                ledger_transaction=ledger_txn,
                rule="near_amount_matching_description",
                similarity=similarity,
                corroboration=(
                    "reference" if references_match(bank_txn, ledger_txn) else "description"
                ),
            )
        )
        logger.info(
            "Stage 1.5 matched %r <-> %r (similarity %.2f, amount gap %s)",
            bank_txn.description[:34], ledger_txn.description[:34], similarity, amount_gap,
        )

    return MatchResult(
        matched=matched,
        unmatched_bank=[t for t in match_result.unmatched_bank if t.id not in used_bank],
        unmatched_ledger=[t for t in match_result.unmatched_ledger if t.id not in used_ledger],
    )
