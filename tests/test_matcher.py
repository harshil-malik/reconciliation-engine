from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.matching.matcher import match
from app.schema import Transaction


def _txn(
    *,
    source: str,
    amount: str,
    day: int,
    description: str = "generic transaction",
    reference: str | None = None,
) -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        reference=reference,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_exact_amount_and_date_match() -> None:
    bank = _txn(source="bank", amount="1000", day=1)
    ledger = _txn(source="ledger", amount="1000", day=1)

    result = match([bank], [ledger])

    assert len(result.matched) == 1
    assert result.matched[0].rule == "exact_amount_same_date"
    assert result.matched[0].bank_transaction.id == bank.id
    assert result.matched[0].ledger_transaction.id == ledger.id
    assert result.unmatched_bank == []
    assert result.unmatched_ledger == []


def test_matches_within_date_tolerance_window() -> None:
    bank = _txn(source="bank", amount="1000", day=1)
    ledger = _txn(source="ledger", amount="1000", day=3)  # 2 days later, within default tolerance

    result = match([bank], [ledger])

    assert len(result.matched) == 1
    # Reported as a near-date match, not an exact-date one: the report is an audit
    # document, so the rule name has to say which condition actually fired.
    assert result.matched[0].rule == "exact_amount_near_date"


def test_no_match_when_outside_date_tolerance() -> None:
    bank = _txn(source="bank", amount="1000", day=1)
    ledger = _txn(source="ledger", amount="1000", day=10)  # 9 days later

    result = match([bank], [ledger])

    assert result.matched == []
    assert result.unmatched_bank == [bank]
    assert result.unmatched_ledger == [ledger]


def test_no_match_when_amount_differs() -> None:
    bank = _txn(source="bank", amount="1000", day=1)
    ledger = _txn(source="ledger", amount="999.99", day=1)

    result = match([bank], [ledger])

    assert result.matched == []
    assert result.unmatched_bank == [bank]
    assert result.unmatched_ledger == [ledger]


def test_ambiguous_candidates_disambiguated_by_exact_reference() -> None:
    bank = _txn(source="bank", amount="500", day=1, description="NEFT XYZ", reference="REF001")
    right_ledger = _txn(
        source="ledger", amount="500", day=1, description="unrelated text", reference="REF001"
    )
    wrong_ledger = _txn(
        source="ledger", amount="500", day=1, description="also unrelated", reference="REF002"
    )

    result = match([bank], [right_ledger, wrong_ledger])

    assert len(result.matched) == 1
    assert result.matched[0].rule == "exact_amount_same_date_fuzzy_desc"
    assert result.matched[0].similarity == 1.0
    assert result.matched[0].ledger_transaction.id == right_ledger.id
    assert result.unmatched_ledger == [wrong_ledger]


def test_ambiguous_candidates_disambiguated_by_description_similarity() -> None:
    bank = _txn(source="bank", amount="750", day=1, description="Vendor Payment ABC Supplies")
    right_ledger = _txn(
        source="ledger", amount="750", day=1, description="Vendor Payment ABC Supplies"
    )
    wrong_ledger = _txn(
        source="ledger", amount="750", day=1, description="Totally different narration text"
    )

    result = match([bank], [right_ledger, wrong_ledger])

    assert len(result.matched) == 1
    assert result.matched[0].ledger_transaction.id == right_ledger.id
    assert result.matched[0].rule == "exact_amount_same_date_fuzzy_desc"


def test_ambiguous_candidates_below_threshold_stay_unmatched() -> None:
    bank = _txn(source="bank", amount="500", day=1, description="AAAAAA")
    ledger_1 = _txn(source="ledger", amount="500", day=1, description="BBBBBB")
    ledger_2 = _txn(source="ledger", amount="500", day=1, description="CCCCCC")

    result = match([bank], [ledger_1, ledger_2])

    assert result.matched == []
    assert result.unmatched_bank == [bank]
    assert set(t.id for t in result.unmatched_ledger) == {ledger_1.id, ledger_2.id}


def test_duplicate_amounts_matched_one_to_one_not_double_matched() -> None:
    bank_1 = _txn(source="bank", amount="500", day=1, description="payment one")
    bank_2 = _txn(source="bank", amount="500", day=1, description="payment two")
    ledger_1 = _txn(source="ledger", amount="500", day=1, description="payment one")
    ledger_2 = _txn(source="ledger", amount="500", day=1, description="payment two")

    result = match([bank_1, bank_2], [ledger_1, ledger_2])

    assert len(result.matched) == 2
    matched_ledger_ids = {pair.ledger_transaction.id for pair in result.matched}
    assert matched_ledger_ids == {ledger_1.id, ledger_2.id}
    # each bank row paired with the ledger row that actually matches its description
    for pair in result.matched:
        assert pair.bank_transaction.description == pair.ledger_transaction.description


def test_extra_unmatched_rows_on_either_side_are_reported() -> None:
    bank = _txn(source="bank", amount="1000", day=1)
    extra_bank = _txn(source="bank", amount="42", day=5)
    ledger = _txn(source="ledger", amount="1000", day=1)
    extra_ledger = _txn(source="ledger", amount="77", day=6)

    result = match([bank, extra_bank], [ledger, extra_ledger])

    assert len(result.matched) == 1
    assert result.unmatched_bank == [extra_bank]
    assert result.unmatched_ledger == [extra_ledger]


def test_match_backed_only_by_amount_and_date_is_labelled_for_review() -> None:
    """The GLOBEX/Initech case: two unrelated payments of the same size on the same
    day are indistinguishable from a real pair on figures alone. The match is still
    made — no similarity threshold separates these from genuine ones, so refusing
    them would lose good matches — but the report says what it rests on."""
    bank = _txn(source="bank", amount="-20000", day=2, description="NEFT TO GLOBEX LTD")
    ledger = _txn(source="ledger", amount="-20000", day=2, description="Payment to Initech Pvt Ltd")

    pair = match([bank], [ledger]).matched[0]

    assert pair.corroboration == "amount_and_date_only"
    assert pair.similarity is not None


def test_matching_reference_is_recorded_as_the_strongest_evidence() -> None:
    bank = _txn(source="bank", amount="-15075", day=1, description="NEFT VENDOR PAYMENT", reference="N123")
    ledger = _txn(source="ledger", amount="-15075", day=1, description="Totally different wording", reference="N123")

    pair = match([bank], [ledger]).matched[0]

    assert pair.corroboration == "reference"


def test_corresponding_descriptions_are_recorded_as_corroboration() -> None:
    bank = _txn(source="bank", amount="-40000", day=25, description="NEFT-OFFICE RENT JULY")
    ledger = _txn(source="ledger", amount="-40000", day=25, description="Office rent - July")

    pair = match([bank], [ledger]).matched[0]

    assert pair.corroboration == "description"
