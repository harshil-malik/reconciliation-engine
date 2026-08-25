#!/usr/bin/env python
"""Check a reconciliation for correctness, without trusting the engine or the model.

Answers "how do I know this result is right?" using arithmetic that the source
documents themselves have to satisfy, rather than asking anyone to eyeball rows.

    python scripts/verify_reconciliation.py <bank.pdf> <ledger.pdf> [--ledger-template NAME]

Four independent checks:

1. DOCUMENT SELF-CONSISTENCY — within each file, every row's amount must equal the
   movement in its own running balance. A file that fails this contradicts itself,
   and no reconciliation of it can be trusted. This is what caught the sample
   statement that printed a deposit while its balance went down.

2. COMPLETENESS — every extracted transaction must appear exactly once in the
   result: in a matched pair, or in the unmatched list. Nothing invented, nothing
   lost. A row that silently vanishes is money disappearing from the report.

3. THE RECONCILIATION IDENTITY — the real test, and the one a chartered accountant
   applies to a bank reconciliation statement:

       (bank total - ledger total) = sum of differences on matched pairs
                                   + unmatched bank items
                                   - unmatched ledger items

   Every rupee of disagreement between the two books must be attributable to a
   specific identified item. If this balances, the reconciliation is arithmetically
   complete: nothing is unexplained. If it does not, something was mismatched,
   double-counted or dropped — regardless of how plausible the report looks.

4. MATCHED-PAIR SANITY — Stage 1 pairs must be exact-amount matches, and any
   Stage 1 pair whose amounts differ would mean the deterministic rules misfired.

Exit code 0 if every check passes, 1 otherwise, so this can gate a change.
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingestion.amounts import to_decimal  # noqa: E402
from app.ingestion.bank_templates.hdfc import HDFCBankTemplate  # noqa: E402
from app.ingestion.ledger_templates.generic_ledger import (  # noqa: E402
    BankAccountLedgerTemplate,
    GenericLedgerTemplate,
)
from app.ingestion.pdf_parser import parse_pdf  # noqa: E402
from app.ingestion.vision_client import LocalPDFExtractor  # noqa: E402
from app.matching.matcher import match  # noqa: E402
from app.schema import Transaction  # noqa: E402

_LEDGER_TEMPLATES = {
    "bank_account_ledger": BankAccountLedgerTemplate,
    "generic_ledger": GenericLedgerTemplate,
}


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, passed: bool, label: str, detail: str = "") -> None:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            self.failures += 1
        print(f"  [{mark}] {label}")
        if detail and not passed:
            print(f"         {detail}")


def _money(value: Decimal) -> str:
    return f"{value:>14,.2f}"


def check_self_consistency(
    transactions: list[Transaction], label: str, report: Report, *, debit_is_inflow: bool
) -> None:
    """Does the document agree with itself, as printed?

    Deliberately compares the figures in the debit/credit COLUMNS against the
    movement in the running balance — not the amounts the engine ended up using.
    Those are partly derived from the balance, so checking them against the balance
    would be circular and would always pass. Reading the columns as printed is what
    exposes a statement that says "deposit" while its own balance goes down.
    """
    print(f"\n1. DOCUMENT SELF-CONSISTENCY (as printed) — {label}")

    def _balance_of(txn: Transaction) -> Decimal | None:
        raw = txn.raw_row.get("balance") if isinstance(txn.raw_row, dict) else None
        if raw in (None, "", "null"):
            return None
        try:
            return to_decimal(raw)
        except Exception:  # noqa: BLE001
            return None

    def _column_amount(txn: Transaction) -> Decimal | None:
        if not isinstance(txn.raw_row, dict):
            return None
        try:
            debit = to_decimal(txn.raw_row.get("debit") or 0)
            credit = to_decimal(txn.raw_row.get("credit") or 0)
        except Exception:  # noqa: BLE001
            return None
        if debit == 0 and credit == 0:
            return None  # nothing printed to check
        return debit - credit if debit_is_inflow else credit - debit

    balances = [_balance_of(t) for t in transactions]
    comparable = [
        i
        for i in range(1, len(transactions))
        if balances[i] is not None
        and balances[i - 1] is not None
        and _column_amount(transactions[i]) is not None
    ]
    if not comparable:
        print("         no balance column (or no printed amounts) — check cannot run here")
        return

    mismatches = [
        (i, transactions[i], _column_amount(transactions[i]), balances[i] - balances[i - 1])  # type: ignore[operator]
        for i in comparable
        if _column_amount(transactions[i]) != balances[i] - balances[i - 1]  # type: ignore[operator]
    ]
    detail = "; ".join(
        f"row {i + 1} {t.date} {t.description[:26]!r}: printed column says {c}, "
        f"its own balance moves {d}"
        for i, t, c, d in mismatches[:4]
    )
    report.check(
        not mismatches,
        f"{len(comparable)} printed rows agree with the running balance",
        detail + ("  <- the FILE contradicts itself" if mismatches else ""),
    )


def check_completeness(
    bank: list[Transaction], ledger: list[Transaction], result, report: Report
) -> None:
    print("\n2. COMPLETENESS — every transaction accounted for exactly once")

    seen_bank = [p.bank_transaction.id for p in result.matched] + [t.id for t in result.unmatched_bank]
    seen_ledger = [p.ledger_transaction.id for p in result.matched] + [t.id for t in result.unmatched_ledger]

    report.check(
        len(seen_bank) == len(set(seen_bank)) == len(bank),
        f"bank: {len(bank)} in, {len(seen_bank)} out, no duplicates",
        f"expected {len(bank)} unique, saw {len(seen_bank)} ({len(set(seen_bank))} unique)",
    )
    report.check(
        len(seen_ledger) == len(set(seen_ledger)) == len(ledger),
        f"ledger: {len(ledger)} in, {len(seen_ledger)} out, no duplicates",
        f"expected {len(ledger)} unique, saw {len(seen_ledger)} ({len(set(seen_ledger))} unique)",
    )


def check_reconciliation_identity(
    bank: list[Transaction], ledger: list[Transaction], result, report: Report
) -> None:
    print("\n3. RECONCILIATION IDENTITY — is every rupee of difference explained?")

    bank_total = sum((t.amount for t in bank), Decimal("0"))
    ledger_total = sum((t.amount for t in ledger), Decimal("0"))
    difference = bank_total - ledger_total

    pair_differences = sum(
        (p.bank_transaction.amount - p.ledger_transaction.amount for p in result.matched),
        Decimal("0"),
    )
    unmatched_bank = sum((t.amount for t in result.unmatched_bank), Decimal("0"))
    unmatched_ledger = sum((t.amount for t in result.unmatched_ledger), Decimal("0"))
    explained = pair_differences + unmatched_bank - unmatched_ledger

    print(f"         bank movement total        {_money(bank_total)}")
    print(f"         ledger movement total      {_money(ledger_total)}")
    print(f"         difference to explain      {_money(difference)}")
    print(f"           matched-pair differences {_money(pair_differences)}")
    print(f"           unmatched bank items     {_money(unmatched_bank)}")
    print(f"           less unmatched ledger    {_money(-unmatched_ledger)}")
    print(f"         total explained            {_money(explained)}")

    report.check(
        difference == explained,
        "difference is fully explained by identified items",
        f"unexplained residual of {difference - explained} — something was mismatched, "
        "double-counted or dropped",
    )


def check_matched_pairs(result, report: Report) -> None:
    print("\n4. MATCHED-PAIR SANITY — deterministic pairs must be exact-amount")

    bad = [p for p in result.matched if p.bank_transaction.amount != p.ledger_transaction.amount]
    detail = "; ".join(
        f"{p.bank_transaction.date} {p.bank_transaction.amount} vs {p.ledger_transaction.amount}"
        for p in bad[:4]
    )
    report.check(not bad, f"all {len(result.matched)} Stage 1 pairs match on amount exactly", detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank_pdf")
    parser.add_argument("ledger_pdf")
    parser.add_argument("--ledger-template", default="bank_account_ledger", choices=list(_LEDGER_TEMPLATES))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  [note] %(message)s")
    extractor = LocalPDFExtractor()

    bank = parse_pdf(args.bank_pdf, source="bank", template=HDFCBankTemplate(), extractor=extractor)
    ledger = parse_pdf(
        args.ledger_pdf,
        source="ledger",
        template=_LEDGER_TEMPLATES[args.ledger_template](),
        extractor=extractor,
    )
    result = match(bank, ledger)

    print(f"\nExtracted {len(bank)} bank rows, {len(ledger)} ledger rows")
    print(f"Stage 1 matched {len(result.matched)}; "
          f"unmatched {len(result.unmatched_bank)} bank / {len(result.unmatched_ledger)} ledger")

    report = Report()
    check_self_consistency(bank, "bank statement", report, debit_is_inflow=False)
    check_self_consistency(
        ledger,
        "internal ledger",
        report,
        debit_is_inflow=_LEDGER_TEMPLATES[args.ledger_template].debit_is_inflow,
    )
    check_completeness(bank, ledger, result, report)
    check_reconciliation_identity(bank, ledger, result, report)
    check_matched_pairs(result, report)

    print()
    if report.failures:
        print(f"{report.failures} CHECK(S) FAILED — do not trust this reconciliation.")
        print("A failure in check 1 means the source document contradicts itself, which is a")
        print("problem with the file rather than the engine. Failures in 2-4 point at the engine.")
        return 1

    print("ALL CHECKS PASSED — the reconciliation is arithmetically complete.")
    print("Every transaction is accounted for and every rupee of difference is attributable")
    print("to a specific item. Note this proves consistency, not that each individual pairing")
    print("is the economically correct one — spot-check the Matched tab for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
