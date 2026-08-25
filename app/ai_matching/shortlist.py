from __future__ import annotations

import math

from app.ai_matching.embeddings import EmbeddingClient
from app.schema import Transaction


def _embedding_text(txn: Transaction) -> str:
    ref = txn.reference or "none"
    return f"{txn.description} | reference: {ref} | amount: {txn.amount} | date: {txn.date.isoformat()}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def shortlist_candidates(
    bank_txns: list[Transaction],
    ledger_txns: list[Transaction],
    *,
    embedding_client: EmbeddingClient,
    top_k: int = 3,
    similarity_threshold: float = 0.5,
) -> list[tuple[Transaction, Transaction, float]]:
    """Embeds unmatched rows and returns (bank_txn, ledger_txn, cosine_similarity)
    triples worth sending to the LLM for confirmation.

    This is the cost-control step: an LLM call per candidate pair here is cheap
    because embeddings + cosine similarity narrowed bank_rows x ledger_rows down to
    only the plausible pairs, instead of confirming every row against every row.
    """
    if not bank_txns or not ledger_txns:
        return []

    bank_vectors = embedding_client.embed([_embedding_text(t) for t in bank_txns])
    ledger_vectors = embedding_client.embed([_embedding_text(t) for t in ledger_txns])

    candidates: list[tuple[Transaction, Transaction, float]] = []
    for bank_txn, bank_vec in zip(bank_txns, bank_vectors):
        scored = sorted(
            (
                (ledger_txn, _cosine_similarity(bank_vec, ledger_vec))
                for ledger_txn, ledger_vec in zip(ledger_txns, ledger_vectors)
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        for ledger_txn, score in scored[:top_k]:
            if score >= similarity_threshold:
                candidates.append((bank_txn, ledger_txn, score))

    return candidates
