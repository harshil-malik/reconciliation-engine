from __future__ import annotations

import pytest

from app.ingestion.bank_templates.pnb import PNBBankTemplate
from app.ingestion.bank_templates.pnb_narration import parse_narration

# Narrations taken from a PNB current-account statement, as assembled by the
# layout-table parser.


@pytest.mark.parametrize(
    "narration,kind,reference,counterparty",
    [
        ("UPI/500584440248/RAJESH KUMAR TRADERS", "upi", "500584440248", "RAJESH KUMAR TRADERS"),
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


def test_salary_credit_gives_a_reference_but_no_counterparty() -> None:
    parsed = parse_narration("SALARY CREDIT SAL4521")
    assert parsed.reference == "SAL4521"
    assert parsed.counterparty is None


def test_cheque_reference_is_recovered() -> None:
    assert parse_narration("CHEQUE DEPOSIT CHQ0452").reference == "CHQ0452"


@pytest.mark.parametrize(
    "narration",
    [
        "ATM CASH WITHDRAWAL KORAMANGALA",
        "SMS ALERT CHARGES Q2",
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
    template = PNBBankTemplate()
    assert template.extra_references("IMPS/503027177157/SUNIL ENTERPRISES") == ["503027177157"]
    assert template.extra_references("SMS ALERT CHARGES Q2") == []
    assert template.counterparty("NEFT/NFT1520/ACME LTD") == "ACME LTD"
