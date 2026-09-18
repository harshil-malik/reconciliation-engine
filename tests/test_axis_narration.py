from __future__ import annotations

import pytest

from app.ingestion.bank_templates.axis import AxisBankTemplate
from app.ingestion.bank_templates.axis_narration import parse_narration

# Narrations taken from an Axis Bank current-account statement, as assembled by the
# layout-table parser.


@pytest.mark.parametrize(
    "narration,kind,reference,counterparty",
    [
        ("UPI/P2A/500584440248/RAJESH KUMAR TRADERS", "upi", "500584440248", "RAJESH KUMAR TRADERS"),
        ("UPI/P2M/501580421755/SWIGGY INSTAMART", "upi", "501580421755", "SWIGGY INSTAMART"),
        ("NEFT/NFT8340/AMAZON SELLER SERVICES PVT LTD", "neft", "NFT8340", "AMAZON SELLER SERVICES PVT LTD"),
        ("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD", "rtgs", "RTG3300", "MAHESH ELECTRICALS PVT LTD"),
        ("IMPS/503027177157/SUNIL ENTERPRISES", "imps", "503027177157", "SUNIL ENTERPRISES"),
    ],
)
def test_extracts_id_and_counterparty(narration, kind, reference, counterparty) -> None:
    parsed = parse_narration(narration)
    assert parsed.kind == kind
    assert parsed.reference == reference
    assert parsed.counterparty == counterparty


def test_salary_posted_as_a_plain_neft_still_reads() -> None:
    """Axis posts salary as an ordinary NEFT credit rather than a distinct
    narration prefix, so it goes through the generic NEFT pattern."""
    parsed = parse_narration("NEFT/SAL4521/SALARY JUL")
    assert parsed.kind == "neft"
    assert parsed.reference == "SAL4521"


def test_cheque_reference_is_recovered() -> None:
    assert parse_narration("CHQ DEP-CHQ0452").reference == "CHQ0452"


@pytest.mark.parametrize(
    "narration",
    [
        "ATM-CASH/KORAMANGALA/BLR",
        "CONS CHG SMS ALERT Q2",
        "",
    ],
)
def test_returns_nothing_rather_than_guessing(narration) -> None:
    """ATM withdrawals and bank charges name no counterparty. Inventing one would
    manufacture matching signal where there is none."""
    parsed = parse_narration(narration)
    assert parsed.reference is None
    assert parsed.counterparty is None


def test_template_exposes_narration_ids_for_matching() -> None:
    template = AxisBankTemplate()
    assert template.extra_references("IMPS/503027177157/SUNIL ENTERPRISES") == ["503027177157"]
    assert template.extra_references("CONS CHG SMS ALERT Q2") == []
    assert template.counterparty("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD") == (
        "MAHESH ELECTRICALS PVT LTD"
    )
