from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.matching.models import MatchedPair
from app.report.classify import classify_unmatched, match_issue, reconciliation_summary
from app.schema import Transaction


def _txn(source, amount, day, description="ACME Supplies", reference=None):
    return Transaction(
        date=date(2026, 7, day),
        amount=Decimal(amount),
        description=description,
        reference=reference,
        source=source,
        file_name=f"{source}.pdf",
        raw_row={},
    )


def _pair(bank, ledger, corroboration="description"):
    return MatchedPair(
        bank_transaction=bank,
        ledger_transaction=ledger,
        rule="exact_amount_same_date",
        similarity=0.9,
        corroboration=corroboration,
    )


def test_clean_pair_needs_no_review() -> None:
    pair = _pair(_txn("bank", "-1000", 1), _txn("ledger", "-1000", 1))
    assert match_issue(pair) is None


def test_date_and_amount_gaps_are_each_named() -> None:
    pair = _pair(_txn("bank", "-45000", 18), _txn("ledger", "-45500", 20))
    issue = match_issue(pair)
    assert "amount differs by 500" in issue
    assert "2 day(s) apart" in issue


def test_pair_resting_on_figures_alone_is_flagged() -> None:
    pair = _pair(_txn("bank", "-1000", 1), _txn("ledger", "-1000", 1), "amount_and_date_only")
    assert "matched on figures alone" in match_issue(pair)


def test_bank_charge_is_explained_as_an_expected_reconciling_item() -> None:
    """A CA files these rather than chasing them, so the report should say so."""
    charge = _txn("bank", "-118", 12, "HDFC CHRG SMS ALERT 062026-072026")
    assert "bank charge" in classify_unmatched(charge, [])


def test_ledger_receipt_with_no_counterpart_reads_as_a_deposit_in_transit() -> None:
    receipt = _txn("ledger", "38000", 25, "Nova Textiles - Advance")
    assert "receipt not yet on the statement" in classify_unmatched(receipt, [])


def test_ambiguity_is_reported_rather_than_left_bare() -> None:
    """Several same-amount candidates means the rules declined to guess — which a
    person can usually resolve at a glance, but only if told it happened."""
    txn = _txn("bank", "-5000", 5)
    others = [_txn("ledger", "-5000", 5, "one"), _txn("ledger", "-5000", 6, "two")]
    assert "ambiguous" in classify_unmatched(txn, others)


def test_same_amount_outside_the_date_window_says_so() -> None:
    txn = _txn("bank", "-5000", 1)
    far = [_txn("ledger", "-5000", 20, "same amount, far away")]
    reason = classify_unmatched(txn, far)
    assert "outside the 3-day window" in reason


def test_summary_unexplained_is_zero_when_everything_is_accounted_for() -> None:
    bank = [_txn("bank", "-1000", 1), _txn("bank", "-118", 2, "HDFC CHRG SMS")]
    ledger = [_txn("ledger", "-1000", 1)]
    rows = reconciliation_summary(bank, ledger, [_pair(bank[0], ledger[0])], [bank[1]], [])

    unexplained = next(r for r in rows if r["item"] == "UNEXPLAINED")
    assert unexplained["amount"] == 0.0


def test_summary_surfaces_an_unexplained_residual() -> None:
    """The check that matters: if a row were dropped, the proof must not balance."""
    bank = [_txn("bank", "-1000", 1), _txn("bank", "-118", 2, "HDFC CHRG SMS")]
    ledger = [_txn("ledger", "-1000", 1)]
    # the bank charge is neither matched nor reported as unmatched — it vanished
    rows = reconciliation_summary(bank, ledger, [_pair(bank[0], ledger[0])], [], [])

    unexplained = next(r for r in rows if r["item"] == "UNEXPLAINED")
    assert unexplained["amount"] != 0.0
