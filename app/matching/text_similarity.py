from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.schema import Transaction


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


def candidate_similarity(bank_txn: Transaction, ledger_txn: Transaction) -> float:
    """Similarity used to disambiguate multiple same-amount/same-date candidates.

    A reference number (cheque no., UTR, voucher no.) is the strongest signal when
    both sides have one — an exact match short-circuits to full confidence. Otherwise
    falls back to fuzzy description/narration similarity.
    """
    if bank_txn.reference and ledger_txn.reference:
        if _normalize_text(bank_txn.reference) == _normalize_text(ledger_txn.reference):
            return 1.0
    return description_similarity(bank_txn.description, ledger_txn.description)
