#!/usr/bin/env python
"""Measure the Stage 2 match confirmer against a labelled set of transaction pairs.

Unlike `tests/`, this needs a live model server — it exists to answer the question
the mocked tests cannot: *is the configured model actually good enough to judge
reconciliation matches?* Run it after changing the model, the prompt, or the
confidence threshold.

    llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192
    python scripts/eval_confirmer.py

What to look at, in priority order:

1. **FALSE-POS must be 0.** A wrong match silently hides a real discrepancy in the
   books — the worst failure this system can produce.
2. **Recall** is secondary. A missed match lands in the CA's review queue, which is
   just the pre-existing manual status quo; it costs a minute, not correctness.
3. **Threshold plateau.** Prefer a threshold sitting in the middle of a range that
   behaves identically, not on a knife-edge where one case flips the result.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai_matching.confirmer import LocalMatchConfirmer  # noqa: E402
from app.schema import Transaction  # noqa: E402


def _txn(source: str, amount: str, day: int, description: str, reference: str | None = None):
    return Transaction(
        date=date(2026, 8, day),
        amount=Decimal(amount),
        description=description,
        reference=reference,
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


# Amounts are written with 2 decimal places because that is what the CSV/Excel
# parsers actually produce. This is not cosmetic: the local 3B model scored the same
# pair 0.8 as "-10050" and 0.3 as "-10050.00", so evaluating on prettier inputs than
# the pipeline produces gives a misleadingly good result.
CASES: list[tuple[bool, str, Transaction, Transaction]] = [
    # --- should match -------------------------------------------------------
    (True, "clearing lag 2d",
     _txn("bank", "-75250.00", 10, "IMPS TRF TO RAJESH KUMAR SUPPLIES"),
     _txn("ledger", "-75250.00", 12, "Payment to R.K. Supplies for material purchase")),
    (True, "clearing lag 8d",
     _txn("bank", "-75250.00", 10, "IMPS TRF TO RAJESH KUMAR SUPPLIES"),
     _txn("ledger", "-75250.00", 18, "Payment to R.K. Supplies for material purchase")),
    (True, "exact same, 1d lag",
     _txn("bank", "-25000.00", 3, "SALARY PAYMENT JOHN DOE"),
     _txn("ledger", "-25000.00", 4, "Salary paid - J. Doe")),
    (True, "bank fee gap",
     _txn("bank", "-10050.00", 7, "RTGS TO SUNRISE TRADERS"),
     _txn("ledger", "-10000.00", 7, "Sunrise Traders invoice payment")),
    (True, "abbreviated vendor",
     _txn("bank", "-33000.00", 9, "UPI-MAHESH ELEC"),
     _txn("ledger", "-33000.00", 9, "Mahesh Electricals - purchase")),
    (True, "reference identical",
     _txn("bank", "-15075.00", 1, "NEFT VENDOR PAYMENT", "N123456789"),
     _txn("ledger", "-15075.00", 1, "Vendor Payment - ABC Supplies", "N123456789")),
    (True, "invoice number tie",
     _txn("bank", "-88000.00", 11, "NEFT PAYMT INV-4471"),
     _txn("ledger", "-88000.00", 13, "Invoice 4471 settlement Zenith")),
    (True, "name reversed",
     _txn("bank", "-41000.00", 4, "NEFT SHARMA PRIYA"),
     _txn("ledger", "-41000.00", 5, "Divya Kapoor consultancy")),
    # --- should NOT match ---------------------------------------------------
    (False, "different vendor",
     _txn("bank", "-20000.00", 2, "NEFT TO GLOBEX LTD"),
     _txn("ledger", "-20000.00", 2, "Payment to Initech Pvt Ltd")),
    (False, "rent vs supplier",
     _txn("bank", "-45000.00", 15, "OFFICE RENT AUGUST"),
     _txn("ledger", "-45000.00", 15, "Raw material purchase Kumar")),
    (False, "atm vs salary",
     _txn("bank", "-50000.00", 5, "ATM CASH WITHDRAWAL MUMBAI"),
     _txn("ledger", "-50000.00", 6, "Salary paid to Divya Kapoor August")),
    (False, "different purpose",
     _txn("bank", "-12000.00", 18, "ELECTRICITY BILL PAYMENT"),
     _txn("ledger", "-12000.00", 18, "Courier charges monthly")),
    (False, "vague both sides",
     _txn("bank", "-9000.00", 22, "MISC DEBIT"),
     _txn("ledger", "-9000.00", 22, "Sundry expenses")),
    (False, "amount gap too large",
     _txn("bank", "-30000.00", 6, "NEFT TO APEX TOOLS"),
     _txn("ledger", "-24000.00", 6, "Apex Tools purchase")),
    (False, "similar name, different company",
     _txn("bank", "-18000.00", 8, "NEFT TO SUNRISE TRADERS"),
     _txn("ledger", "-18000.00", 8, "Sunset Traders payment")),
]


def main() -> int:
    confirmer = LocalMatchConfirmer()
    scored: list[tuple[bool, str, float]] = []

    print(f"{'case':34s} {'expected':9s} {'conf':>5s}")
    print("-" * 52)
    for expected, label, bank_txn, ledger_txn in CASES:
        result = confirmer.confirm(bank_txn, ledger_txn)
        scored.append((expected, label, result.confidence))
        print(f"{label:34s} {str(expected):9s} {result.confidence:5.2f}")

    print("\nthreshold sweep")
    print("-" * 52)
    best: tuple[int, float] | None = None
    for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        tp = sum(1 for e, _, c in scored if e and c >= threshold)
        fn = sum(1 for e, _, c in scored if e and c < threshold)
        fp = sum(1 for e, _, c in scored if not e and c >= threshold)
        tn = sum(1 for e, _, c in scored if not e and c < threshold)
        flag = "  <-- FALSE POSITIVES" if fp else ""
        print(
            f"  {threshold:.1f}  recall {tp}/{tp + fn}   "
            f"true-neg {tn}/{tn + fp}   false-pos {fp}{flag}"
        )
        if fp == 0 and (best is None or tp > best[0]):
            best = (tp, threshold)

    if best is None:
        print("\nNo threshold avoids false positives — the model is unsuitable as configured.")
        return 1

    print(f"\nBest false-positive-free threshold: {best[1]:.1f} (recall {best[0]}/8)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
