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


def _ref_txn(reference=None, alt_references=None, description="x"):
    from datetime import date
    from decimal import Decimal

    from app.schema import Transaction

    return Transaction(
        date=date(2026, 7, 1),
        amount=Decimal("1"),
        description=description,
        reference=reference,
        alt_references=alt_references or [],
        source="bank",
        file_name="x.pdf",
        raw_row={},
    )


def test_reference_recovered_from_a_narration_still_matches() -> None:
    """The bank printed a placeholder in its reference column and buried the real id
    in the narration; the ledger recorded that id properly."""
    from app.matching.text_similarity import references_match

    bank = _ref_txn(reference=None, alt_references=["900000000001"])
    ledger = _ref_txn(reference="900000000001")

    assert references_match(bank, ledger)


def test_cheque_numbers_match_across_different_zero_padding() -> None:
    from app.matching.text_similarity import references_match

    assert references_match(_ref_txn(alt_references=["412"]), _ref_txn(reference="000412"))


def test_rows_without_any_identifier_do_not_match_on_reference() -> None:
    from app.matching.text_similarity import references_match

    assert not references_match(_ref_txn(), _ref_txn())
