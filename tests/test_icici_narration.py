from __future__ import annotations

import pytest

from app.ingestion.bank_templates.icici import ICICIBankTemplate
from app.ingestion.bank_templates.icici_narration import parse_narration

# Narrations taken from an ICICI current-account statement, as assembled by the
# layout-table parser. ICICI's own Cheque Number column reads "-" on every row but
# a real cheque transaction, so this narration id is the only reference most rows
# carry.


@pytest.mark.parametrize(
    "narration,kind,reference,counterparty",
    [
        (
            "UPI/500584440248/INV2241/RAJESH KUM/UTIB/rajeshtrader",
            "upi",
            "500584440248",
            "RAJESH KUM",
        ),
        ("NEFT-NFT8340-AMAZON SELLER SERVICES PVT LTD", "neft", "NFT8340", "AMAZON SELLER SERVICES PVT LTD"),
        ("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD", "rtgs", "RTG3300", "MAHESH ELECTRICALS PVT LTD"),
        ("MMT/IMPS/503027177157/FREIGHT/SUNIL ENTE", "imps", "503027177157", "SUNIL ENTE"),
    ],
)
def test_extracts_id_and_counterparty(narration, kind, reference, counterparty) -> None:
    parsed = parse_narration(narration)
    assert parsed.kind == kind
    assert parsed.reference == reference
    assert parsed.counterparty == counterparty


def test_salary_gives_a_reference_but_no_counterparty() -> None:
    parsed = parse_narration("SAL-SAL4521-JUL26")
    assert parsed.reference == "SAL4521"
    assert parsed.counterparty is None


def test_cheque_reference_is_recovered() -> None:
    assert parse_narration("CLG/CHQ0452").reference == "CHQ0452"


@pytest.mark.parametrize(
    "narration",
    [
        "ATM/S1CA1234/KORAMANGALA/BLR",
        "BIL/ONL/500204026/SMS ALERT CHG",
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
    template = ICICIBankTemplate()
    assert template.extra_references("MMT/IMPS/503027177157/FREIGHT/SUNIL ENTE") == ["503027177157"]
    assert template.extra_references("BIL/ONL/500204026/SMS ALERT CHG") == []
    assert template.counterparty("RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD") == (
        "MAHESH ELECTRICALS PVT LTD"
    )
