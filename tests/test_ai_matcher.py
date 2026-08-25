from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.ai_matching.confirmer import ConfirmationResult
from app.ai_matching.matcher import match_with_ai
from app.ai_matching.shortlist import _embedding_text
from app.matching.models import MatchResult
from app.schema import Transaction


class _FixedEmbeddingClient:
    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


class _FakeConfirmer:
    def __init__(self, responses: dict[tuple[str, str], ConfirmationResult]):
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def confirm(self, bank_txn: Transaction, ledger_txn: Transaction) -> ConfirmationResult:
        self.calls.append((bank_txn.id, ledger_txn.id))
        return self._responses.get(
            (bank_txn.id, ledger_txn.id),
            ConfirmationResult(is_match=False, confidence=0.0, reasoning="no fixture"),
        )


def _txn(*, source: str, amount: str, day: int, description: str = "x") -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_confirmed_pair_becomes_ai_matched_and_llm_only_called_on_shortlist() -> None:
    bank_txn = _txn(source="bank", amount="998", day=1, description="NEFT vendor")
    ledger_txn = _txn(source="ledger", amount="1000", day=1, description="Vendor payment")
    unrelated_ledger = _txn(source="ledger", amount="42", day=10, description="other")

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(ledger_txn): [1.0, 0.0],
        _embedding_text(unrelated_ledger): [0.0, 1.0],
    }
    confirmer = _FakeConfirmer(
        {
            (bank_txn.id, ledger_txn.id): ConfirmationResult(
                is_match=True, confidence=0.9, reasoning="Same vendor, bank fee explains the gap."
            )
        }
    )

    match_result = MatchResult(
        matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn, unrelated_ledger]
    )

    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=confirmer,
        similarity_threshold=0.5,
        confidence_threshold=0.7,
    )

    assert len(result.ai_matched) == 1
    assert result.ai_matched[0].bank_transaction.id == bank_txn.id
    assert result.ai_matched[0].ledger_transaction.id == ledger_txn.id
    assert result.ai_matched[0].confidence == 0.9
    assert "bank fee" in result.ai_matched[0].reasoning
    assert result.unmatched_bank == []
    assert result.unmatched_ledger == [unrelated_ledger]
    # the orthogonal (low-similarity) pair never reached the LLM at all
    assert confirmer.calls == [(bank_txn.id, ledger_txn.id)]


def test_low_confidence_confirmation_leaves_rows_unmatched() -> None:
    bank_txn = _txn(source="bank", amount="998", day=1)
    ledger_txn = _txn(source="ledger", amount="1000", day=1)

    vectors = {_embedding_text(bank_txn): [1.0, 0.0], _embedding_text(ledger_txn): [1.0, 0.0]}
    confirmer = _FakeConfirmer(
        {(bank_txn.id, ledger_txn.id): ConfirmationResult(is_match=True, confidence=0.4, reasoning="uncertain")}
    )

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn])
    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=confirmer,
        confidence_threshold=0.7,
    )

    assert result.ai_matched == []
    assert result.unmatched_bank == [bank_txn]
    assert result.unmatched_ledger == [ledger_txn]


def test_opposite_direction_pair_never_reaches_the_llm() -> None:
    """A bank inflow and a ledger outflow are different economic events, however
    similar their text — rejected deterministically, without spending a model call."""
    bank_txn = _txn(source="bank", amount="60000", day=1, description="CUSTOMER RECEIPT")
    ledger_txn = _txn(source="ledger", amount="-60000", day=1, description="CUSTOMER RECEIPT")

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(ledger_txn): [1.0, 0.0],
    }
    confirmer = _FakeConfirmer(
        {
            (bank_txn.id, ledger_txn.id): ConfirmationResult(
                is_match=True, confidence=1.0, reasoning="identical descriptions"
            )
        }
    )

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn])
    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=confirmer,
        similarity_threshold=0.0,
    )

    # even a maximally confident confirmation cannot override the sign guard
    assert result.ai_matched == []
    assert confirmer.calls == []


def test_high_confidence_matches_even_when_is_match_flag_is_false() -> None:
    """Decision keys off `confidence`, not the boolean: local small models were
    observed returning is_match=False on pairs they simultaneously scored 0.8."""
    bank_txn = _txn(source="bank", amount="1000", day=1)
    ledger_txn = _txn(source="ledger", amount="1000", day=1)

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(ledger_txn): [1.0, 0.0],
    }
    confirmer = _FakeConfirmer(
        {
            (bank_txn.id, ledger_txn.id): ConfirmationResult(
                is_match=False, confidence=0.8, reasoning="same vendor, clearing lag"
            )
        }
    )

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn])
    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=confirmer,
        confidence_threshold=0.7,
    )

    assert len(result.ai_matched) == 1
    assert result.ai_matched[0].confidence == 0.8


def test_confirmer_failure_on_one_pair_leaves_it_unmatched_without_crashing() -> None:
    bank_txn = _txn(source="bank", amount="998", day=1, description="NEFT vendor")
    ledger_txn = _txn(source="ledger", amount="1000", day=1, description="Vendor payment")

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(ledger_txn): [1.0, 0.0],
    }

    class _RaisingConfirmer:
        def confirm(self, bank_txn: Transaction, ledger_txn: Transaction) -> ConfirmationResult:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn])
    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=_RaisingConfirmer(),
        confidence_threshold=0.7,
    )

    assert result.ai_matched == []
    assert result.unmatched_bank == [bank_txn]
    assert result.unmatched_ledger == [ledger_txn]


def test_shortlist_failure_leaves_everything_unmatched_without_crashing() -> None:
    bank_txn = _txn(source="bank", amount="998", day=1)
    ledger_txn = _txn(source="ledger", amount="1000", day=1)

    class _RaisingEmbeddingClient:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding API unavailable")

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_txn])
    result = match_with_ai(
        match_result,
        embedding_client=_RaisingEmbeddingClient(),
        confirmer=_FakeConfirmer({}),
        confidence_threshold=0.7,
    )

    assert result.ai_matched == []
    assert result.unmatched_bank == [bank_txn]
    assert result.unmatched_ledger == [ledger_txn]


def test_conflicting_confirmations_resolved_by_highest_confidence() -> None:
    bank_txn = _txn(source="bank", amount="1000", day=1)
    ledger_1 = _txn(source="ledger", amount="1000", day=1, description="a")
    ledger_2 = _txn(source="ledger", amount="1000", day=1, description="b")

    vectors = {
        _embedding_text(bank_txn): [1.0, 0.0],
        _embedding_text(ledger_1): [1.0, 0.0],
        _embedding_text(ledger_2): [1.0, 0.0],
    }
    confirmer = _FakeConfirmer(
        {
            (bank_txn.id, ledger_1.id): ConfirmationResult(is_match=True, confidence=0.75, reasoning="ok"),
            (bank_txn.id, ledger_2.id): ConfirmationResult(is_match=True, confidence=0.95, reasoning="better"),
        }
    )

    match_result = MatchResult(matched=[], unmatched_bank=[bank_txn], unmatched_ledger=[ledger_1, ledger_2])
    result = match_with_ai(
        match_result,
        embedding_client=_FixedEmbeddingClient(vectors),
        confirmer=confirmer,
        confidence_threshold=0.7,
        top_k=5,
        similarity_threshold=0.0,
    )

    assert len(result.ai_matched) == 1
    assert result.ai_matched[0].ledger_transaction.id == ledger_2.id
    assert result.unmatched_ledger == [ledger_1]
