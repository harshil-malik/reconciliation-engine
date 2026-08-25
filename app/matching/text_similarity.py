from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.schema import Transaction


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


# Words that appear on both sides of almost every reconciliation and therefore carry
# no evidence about WHO a transaction was with. Leaving them in inflates similarity
# between unrelated rows: "NEFT TO GLOBEX LTD" and "Payment to Initech Pvt Ltd" score
# 0.50 on raw characters — higher than genuine pairs — almost entirely on the shared
# "to"/"Ltd" scaffolding, which is how two different companies come to look alike.
_BOILERPLATE = {
    "neft", "rtgs", "imps", "upi", "chq", "cheque", "dep", "deposit", "wdl",
    "withdrawal", "trf", "transfer", "payment", "paymt", "payt", "paid", "pymt",
    "to", "from", "for", "by", "of", "the", "and", "a", "an", "ref", "no",
    "ltd", "limited", "pvt", "private", "inc", "co", "company", "corp",
    "bank", "account", "ac", "credit", "debit", "cr", "dr", "amt", "amount",
    # Business-type suffixes. Two firms sharing only "Traders" are no more likely to
    # be the same firm than two sharing only "Ltd" — without these, "Sunrise Traders"
    # and "Sunset Traders" corroborate each other.
    "traders", "trading", "enterprises", "enterprise", "services", "solutions",
    "industries", "supplies", "associates", "agencies", "stores", "sons",
}


def distinctive_tokens(text: str) -> set[str]:
    """The words in a narration that actually identify a counterparty.

    Strips banking boilerplate and one/two-character fragments, so what remains is
    the party name, invoice number, or purpose — the parts that mean something.
    """
    words = re.findall(r"[a-z0-9]+", _normalize_text(text))
    return {w for w in words if len(w) > 2 and w not in _BOILERPLATE}


def descriptions_corroborate(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    """Do these two narrations name anything in common?

    A stricter question than "are these strings similar", and the right one for
    deciding whether a match rests on more than its figures.
    """
    bank_tokens = distinctive_tokens(bank_txn.description)
    ledger_tokens = distinctive_tokens(ledger_txn.description)
    if not bank_tokens or not ledger_tokens:
        return False
    if bank_tokens & ledger_tokens:
        return True
    # Bank statements truncate ("MAHESH ELEC" for "Mahesh Electricals"), so also
    # accept a token that is a prefix of one on the other side.
    return any(
        a.startswith(b) or b.startswith(a)
        for a in bank_tokens
        for b in ledger_tokens
        if min(len(a), len(b)) >= 4
    )


def references_match(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    """Both sides carry the same cheque/UTR/voucher number.

    The strongest evidence available in reconciliation: banks and ledgers word
    narrations differently, but a reference number is the same string by design.
    """
    if not (bank_txn.reference and ledger_txn.reference):
        return False
    return _normalize_text(bank_txn.reference) == _normalize_text(ledger_txn.reference)


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
