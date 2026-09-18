from __future__ import annotations

import pytest

from app.ingestion.bank_templates.kotak import KotakBankTemplate
from app.ingestion.bank_templates.kotak_narration import parse_narration

# Narrations taken from a Kotak Mahindra Bank current-account statement, as
# assembled by the layout-table parser.


@pytest.mark.parametrize(
    "narration,kind,reference,counterparty",
    [
        (
            "UPI/500584440248/RAJESH KUMAR TRADERS/rajeshtraders@okaxis",
            "upi",
            "500584440248",
            "RAJESH KUMAR TRADERS",
        ),
        (
            "NEFT/NFT8340/AMAZON SELLER SERVICES PVT LTD/HDFC0000456",
            "neft",
            "NFT8340",
            "AMAZON SELLER SERVICES PVT LTD",
        ),
        ("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD", "rtgs", "RTG3300", "MAHESH ELECTRICALS PVT LTD"),
        ("IMPS/503027177157/SUNIL ENTERPRISES", "imps", "503027177157", "SUNIL ENTERPRISES"),
    ],
)
def test_extracts_id_and_counterparty(narration, kind, reference, counterparty) -> None:
    parsed = parse_narration(narration)
    assert parsed.kind == kind
    assert parsed.reference == reference
    assert parsed.counterparty == counterparty


def test_neft_with_no_trailing_ifsc_still_reads() -> None:
    """Not every NEFT/RTGS line trails an IFSC code — the trailing group is
    optional, and its absence must not swallow the last word of the name."""
    parsed = parse_narration("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD")
    assert parsed.counterparty == "MAHESH ELECTRICALS PVT LTD"


def test_salary_gives_a_reference_but_no_counterparty() -> None:
    parsed = parse_narration("SALARY SAL4521")
    assert parsed.reference == "SAL4521"
    assert parsed.counterparty is None


def test_cheque_reference_is_recovered() -> None:
    assert parse_narration("CHQ DEP CHQ0452").reference == "CHQ0452"


@pytest.mark.parametrize(
    "narration",
    [
        "ATM WDL KORAMANGALA BLR",
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
    template = KotakBankTemplate()
    assert template.extra_references("IMPS/503027177157/SUNIL ENTERPRISES") == ["503027177157"]
    assert template.extra_references("SMS ALERT CHARGES Q2") == []
    assert template.counterparty("NEFT/NFT1520/ACME LTD/KKBK0000567") == "ACME LTD"
