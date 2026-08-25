from __future__ import annotations

import io

import pandas as pd

from app.ai_matching.models import AIMatchResult
from app.anomaly.models import AnomalyResult
from app.matching.models import MatchResult
from app.schema import Transaction


def _txn_fields(txn: Transaction, *, prefix: str) -> dict:
    return {
        f"{prefix}_date": txn.date.isoformat(),
        f"{prefix}_amount": float(txn.amount),
        f"{prefix}_description": txn.description,
        f"{prefix}_reference": txn.reference,
        f"{prefix}_file_name": txn.file_name,
    }


def _matched_sheet(match_result: MatchResult) -> pd.DataFrame:
    rows = []
    for pair in match_result.matched:
        # `corroboration` is what a reviewer should scan first: rows marked
        # amount_and_date_only rest on the figures alone, which is where two
        # unrelated payments of the same size on the same day would be paired.
        row = {
            "rule": pair.rule,
            "corroboration": pair.corroboration,
            "similarity": pair.similarity,
        }
        row.update(_txn_fields(pair.bank_transaction, prefix="bank"))
        row.update(_txn_fields(pair.ledger_transaction, prefix="ledger"))
        rows.append(row)
    return pd.DataFrame(rows)


def _ai_matched_sheet(ai_match_result: AIMatchResult) -> pd.DataFrame:
    rows = []
    for pair in ai_match_result.ai_matched:
        row = {"confidence": pair.confidence, "reasoning": pair.reasoning}
        row.update(_txn_fields(pair.bank_transaction, prefix="bank"))
        row.update(_txn_fields(pair.ledger_transaction, prefix="ledger"))
        rows.append(row)
    return pd.DataFrame(rows)


def _unmatched_sheet(ai_match_result: AIMatchResult) -> pd.DataFrame:
    rows = []
    for txn in ai_match_result.unmatched_bank:
        rows.append({"side": "bank", **_txn_fields(txn, prefix="txn")})
    for txn in ai_match_result.unmatched_ledger:
        rows.append({"side": "ledger", **_txn_fields(txn, prefix="txn")})
    return pd.DataFrame(rows)


def _anomalies_sheet(anomaly_result: AnomalyResult) -> pd.DataFrame:
    rows = []
    for i, flag in enumerate(anomaly_result.flags):
        group_id = f"A{i + 1:04d}"
        for txn in flag.transactions:
            rows.append(
                {
                    "group_id": group_id,
                    "rule": flag.rule,
                    "reason": flag.reason,
                    "source": txn.source,
                    **_txn_fields(txn, prefix="txn"),
                }
            )
    return pd.DataFrame(rows)


_SHEET_COLUMNS = {
    "Matched": [
        "rule",
        "corroboration",
        "similarity",
        "bank_date",
        "bank_amount",
        "bank_description",
        "bank_reference",
        "bank_file_name",
        "ledger_date",
        "ledger_amount",
        "ledger_description",
        "ledger_reference",
        "ledger_file_name",
    ],
    "AI Matched": [
        "confidence",
        "reasoning",
        "bank_date",
        "bank_amount",
        "bank_description",
        "bank_reference",
        "bank_file_name",
        "ledger_date",
        "ledger_amount",
        "ledger_description",
        "ledger_reference",
        "ledger_file_name",
    ],
    "Unmatched": ["side", "txn_date", "txn_amount", "txn_description", "txn_reference", "txn_file_name"],
    "Anomalies": [
        "group_id",
        "rule",
        "reason",
        "source",
        "txn_date",
        "txn_amount",
        "txn_description",
        "txn_reference",
        "txn_file_name",
    ],
}


def build_report(
    match_result: MatchResult,
    ai_match_result: AIMatchResult,
    anomaly_result: AnomalyResult,
) -> bytes:
    """Builds the audit-ready reconciliation report as one .xlsx workbook, four tabs.

    Every row carries the rule that fired (Matched), the AI's confidence + reasoning
    (AI Matched), or the anomaly rule + reason (Anomalies) — so a CA can see *why* a
    row landed in its bucket without leaving the sheet.
    """
    sheets = {
        "Matched": _matched_sheet(match_result),
        "AI Matched": _ai_matched_sheet(ai_match_result),
        "Unmatched": _unmatched_sheet(ai_match_result),
        "Anomalies": _anomalies_sheet(anomaly_result),
    }

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            columns = _SHEET_COLUMNS[sheet_name]
            df = df.reindex(columns=columns) if not df.empty else pd.DataFrame(columns=columns)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    return buffer.getvalue()
