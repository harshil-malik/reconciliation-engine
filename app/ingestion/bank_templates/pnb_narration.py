from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# Formats seen on a PNB current-account statement. PNB's Instrument Id column
# already carries most references, but is blank for ATM and bank-charge rows, and
# the narration is where a counterparty name lives either way.
#   UPI/500584440248/RAJESH KUMAR TRADERS
#   NEFT/NFT8340/AMAZON SELLER SERVICES PVT LTD
#   RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD
#   IMPS/503027177157/SUNIL ENTERPRISES
#   SALARY CREDIT SAL4521
#   CHEQUE DEPOSIT CHQ0452
#   ATM CASH WITHDRAWAL KORAMANGALA
#   SMS ALERT CHARGES Q2
_UPI = re.compile(r"^UPI/([A-Za-z0-9]+)/(.+)$", re.I)
_NEFT = re.compile(r"^NEFT/([A-Za-z0-9]+)/(.+)$", re.I)
_RTGS = re.compile(r"^RTGS/([A-Za-z0-9]+)/(.+)$", re.I)
_IMPS = re.compile(r"^IMPS/([A-Za-z0-9]+)/(.+)$", re.I)
_SALARY = re.compile(r"^SALARY\s+CREDIT\s+([A-Za-z0-9]+)$", re.I)
_CHEQUE = re.compile(r"^CHEQUE\s+DEPOSIT\s+([A-Za-z0-9]+)$", re.I)
# Bank-originated / cash postings. No counterparty is named — the bank IS the
# counterparty — and marking them as such lets an unmatched row be reported as an
# expected reconciling item rather than a discrepancy.
_ATM = re.compile(r"^ATM\s+CASH\s+WITHDRAWAL\b", re.I)
_CHARGE = re.compile(r"^SMS\s+ALERT\b", re.I)

_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of a PNB narration.

    Returns empties rather than guessing when the narration is not one of the known
    shapes — ATM withdrawals and bank charges name no counterparty, and inventing
    one would create false matching signal.
    """
    text = (description or "").strip()
    if not text:
        return ParsedNarration(None, None, None)

    for kind, pattern in (("upi", _UPI), ("neft", _NEFT), ("rtgs", _RTGS), ("imps", _IMPS)):
        match = pattern.match(text)
        if match:
            reference, counterparty = match.group(1).strip(), match.group(2).strip()
            if kind in ("upi", "imps") and len(reference) < _MIN_REFERENCE_LENGTH:
                reference = None
            return ParsedNarration(kind, reference or None, counterparty or None)

    match = _SALARY.match(text)
    if match:
        return ParsedNarration("salary", match.group(1).strip() or None, None)

    match = _CHEQUE.match(text)
    if match:
        return ParsedNarration("cheque", match.group(1).strip() or None, None)

    if _ATM.match(text):
        return ParsedNarration("atm", None, None)
    if _CHARGE.match(text):
        return ParsedNarration("charge", None, None)

    return ParsedNarration(None, None, None)
