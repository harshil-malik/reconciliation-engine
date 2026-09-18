from __future__ import annotations

import pytest

from app.ingestion.bank_templates.sbi import SBIBankTemplate
from app.ingestion.bank_templates.sbi_narration import parse_narration

# Narrations taken from an SBI current-account statement, as assembled by the
# layout-table parser (continuation lines already joined onto the row).


@pytest.mark.parametrize(
    "narration,kind,reference,counterparty",
    [
        (
            "TO TRANSFER-UPI/DR/500584440248/RAJESH KUMAR TRADE/UTIB/rajeshtraders@okaxis/INV2241",
            "upi",
            "500584440248",
            "RAJESH KUMAR TRADE",
        ),
        (
            "BY TRANSFER-NEFT*HDFC0000456*NFT8340*AMAZON SELLER SERVICES PVT LTD",
            "neft",
            "NFT8340",
            "AMAZON SELLER SERVICES PVT LTD",
        ),
        (
            "TO TRANSFER-RTGS*BARB0000890*RTG3300*MAHESH ELECTRICALS PVT LTD",
            "rtgs",
            "RTG3300",
            "MAHESH ELECTRICALS PVT LTD",
        ),
        ("TO TRANSFER-IMPS/503027177157/SUNIL ENTERPRISES", "imps", "503027177157", "SUNIL ENTERPRISES"),
    ],
)
def test_extracts_id_and_counterparty(narration, kind, reference, counterparty) -> None:
    parsed = parse_narration(narration)
    assert parsed.kind == kind
    assert parsed.reference == reference
    assert parsed.counterparty == counterparty


def test_salary_credit_gives_a_reference_but_no_counterparty() -> None:
    parsed = parse_narration("BY TRANSFER-INB SALARY JUL/SAL4521")
    assert parsed.reference == "SAL4521"
    assert parsed.counterparty is None


def test_cheque_reference_is_recovered() -> None:
    assert parse_narration("BY CLEARING-CHEQUE NO CHQ0452").reference == "CHQ0452"


@pytest.mark.parametrize(
    "narration",
    [
        "TO ATM WDL-ATM CASH/S1CA1234/KORAMANGALA BLR",
        "TO CHARGES-SMS ALERT CHARGES Q2 INCL GST",
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
    template = SBIBankTemplate()
    assert template.extra_references("TO TRANSFER-IMPS/503027177157/SUNIL ENTERPRISES") == [
        "503027177157"
    ]
    assert template.extra_references("TO CHARGES-SMS ALERT CHARGES Q2 INCL GST") == []
    assert template.counterparty("TO TRANSFER-IMPS/503027177157/SUNIL ENTERPRISES") == (
        "SUNIL ENTERPRISES"
    )
