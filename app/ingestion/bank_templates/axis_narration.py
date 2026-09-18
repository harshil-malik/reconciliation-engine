from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# Formats seen on an Axis Bank current-account statement.
#   UPI/P2A/500584440248/RAJESH KUMAR TRADERS
#   UPI/P2M/501580421755/SWIGGY INSTAMART
#   NEFT/NFT8340/AMAZON SELLER SERVICES PVT LTD
#   NEFT/SAL4521/SALARY JUL         (Axis posts salary as a plain NEFT credit)
#   RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD
#   IMPS/503027177157/SUNIL ENTERPRISES
#   CHQ DEP-CHQ0452
#   ATM-CASH/KORAMANGALA/BLR
#   CONS CHG SMS ALERT Q2
_UPI = re.compile(r"^UPI/(?:P2[AM]/)?([A-Za-z0-9]+)/(.+)$", re.I)
_NEFT = re.compile(r"^NEFT/([A-Za-z0-9]+)/(.+)$", re.I)
_RTGS = re.compile(r"^RTGS/([A-Za-z0-9]+)/(.+)$", re.I)
_IMPS = re.compile(r"^IMPS/([A-Za-z0-9]+)/(.+)$", re.I)
_CHEQUE = re.compile(r"^CHQ\s+DEP-([A-Za-z0-9]+)$", re.I)
# Bank-originated / cash postings. No counterparty is named — the bank IS the
# counterparty — and marking them as such lets an unmatched row be reported as an
# expected reconciling item rather than a discrepancy.
_ATM = re.compile(r"^ATM-CASH\b", re.I)
_CHARGE = re.compile(r"^CONS\s+CHG\b", re.I)

_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of an Axis Bank
    narration.

    Returns empties rather than guessing when the narration is not one of the known
    shapes — ATM withdrawals and bank charges name no counterparty, and inventing
    one would create false matching signal.
    """
    text = (description or "").strip()
    if not text:
        return ParsedNarration(None, None, None)

    match = _UPI.match(text)
    if match:
        reference, counterparty = match.group(1).strip(), match.group(2).strip()
        if len(reference) < _MIN_REFERENCE_LENGTH:
            reference = None
        return ParsedNarration("upi", reference or None, counterparty or None)

    match = _NEFT.match(text)
    if match:
        reference, counterparty = match.group(1).strip(), match.group(2).strip()
        return ParsedNarration("neft", reference or None, counterparty or None)

    match = _RTGS.match(text)
    if match:
        reference, counterparty = match.group(1).strip(), match.group(2).strip()
        return ParsedNarration("rtgs", reference or None, counterparty or None)

    match = _IMPS.match(text)
    if match:
        reference, counterparty = match.group(1).strip(), match.group(2).strip()
        if len(reference) < _MIN_REFERENCE_LENGTH:
            reference = None
        return ParsedNarration("imps", reference or None, counterparty or None)

    match = _CHEQUE.match(text)
    if match:
        return ParsedNarration("cheque", match.group(1).strip() or None, None)

    if _ATM.match(text):
        return ParsedNarration("atm", None, None)
    if _CHARGE.match(text):
        return ParsedNarration("charge", None, None)

    return ParsedNarration(None, None, None)
