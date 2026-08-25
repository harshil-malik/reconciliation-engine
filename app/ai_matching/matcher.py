from __future__ import annotations

import logging
import re
from decimal import Decimal

from app.ai_matching.confirmer import ConfirmationResult, MatchConfirmer
from app.ai_matching.embeddings import EmbeddingClient
from app.ai_matching.models import AIMatchedPair, AIMatchResult
from app.ai_matching.shortlist import shortlist_candidates
from app.matching.models import MatchResult
from app.schema import NO_NARRATION, Transaction

logger = logging.getLogger(__name__)

# Stage 2 exists to recover pairs separated by a bank fee, rounding, or a clearing
# lag — not to pair up arbitrary amounts. A fee is small in both absolute and
# relative terms, so a gap beyond BOTH of these cannot be one, and the pair is
# rejected without spending a model call. Observed necessity: a local 3B matched
# -12,500 to -8,340 (a 33% gap) at 0.8 confidence while asserting in its reasoning
# that the amounts were "the same".
_MAX_FEE_GAP_ABSOLUTE = Decimal("500")
_MAX_FEE_GAP_FRACTION = Decimal("0.02")


def _identifying_text(txn: Transaction) -> str:
    """What the model could use to tell WHO this transaction was with."""
    if txn.reference:
        return txn.reference
    description = (txn.description or "").strip()
    if description == NO_NARRATION:
        return ""
    return description if re.search(r"[A-Za-z]{3}", description) else ""


def _fee_gap_is_plausible(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    gap = abs(bank_txn.amount - ledger_txn.amount)
    magnitude = max(abs(bank_txn.amount), abs(ledger_txn.amount))
    return gap <= max(_MAX_FEE_GAP_ABSOLUTE, magnitude * _MAX_FEE_GAP_FRACTION)


def match_with_ai(
    match_result: MatchResult,
    *,
    embedding_client: EmbeddingClient,
    confirmer: MatchConfirmer,
    top_k: int = 3,
    similarity_threshold: float = 0.5,
    # Tuned against scripts/eval_confirmer.py on the local Qwen2.5-3B setup: every
    # true negative in that set scored <= 0.2 while true matches scored 0.5-0.8, so
    # anything in 0.3-0.5 gives identical results (6/8 recall, zero false positives)
    # and 0.5 is the conservative end of that plateau. Raise it if your data shows
    # false positives; the eval script is there to re-measure rather than guess.
    confidence_threshold: float = 0.5,
) -> AIMatchResult:
    """Stage 2: runs only on what Stage 1 couldn't resolve (match_result.unmatched_*).

    Two-step to control cost — an embeddings shortlist narrows candidates first, then
    an LLM call confirms only those, instead of one call per bank_row x ledger_row.
    Confirmations below `confidence_threshold` are treated as no-match: better to
    leave a row in the CA's "unmatched" queue than assert a wrong match.

    A failure talking to the embedding/LLM provider (rate limit, timeout, malformed
    response) is treated the same way — the affected row(s) fall through to
    "unmatched" rather than the failure taking down the whole /reconcile request and
    losing Stage 1's already-resolved matches with it.
    """
    try:
        candidates = shortlist_candidates(
            match_result.unmatched_bank,
            match_result.unmatched_ledger,
            embedding_client=embedding_client,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
    except Exception:
        logger.exception("Stage 2 shortlist failed; leaving all rows for CA review")
        candidates = []

    confirmed: list[tuple[Transaction, Transaction, ConfirmationResult]] = []
    for bank_txn, ledger_txn, _score in candidates:
        # Opposite-direction pairs can never be the same transaction: amounts are
        # normalized signed on both sides (inflow positive, outflow negative), so a
        # bank inflow and a ledger outflow are different economic events regardless
        # of how alike their descriptions look. Enforced here rather than left to the
        # model — a local 3B was observed confidently matching +60,000 against
        # -60,000, and a wrong match silently hides a real discrepancy. Skipping also
        # saves a model call.
        if (bank_txn.amount > 0) != (ledger_txn.amount > 0):
            continue

        # Without a payee or reference on BOTH sides there is no evidence of who the
        # transaction was with, so any match would rest on amount and date alone —
        # which the confirmation prompt explicitly forbids, and which a small model
        # nonetheless does when handed a row it cannot identify.
        if not (_identifying_text(bank_txn) and _identifying_text(ledger_txn)):
            continue

        # A gap too large to be a bank fee or rounding is not the problem Stage 2
        # was built to solve, whatever the model thinks of the descriptions.
        if not _fee_gap_is_plausible(bank_txn, ledger_txn):
            continue

        try:
            result = confirmer.confirm(bank_txn, ledger_txn)
        except Exception:
            logger.exception(
                "Stage 2 confirmation failed for bank_txn=%s ledger_txn=%s; leaving unmatched",
                bank_txn.id,
                ledger_txn.id,
            )
            continue
        # Decision keys off `confidence`, not the `is_match` boolean. The prompt
        # defines confidence as the model's probability that the two rows are the
        # same transaction, so thresholding it is the direct reading — and on a
        # local 3B the boolean proved badly calibrated against the model's own
        # number, returning is_match=False on pairs it simultaneously scored 0.8.
        # The boolean is still parsed and kept for the audit trail.
        if result.confidence >= confidence_threshold:
            confirmed.append((bank_txn, ledger_txn, result))

    # Strongest confirmations win first, so a row that appears in more than one
    # confirmed pair (e.g. two similar-looking candidates) is claimed by the best one.
    confirmed.sort(key=lambda item: item[2].confidence, reverse=True)

    ai_matched: list[AIMatchedPair] = []
    used_bank_ids: set[str] = set()
    used_ledger_ids: set[str] = set()
    for bank_txn, ledger_txn, result in confirmed:
        if bank_txn.id in used_bank_ids or ledger_txn.id in used_ledger_ids:
            continue
        used_bank_ids.add(bank_txn.id)
        used_ledger_ids.add(ledger_txn.id)
        ai_matched.append(
            AIMatchedPair(
                bank_transaction=bank_txn,
                ledger_transaction=ledger_txn,
                confidence=result.confidence,
                reasoning=result.reasoning,
            )
        )

    return AIMatchResult(
        ai_matched=ai_matched,
        unmatched_bank=[
            t for t in match_result.unmatched_bank if t.id not in used_bank_ids
        ],
        unmatched_ledger=[
            t for t in match_result.unmatched_ledger if t.id not in used_ledger_ids
        ],
    )
