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


# Below this length a shared prefix means nothing: "ele" opens "Electricals" and
# "Electronics" alike, and three characters of agreement is not evidence about who
# was paid.
_MIN_PREFIX_TOKEN_LENGTH = 4


def tokens_correspond(a: str, b: str) -> bool:
    """Do these two words name the same thing, allowing for truncation?

    Bank statements abbreviate where ledgers spell out ("MAHESH ELEC" against
    "Mahesh Electricals"), so a token that opens one on the other side counts.
    """
    if a == b:
        return True
    if min(len(a), len(b)) < _MIN_PREFIX_TOKEN_LENGTH:
        return False
    return a.startswith(b) or b.startswith(a)


def token_similarity(a: str, b: str) -> float:
    """How much of what each narration says about its counterparty is shared.

    Scored over `distinctive_tokens` rather than raw characters, which is what makes
    it disagree with `description_similarity` in both directions and in the right
    one each time. A bank narration carries routing scaffolding the ledger never has
    ("RTGS DR-BARB0000890-...-RTG3300"), and character similarity reads that padding
    as disagreement; conversely two unrelated firms score highly on shared "to"/
    "Ltd" boilerplate, which `distinctive_tokens` has already removed.

    Dice rather than a raw hit count, so a long narration cannot corroborate a short
    one merely by containing many words: both sides must be substantially about the
    same party.
    """
    a_tokens = distinctive_tokens(a)
    b_tokens = distinctive_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    a_hits = sum(1 for x in a_tokens if any(tokens_correspond(x, y) for y in b_tokens))
    b_hits = sum(1 for y in b_tokens if any(tokens_correspond(x, y) for x in a_tokens))
    return (a_hits + b_hits) / (len(a_tokens) + len(b_tokens))


def descriptions_corroborate(bank_txn: Transaction, ledger_txn: Transaction) -> bool:
    """Do these two narrations name anything in common?

    A stricter question than "are these strings similar", and the right one for
    deciding whether a match rests on more than its figures.
    """
    bank_tokens = distinctive_tokens(bank_txn.description)
    ledger_tokens = distinctive_tokens(ledger_txn.description)
    if not bank_tokens or not ledger_tokens:
        return False
    return any(
        tokens_correspond(a, b) for a in bank_tokens for b in ledger_tokens
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


# 1.0 is reserved for a matching identifier, which is certainty: the two rows carry
# the same string because a bank wrote it on both. Narrations can agree on every
# distinctive word and still describe two payments to one supplier in the same week,
# so however well they correspond they stay just short of that.
_MAX_NARRATION_CONFIDENCE = 0.99


def candidate_similarity(bank_txn: Transaction, ledger_txn: Transaction) -> float:
    """Similarity used to disambiguate multiple same-amount/same-date candidates.

    A reference number (cheque no., UTR, voucher no.) is the strongest signal when
    both sides have one — an exact match short-circuits to full confidence.

    Failing that, score on the words that identify the counterparty rather than on
    raw characters. Character similarity answers "are these strings alike", which is
    not the question: a bank narration is mostly routing scaffolding the ledger never
    repeats, so a plainly correct pair reads as weak, while two unrelated firms read
    as strong on shared boilerplate. Where neither side offers a distinctive word to
    compare — an unnarrated row — fall back to characters, since a weak signal beats
    none.
    """
    if bank_txn.reference and ledger_txn.reference:
        if _normalize_text(bank_txn.reference) == _normalize_text(ledger_txn.reference):
            return 1.0
    if distinctive_tokens(bank_txn.description) and distinctive_tokens(
        ledger_txn.description
    ):
        score = token_similarity(bank_txn.description, ledger_txn.description)
    else:
        score = description_similarity(bank_txn.description, ledger_txn.description)
    return min(score, _MAX_NARRATION_CONFIDENCE)
