from __future__ import annotations

from decimal import Decimal

from app.schema import Transaction

# What counts as a difference a bank fee or rounding could explain. A fee is small
# both absolutely and relative to the transaction, so a gap beyond BOTH of these is
# a different transaction, not the same one net of charges.
MAX_FEE_GAP_ABSOLUTE = Decimal("500")
MAX_FEE_GAP_FRACTION = Decimal("0.02")


def fee_gap_is_plausible(
    bank_txn: Transaction,
    ledger_txn: Transaction,
    *,
    max_absolute: Decimal = MAX_FEE_GAP_ABSOLUTE,
    max_fraction: Decimal = MAX_FEE_GAP_FRACTION,
) -> bool:
    """Could the difference between these two amounts be a fee or rounding?

    Shared by the deterministic near-match stage and the AI net so the two cannot
    drift apart on what counts as a plausible difference — they would otherwise
    disagree about which pairs are even worth considering.
    """
    gap = abs(bank_txn.amount - ledger_txn.amount)
    magnitude = max(abs(bank_txn.amount), abs(ledger_txn.amount))
    return gap <= max(max_absolute, magnitude * max_fraction)


def same_direction(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    """Both sides record money moving the same way.

    Amounts are normalized signed on both sides, so a bank inflow and a ledger
    outflow are different economic events however alike they otherwise look.
    """
    return (bank_txn.amount > 0) == (ledger_txn.amount > 0)
