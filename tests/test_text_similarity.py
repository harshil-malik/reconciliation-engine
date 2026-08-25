from __future__ import annotations

from decimal import Decimal

from app.matching.text_similarity import candidate_similarity, description_similarity
from app.schema import Transaction
from datetime import date


def _txn(*, description: str, reference: str | None, source: str) -> Transaction:
    return Transaction(
        date=date(2024, 4, 1),
        amount=Decimal("100"),
        description=description,
        reference=reference,
        source=source,
        file_name="f.csv",
        raw_row={},
    )


def test_description_similarity_is_case_and_whitespace_insensitive() -> None:
    assert description_similarity("Vendor  Payment", "vendor payment") == 1.0


def test_description_similarity_lower_for_different_text() -> None:
    assert description_similarity("Vendor Payment ABC", "Customer Invoice XYZ") < 0.5


def test_candidate_similarity_short_circuits_on_exact_reference_match() -> None:
    bank = _txn(description="NEFT TRANSFER XYZ CORP", reference="N123456", source="bank")
    ledger = _txn(description="Completely unrelated text", reference="n123456", source="ledger")

    assert candidate_similarity(bank, ledger) == 1.0


def test_candidate_similarity_falls_back_to_description_when_no_reference_match() -> None:
    bank = _txn(description="NEFT TRANSFER VENDOR ABC", reference="N123456", source="bank")
    ledger = _txn(description="Vendor Payment - ABC Supplies", reference="N999999", source="ledger")

    score = candidate_similarity(bank, ledger)
    assert 0.0 < score < 1.0
