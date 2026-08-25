from __future__ import annotations

import logging

from app.matching.matcher import match
from app.matching.text_similarity import candidate_similarity
from app.matching.tolerance import CORROBORATION_FLOOR
from app.schema import Transaction

logger = logging.getLogger(__name__)

# A pairing counts as evidence for a convention only if the two rows also say
# something similar about WHO the transaction was with. Amount-and-date agreement
# alone is cheap: on a statement carrying same-day offsetting legs, the inverted
# convention lines up with the nameless leg and scores just as many matches as the
# correct one. Requiring a little description agreement separates them.
_CORROBORATION_FLOOR = CORROBORATION_FLOOR


class ConventionChoice:
    """Which Debit/Credit reading of a ledger was used, and how clear the call was."""

    def __init__(self, name: str, transactions: list[Transaction], scores: dict[str, int]):
        self.name = name
        self.transactions = transactions
        self.scores = scores

    @property
    def is_ambiguous(self) -> bool:
        ordered = sorted(self.scores.values(), reverse=True)
        return len(ordered) > 1 and ordered[0] == ordered[1]


def choose_ledger_convention(
    bank_txns: list[Transaction], candidates: dict[str, list[Transaction]]
) -> ConventionChoice:
    """Pick the Debit/Credit convention that actually reconciles against this bank.

    Whether a ledger's Debit means money in or money out depends on which account it
    covers, and the file rarely says. Getting it backwards inverts every amount and
    fails silently: nothing errors, matches simply collapse, and the rows that do
    match can pair a receipt against a same-day payment of equal size — a confident,
    plausible, wrong result.

    Rather than making the person uploading get it right, both readings are tried and
    the one producing more deterministic matches wins. That is a fair test because
    Stage 1 requires exact signed amounts within a date window, so the wrong
    orientation cannot score well except by coincidence.
    """
    scores = {
        name: sum(
            1
            for pair in match(bank_txns, txns).matched
            if candidate_similarity(pair.bank_transaction, pair.ledger_transaction)
            >= _CORROBORATION_FLOOR
        )
        for name, txns in candidates.items()
    }
    best = max(scores, key=lambda name: scores[name])
    choice = ConventionChoice(best, candidates[best], scores)

    detail = ", ".join(f"{name}={score}" for name, score in sorted(scores.items()))
    if choice.is_ambiguous:
        # Equal scores mean the evidence does not distinguish them — usually a tiny
        # ledger, or one that barely overlaps the statement period. Say so rather
        # than presenting a coin toss as a determination.
        logger.warning(
            "Ledger Debit/Credit convention is ambiguous (%s) — defaulting to %r. "
            "If the report looks inverted, set the ledger template explicitly.",
            detail, best,
        )
    else:
        logger.info("Ledger convention detected: %r (matches by convention: %s)", best, detail)
    return choice
