from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.matching.models import MatchedPair
from app.matching.tolerance import fee_gap_is_plausible
from app.schema import Transaction

# Narration fragments that identify a bank-originated posting. These are the
# classic reconciling items on the bank side: the bank levied or paid something the
# client has not booked yet, so an unmatched row here is expected rather than a
# problem to chase.
_BANK_ORIGINATED = (
    ("chrg", "bank charge"),
    ("charge", "bank charge"),
    ("int.pd", "interest paid by bank"),
    ("int pd", "interest paid by bank"),
    ("interest", "interest"),
    ("gst", "tax debited by bank"),
    ("tds", "tax deducted at source"),
)


def match_issue(pair: MatchedPair) -> Optional[str]:
    """What, if anything, a reviewer should look at on this matched pair.

    None means the pair needs no attention: same amount, same date, and something
    beyond the figures tying the two rows together.
    """
    date_gap = abs((pair.bank_transaction.date - pair.ledger_transaction.date).days)
    amount_gap = abs(pair.bank_transaction.amount - pair.ledger_transaction.amount)

    issues: list[str] = []
    if amount_gap:
        issues.append(f"amount differs by {amount_gap}")
    if date_gap:
        issues.append(f"booked {date_gap} day(s) apart")
    if pair.corroboration == "amount_and_date_only":
        # The pairing rests on figures alone, so an unrelated payment of the same
        # size on the same day would look identical to a genuine match.
        issues.append("no shared reference or payee — matched on figures alone")

    return "; ".join(issues) if issues else None


def classify_unmatched(
    txn: Transaction,
    counterparties: list[Transaction],
    *,
    date_tolerance_days: int = 3,
) -> str:
    """Say why this row found no partner, in terms a reviewer can act on.

    "Unmatched" alone forces the reader to work out whether a row is an expected
    reconciling item, a near miss the rules were too strict for, or a genuine
    discrepancy — which is the difference between filing it and chasing it.
    """
    description = (txn.description or "").lower()
    for fragment, label in _BANK_ORIGINATED:
        if fragment in description:
            side = "not yet booked in the ledger" if txn.source == "bank" else "not on the statement"
            return f"{label} — {side}"

    same_amount = [c for c in counterparties if c.amount == txn.amount]
    if len(same_amount) > 1:
        # Several rows share the amount and the rules could not choose between them,
        # so nothing was asserted. A human can usually tell them apart instantly.
        return (
            f"ambiguous — {len(same_amount)} rows on the other side share this amount; "
            "none chosen rather than guessing"
        )
    if len(same_amount) == 1:
        gap = abs((txn.date - same_amount[0].date).days)
        return (
            f"same amount exists on the other side but is dated {same_amount[0].date} "
            f"({gap} days away, outside the {date_tolerance_days}-day window)"
        )

    near = [c for c in counterparties if fee_gap_is_plausible(txn, c)]
    if near:
        closest = min(near, key=lambda c: abs((txn.date - c.date).days))
        return (
            f"no exact amount; closest is {closest.amount} on {closest.date} "
            f"({closest.description[:40]})"
        )

    if txn.source == "ledger":
        kind = "receipt not yet on the statement" if txn.amount > 0 else "payment not yet presented"
        return f"no counterpart — {kind}"
    return "no counterpart on the ledger side"


def reconciliation_summary(
    bank_txns: list[Transaction],
    ledger_txns: list[Transaction],
    matched: list[MatchedPair],
    unmatched_bank: list[Transaction],
    unmatched_ledger: list[Transaction],
) -> list[dict]:
    """The reconciliation stated as a balancing proof, the way a CA presents one.

    Every rupee of difference between the two sets of books must be attributable to
    a named group of items. If "unexplained" is anything other than zero, something
    was mismatched, double-counted or dropped — regardless of how tidy the rest of
    the workbook looks.
    """
    bank_total = sum((t.amount for t in bank_txns), Decimal("0"))
    ledger_total = sum((t.amount for t in ledger_txns), Decimal("0"))
    pair_differences = sum(
        (p.bank_transaction.amount - p.ledger_transaction.amount for p in matched),
        Decimal("0"),
    )
    bank_only = sum((t.amount for t in unmatched_bank), Decimal("0"))
    ledger_only = sum((t.amount for t in unmatched_ledger), Decimal("0"))
    explained = pair_differences + bank_only - ledger_only
    difference = bank_total - ledger_total

    def row(item: str, count: object, amount: object, note: str = "") -> dict:
        return {"item": item, "count": count, "amount": amount, "note": note}

    return [
        row("Bank transactions", len(bank_txns), float(bank_total), "net movement on the statement"),
        row("Ledger transactions", len(ledger_txns), float(ledger_total), "net movement in the books"),
        row("Difference to explain", "", float(difference), "bank net less ledger net"),
        row("", "", "", ""),
        row("Matched", len(matched), "", "paired between the two documents"),
        row("  of which need review", sum(1 for p in matched if match_issue(p)), "",
            "see the Needs Review tab"),
        row("Bank-only items", len(unmatched_bank), float(bank_only),
            "on the statement, not in the ledger"),
        row("Ledger-only items", len(unmatched_ledger), float(ledger_only),
            "in the ledger, not on the statement"),
        row("", "", "", ""),
        row("Explained by matched-pair differences", "", float(pair_differences), ""),
        row("Explained by bank-only items", "", float(bank_only), ""),
        row("Less ledger-only items", "", float(-ledger_only), ""),
        row("Total explained", "", float(explained), ""),
        row(
            "UNEXPLAINED",
            "",
            float(difference - explained),
            "must be 0.00 — anything else means a row was mismatched, "
            "double-counted or dropped",
        ),
    ]
