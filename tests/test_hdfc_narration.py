from __future__ import annotations

import pytest

from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.bank_templates.hdfc_narration import parse_narration

# Narrations taken from a real HDFC current-account statement. HDFC prints
# "0000000000" in the reference column for most of these while carrying the actual
# transaction id inside the narration — on that statement, 11 of 18 rows held an id
# the ledger also recorded, but only 5 matched through the reference column.


@pytest.mark.parametrize(
    "narration,reference,counterparty",
    [
        ("UPI/P2M/900000000001/SWIFTWAY EXPRESS/Courier chg", "900000000001", "SWIFTWAY EXPRESS"),
        ("UPI/P2A/900000000002/ANIL GUPTA/Rent share", "900000000002", "ANIL GUPTA"),
        ("IMPS-900000000005-DIVYA KAPOOR-Consulting fee", "900000000005", "DIVYA KAPOOR"),
        (
            "NEFT CR:HDFC0R00099920250703/NORTH SUPPLY PVT LTD/INV-1042",
            "HDFC0R00099920250703",
            "NORTH SUPPLY PVT LTD",
        ),
        (
            "RTGS CR:HDFC0R00099920250707/PEAK TRADERS/Advance payment",
            "HDFC0R00099920250707",
            "PEAK TRADERS",
        ),
    ],
)
def test_extracts_id_and_counterparty(narration, reference, counterparty) -> None:
    parsed = parse_narration(narration)
    assert parsed.reference == reference
    assert parsed.counterparty == counterparty


def test_cheque_number_is_recovered_without_its_leading_zeros() -> None:
    """The ledger writes 000412, the statement narration writes it the same way, but
    the two sides disagree about padding often enough to compare on digits."""
    assert parse_narration("CHQ PAID 000412").reference == "412"


def test_nach_gives_a_counterparty_but_no_id() -> None:
    parsed = parse_narration("ACH DR-NACH-OMNI HEALTH INS-Premium")
    assert parsed.reference is None
    assert parsed.counterparty == "OMNI HEALTH INS"


@pytest.mark.parametrize(
    "narration",
    [
        "HDFC CHRG SMS ALERT 062026-072026",
        "HDFC CHRG CASH MGMT 072026",
        "INT.PD:01/04/26 TO 30/06/26",
        "",
    ],
)
def test_returns_nothing_rather_than_guessing(narration) -> None:
    """Bank charges and interest postings name no counterparty. Inventing one would
    manufacture matching signal where there is none."""
    parsed = parse_narration(narration)
    assert parsed.reference is None
    assert parsed.counterparty is None


def test_template_exposes_narration_ids_for_matching() -> None:
    template = HDFCBankTemplate()
    assert template.extra_references("UPI/P2M/900000000001/SWIFTWAY EXPRESS/x") == ["900000000001"]
    assert template.extra_references("HDFC CHRG SMS ALERT 062026-072026") == []
