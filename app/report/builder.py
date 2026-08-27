from __future__ import annotations

import io

import pandas as pd

from app.ai_matching.models import AIMatchResult
from app.anomaly.models import AnomalyResult
from app.matching.models import MatchResult
from app.report.classify import classify_unmatched, match_issue, reconciliation_summary
from app.schema import Transaction


def _txn_fields(txn: Transaction, *, prefix: str) -> dict:
    return {
        f"{prefix}_date": txn.date.isoformat(),
        f"{prefix}_amount": float(txn.amount),
        f"{prefix}_description": txn.description,
        f"{prefix}_reference": txn.reference,
        f"{prefix}_file_name": txn.file_name,
        # Where in that file the row was read from, so a reviewer working in the
        # workbook rather than the browser can still get back to the source line.
        f"{prefix}_source": txn.source_ref.label if txn.source_ref else "",
        f"{prefix}_source_text": txn.source_ref.text if txn.source_ref else "",
    }


def _pair_row(pair) -> dict:
    date_gap = abs((pair.bank_transaction.date - pair.ledger_transaction.date).days)
    amount_gap = abs(pair.bank_transaction.amount - pair.ledger_transaction.amount)
    row = {
        "rule": pair.rule,
        "corroboration": pair.corroboration,
        "date_gap_days": date_gap,
        "amount_difference": float(amount_gap),
        "similarity": pair.similarity,
    }
    row.update(_txn_fields(pair.bank_transaction, prefix="bank"))
    row.update(_txn_fields(pair.ledger_transaction, prefix="ledger"))
    return row


def _unmatched_rows(
    transactions: list[Transaction], counterparties: list[Transaction]
) -> list[dict]:
    rows = []
    for txn in transactions:
        rows.append(
            {
                "why_unmatched": classify_unmatched(txn, counterparties),
                **_txn_fields(txn, prefix="txn"),
            }
        )
    return rows


_TRANSACTION_COLUMNS = [
    "{p}_date",
    "{p}_amount",
    "{p}_description",
    "{p}_reference",
    "{p}_file_name",
    "{p}_source",
    "{p}_source_text",
]


def _pair_columns(prefix_first: list[str]) -> list[str]:
    columns = list(prefix_first)
    for prefix in ("bank", "ledger"):
        columns += [c.format(p=prefix) for c in _TRANSACTION_COLUMNS]
    return columns


_SHEET_COLUMNS = {
    "Summary": ["item", "count", "amount", "note"],
    "Matched": _pair_columns(
        ["rule", "corroboration", "date_gap_days", "amount_difference", "similarity"]
    ),
    "Review - Amount": _pair_columns(
        ["issue", "rule", "corroboration", "date_gap_days", "amount_difference", "similarity"]
    ),
    "Review - Date": _pair_columns(
        ["issue", "rule", "corroboration", "date_gap_days", "amount_difference", "similarity"]
    ),
    "Review - Weak Evidence": _pair_columns(
        ["issue", "rule", "corroboration", "date_gap_days", "amount_difference", "similarity"]
    ),
    "AI Matched": _pair_columns(["confidence", "reasoning"]),
    "Unmatched - Bank": ["why_unmatched"] + [c.format(p="txn") for c in _TRANSACTION_COLUMNS],
    "Unmatched - Ledger": ["why_unmatched"] + [c.format(p="txn") for c in _TRANSACTION_COLUMNS],
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
    """Build the reconciliation workbook, organised by what a reviewer must DO.

    The tabs are split by required action rather than by which stage produced a row.
    An earlier layout put every pairing in one "Matched" tab and every leftover in
    one "Unmatched" tab, which meant a clean exact match sat beside one resting on
    figures alone, and an expected bank charge sat beside a genuine discrepancy —
    two readers counted the same file differently because nothing said which was
    which.

    Summary                the balancing proof: is every rupee of difference accounted for
    Matched                every pairing, with its date gap, amount gap and evidence
    Review - Amount        pairings where the two sides disagree about the money
    Review - Date          pairings booked on different dates
    Review - Weak Evidence pairings resting on the figures alone
    AI Matched         model-asserted pairs, kept separate as they are the least certain
    Unmatched - Bank   on the statement, not in the books, each with a reason
    Unmatched - Ledger in the books, not on the statement, each with a reason
    Anomalies          risk flags, which may also appear above (Stage 3 sees everything)
    """
    matched = match_result.matched
    bank_txns = [p.bank_transaction for p in matched] + ai_match_result.unmatched_bank + [
        p.bank_transaction for p in ai_match_result.ai_matched
    ]
    ledger_txns = [p.ledger_transaction for p in matched] + ai_match_result.unmatched_ledger + [
        p.ledger_transaction for p in ai_match_result.ai_matched
    ]

    # One tab per reason a pairing needs attention, because "show me everything
    # where the amounts disagree" is a different job from "show me the timing
    # differences" and a reviewer does them separately. A pairing that fails on more
    # than one count appears in each relevant tab — the alternative, filing it under
    # a single "primary" reason, leaves the other tab quietly incomplete — and its
    # `issue` column always states the full picture.
    amount_review, date_review, weak_review = [], [], []
    for pair in matched:
        issue = match_issue(pair)
        if not issue:
            continue
        row = {"issue": issue, **_pair_row(pair)}
        if row["amount_difference"]:
            amount_review.append(row)
        if row["date_gap_days"]:
            date_review.append(row)
        if pair.corroboration == "amount_and_date_only":
            weak_review.append(row)

    ai_rows = []
    for pair in ai_match_result.ai_matched:
        row = {"confidence": pair.confidence, "reasoning": pair.reasoning}
        row.update(_txn_fields(pair.bank_transaction, prefix="bank"))
        row.update(_txn_fields(pair.ledger_transaction, prefix="ledger"))
        ai_rows.append(row)

    anomaly_rows = []
    for index, flag in enumerate(anomaly_result.flags):
        group_id = f"A{index + 1:04d}"
        for txn in flag.transactions:
            anomaly_rows.append(
                {
                    "group_id": group_id,
                    "rule": flag.rule,
                    "reason": flag.reason,
                    "source": txn.source,
                    **_txn_fields(txn, prefix="txn"),
                }
            )

    sheets = {
        "Summary": pd.DataFrame(
            reconciliation_summary(
                bank_txns,
                ledger_txns,
                matched,
                ai_match_result.unmatched_bank,
                ai_match_result.unmatched_ledger,
            )
        ),
        "Matched": pd.DataFrame([_pair_row(p) for p in matched]),
        "Review - Amount": pd.DataFrame(amount_review),
        "Review - Date": pd.DataFrame(date_review),
        "Review - Weak Evidence": pd.DataFrame(weak_review),
        "AI Matched": pd.DataFrame(ai_rows),
        "Unmatched - Bank": pd.DataFrame(
            _unmatched_rows(ai_match_result.unmatched_bank, ledger_txns)
        ),
        "Unmatched - Ledger": pd.DataFrame(
            _unmatched_rows(ai_match_result.unmatched_ledger, bank_txns)
        ),
        "Anomalies": pd.DataFrame(anomaly_rows),
    }

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            columns = _SHEET_COLUMNS[sheet_name]
            frame = (
                frame.reindex(columns=columns)
                if not frame.empty
                else pd.DataFrame(columns=columns)
            )
            frame.to_excel(writer, sheet_name=sheet_name, index=False)

    return buffer.getvalue()
