from __future__ import annotations

from app.matching.models import MatchedPair, MatchResult
from app.matching.text_similarity import (
    candidate_similarity,
    descriptions_corroborate,
    references_match,
)
from app.matching.tolerance import CORROBORATION_FLOOR
from app.schema import Transaction


def match(
    bank_txns: list[Transaction],
    ledger_txns: list[Transaction],
    *,
    date_tolerance_days: int = 3,
    fuzzy_threshold: float = 0.85,
) -> MatchResult:
    """Deterministic, explainable matching — no AI. Every match carries the specific
    rule that fired; rows this can't resolve are left unmatched to flow into Stage 2.

    Rule order (spec): exact amount -> date within tolerance -> if that still leaves
    more than one candidate, fuzzy reference/description similarity breaks the tie,
    only when the best score clears the threshold and isn't itself tied.
    """
    remaining_ledger = list(ledger_txns)
    matched: list[MatchedPair] = []

    for bank_txn in sorted(bank_txns, key=lambda t: (t.amount, t.date)):
        candidates = [
            ledger_txn
            for ledger_txn in remaining_ledger
            if ledger_txn.amount == bank_txn.amount
            and abs((ledger_txn.date - bank_txn.date).days) <= date_tolerance_days
        ]
        if not candidates:
            continue

        if len(candidates) == 1:
            chosen, similarity = candidates[0], None
            rule = (
                "exact_amount_same_date"
                if chosen.date == bank_txn.date
                else "exact_amount_near_date"
            )
        else:
            scored = sorted(
                ((c, candidate_similarity(bank_txn, c)) for c in candidates),
                key=lambda pair: pair[1],
                reverse=True,
            )
            best, best_score = scored[0]
            runner_up_score = scored[1][1] if len(scored) > 1 else 0.0
            if best_score < fuzzy_threshold or best_score == runner_up_score:
                continue  # genuinely ambiguous -> leave unmatched for Stage 2
            chosen, similarity = best, best_score
            rule = (
                "exact_amount_same_date_fuzzy_desc"
                if best.date == bank_txn.date
                else "exact_amount_near_date_fuzzy_desc"
            )

        # Record what actually backs this pairing. Amount and date agreeing is the
        # weakest case and stays a match — but it is the one where two unrelated
        # payments of the same size on the same day are indistinguishable, so the
        # report says so rather than presenting every match as equally sound.
        if similarity is None:
            similarity = candidate_similarity(bank_txn, chosen)
        if references_match(bank_txn, chosen):
            corroboration = "reference"
        elif descriptions_corroborate(bank_txn, chosen):
            corroboration = "description"
        else:
            corroboration = "amount_and_date_only"

        matched.append(
            MatchedPair(
                bank_transaction=bank_txn,
                ledger_transaction=chosen,
                rule=rule,
                similarity=similarity,
                corroboration=corroboration,
            )
        )
        remaining_ledger.remove(chosen)

    matched_bank_ids = {pair.bank_transaction.id for pair in matched}
    matched_ledger_ids = {pair.ledger_transaction.id for pair in matched}

    return MatchResult(
        matched=matched,
        unmatched_bank=[t for t in bank_txns if t.id not in matched_bank_ids],
        unmatched_ledger=[t for t in ledger_txns if t.id not in matched_ledger_ids],
    )
