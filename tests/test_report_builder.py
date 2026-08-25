from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pandas as pd

from app.ai_matching.models import AIMatchedPair, AIMatchResult
from app.anomaly.models import AnomalyFlag, AnomalyResult
from app.matching.models import MatchedPair, MatchResult
from app.report.builder import build_report
from app.schema import Transaction


def _txn(*, source: str, amount: str, day: int, description: str = "x") -> Transaction:
    return Transaction(
        date=date(2024, 4, day),
        amount=Decimal(amount),
        description=description,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_build_report_produces_four_sheets_with_expected_rows() -> None:
    bank_matched = _txn(source="bank", amount="1000", day=1, description="Vendor Payment")
    ledger_matched = _txn(source="ledger", amount="1000", day=1, description="Vendor Payment")
    match_result = MatchResult(
        matched=[
            MatchedPair(
                bank_transaction=bank_matched,
                ledger_transaction=ledger_matched,
                rule="exact_amount_date",
            )
        ],
        unmatched_bank=[],
        unmatched_ledger=[],
    )

    ai_bank = _txn(source="bank", amount="998", day=2, description="NEFT vendor")
    ai_ledger = _txn(source="ledger", amount="1000", day=2, description="Vendor payment")
    leftover_bank = _txn(source="bank", amount="42", day=5, description="unresolved")
    ai_match_result = AIMatchResult(
        ai_matched=[
            AIMatchedPair(
                bank_transaction=ai_bank,
                ledger_transaction=ai_ledger,
                confidence=0.9,
                reasoning="Same vendor, fee explains the gap.",
            )
        ],
        unmatched_bank=[leftover_bank],
        unmatched_ledger=[],
    )

    flagged = _txn(source="bank", amount="49500", day=1, description="Consulting Fee")
    anomaly_result = AnomalyResult(
        flags=[
            AnomalyFlag(
                rule="just_below_approval_threshold",
                transactions=[flagged],
                reason="Amount is just below the 50000 threshold.",
            )
        ]
    )

    report_bytes = build_report(match_result, ai_match_result, anomaly_result)

    sheets = pd.read_excel(io.BytesIO(report_bytes), sheet_name=None)
    assert set(sheets.keys()) == {"Matched", "AI Matched", "Unmatched", "Anomalies"}

    assert len(sheets["Matched"]) == 1
    assert sheets["Matched"].iloc[0]["rule"] == "exact_amount_date"
    assert sheets["Matched"].iloc[0]["bank_amount"] == 1000.0

    assert len(sheets["AI Matched"]) == 1
    assert sheets["AI Matched"].iloc[0]["confidence"] == 0.9
    assert "fee" in sheets["AI Matched"].iloc[0]["reasoning"]

    assert len(sheets["Unmatched"]) == 1
    assert sheets["Unmatched"].iloc[0]["side"] == "bank"
    assert sheets["Unmatched"].iloc[0]["txn_amount"] == 42.0

    assert len(sheets["Anomalies"]) == 1
    assert sheets["Anomalies"].iloc[0]["rule"] == "just_below_approval_threshold"
    assert sheets["Anomalies"].iloc[0]["group_id"] == "A0001"


def test_build_report_handles_empty_result_sets() -> None:
    match_result = MatchResult(matched=[], unmatched_bank=[], unmatched_ledger=[])
    ai_match_result = AIMatchResult(ai_matched=[], unmatched_bank=[], unmatched_ledger=[])
    anomaly_result = AnomalyResult(flags=[])

    report_bytes = build_report(match_result, ai_match_result, anomaly_result)
    sheets = pd.read_excel(io.BytesIO(report_bytes), sheet_name=None)

    assert set(sheets.keys()) == {"Matched", "AI Matched", "Unmatched", "Anomalies"}
    for df in sheets.values():
        assert len(df) == 0


def test_anomaly_flag_with_two_transactions_produces_two_rows_sharing_group_id() -> None:
    txn_1 = _txn(source="bank", amount="5000", day=1)
    txn_2 = _txn(source="bank", amount="5000", day=2)
    anomaly_result = AnomalyResult(
        flags=[AnomalyFlag(rule="duplicate_payment", transactions=[txn_1, txn_2], reason="possible duplicate")]
    )

    report_bytes = build_report(
        MatchResult(matched=[], unmatched_bank=[], unmatched_ledger=[]),
        AIMatchResult(ai_matched=[], unmatched_bank=[], unmatched_ledger=[]),
        anomaly_result,
    )

    anomalies = pd.read_excel(io.BytesIO(report_bytes), sheet_name="Anomalies")
    assert len(anomalies) == 2
    assert anomalies["group_id"].nunique() == 1
