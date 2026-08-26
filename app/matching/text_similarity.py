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


def _reference_forms(txn: Transaction) -> set[str]:
    """Every identifier this row can be matched on, in comparable form.

    Includes ids recovered from the narration, since a bank may bury the real
    transaction id there while printing a placeholder in the reference column.
    Leading zeros are stripped from purely numeric ids: a cheque is "000412" on one
    side and "412" on the other, and they are the same cheque.
    """
    forms: set[str] = set()
    for raw in [txn.reference, *txn.alt_references]:
        if not raw:
            continue
        value = _normalize_text(raw)
        if not value:
            continue
        forms.add(value)
        if value.isdigit():
            forms.add(value.lstrip("0") or "0")
    return forms


def references_match(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    """The two rows carry an identifier in common.

    The strongest evidence available in reconciliation: banks and ledgers word
    narrations differently, but a transaction id is the same string by design.
    """
    bank_forms = _reference_forms(bank_txn)
    ledger_forms = _reference_forms(ledger_txn)
    return bool(bank_forms and ledger_forms and bank_forms & ledger_forms)


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
