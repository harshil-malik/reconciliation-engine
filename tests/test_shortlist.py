from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.ai_matching.shortlist import _embedding_text, shortlist_candidates
from app.schema import Transaction


class _FixedEmbeddingClient:
    """Test double giving full control over which vectors come back — real
    similarity behavior is exercised by feeding in hand-picked orthogonal/identical
    vectors rather than relying on a real embedding model in tests."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


def _txn(*, source: str, amount: str, day: int, description: str = "x") -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_shortlist_keeps_high_similarity_candidate_above_threshold() -> None:
    bank_txn = _txn(source="bank", amount="1000", day=1, description="close match")
    good_ledger = _txn(source="ledger", amount="999", day=2, description="close match")
    bad_ledger = _txn(source="ledger", amount="42", day=20, description="unrelated")

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(good_ledger): [1.0, 0.0],
        _embedding_text(bad_ledger): [0.0, 1.0],
    }

    candidates = shortlist_candidates(
        [bank_txn],
        [good_ledger, bad_ledger],
        embedding_client=_FixedEmbeddingClient(vectors),
        top_k=3,
        similarity_threshold=0.5,
    )

    assert len(candidates) == 1
    matched_bank, matched_ledger, score = candidates[0]
    assert matched_bank.id == bank_txn.id
    assert matched_ledger.id == good_ledger.id
    assert score == 1.0


def test_shortlist_respects_top_k() -> None:
    bank_txn = _txn(source="bank", amount="1000", day=1)
    ledgers = [_txn(source="ledger", amount="1000", day=1, description=f"l{i}") for i in range(5)]

    vectors = {_embedding_text(bank_txn): [1.0, 0.0]}
    for i, ledger in enumerate(ledgers):
        # all point the same general direction as the bank vector so every one
        # clears a threshold of 0.0 — top_k alone must do the limiting here
        vectors[_embedding_text(ledger)] = [1.0, 0.01 * i]

    candidates = shortlist_candidates(
        [bank_txn],
        ledgers,
        embedding_client=_FixedEmbeddingClient(vectors),
        top_k=2,
        similarity_threshold=0.0,
    )

    assert len(candidates) == 2


def test_shortlist_empty_when_either_side_has_no_transactions() -> None:
    assert shortlist_candidates([], [], embedding_client=_FixedEmbeddingClient({})) == []
