from __future__ import annotations

import pytest

from app.ingestion.bank_templates.bob import BankOfBarodaTemplate
from app.ingestion.bank_templates.bob_narration import parse_narration

# Narrations taken from a Bank of Baroda current-account statement, as assembled by
# the layout-table parser.


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
            "NEFT/HDFC0000456/NFT8340/AMAZON SELLER SERVICES PVT LTD",
            "neft",
            "NFT8340",
            "AMAZON SELLER SERVICES PVT LTD",
        ),
        (
            "RTGS/BARB0000890/RTG3300/MAHESH ELECTRICALS PVT LTD",
            "rtgs",
            "RTG3300",
            "MAHESH ELECTRICALS PVT LTD",
        ),
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


def test_cheque_deposited_names_no_reference() -> None:
    """The cheque number lives in Bank of Baroda's separate Cheque No column, not
    in this narration — the template must not invent one from the cell text."""
    parsed = parse_narration("CHEQUE DEPOSITED")
    assert parsed.kind == "cheque"
    assert parsed.reference is None


@pytest.mark.parametrize(
    "narration",
    [
        "ATM CASH WDL KORAMANGALA BLR",
        "SMS ALERT CHG Q2 INCL GST",
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
    template = BankOfBarodaTemplate()
    assert template.extra_references("IMPS/503027177157/SUNIL ENTERPRISES") == ["503027177157"]
    assert template.extra_references("SMS ALERT CHG Q2 INCL GST") == []
    assert template.counterparty("RTGS/BARB0000890/RTG3300/MAHESH ELECTRICALS PVT LTD") == (
        "MAHESH ELECTRICALS PVT LTD"
    )
