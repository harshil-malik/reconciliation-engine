from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# Formats seen on a Kotak Mahindra Bank current-account statement.
#   UPI/500584440248/RAJESH KUMAR TRADERS/rajeshtraders@okaxis
#   NEFT/NFT8340/AMAZON SELLER SERVICES PVT LTD/HDFC0000456
#   RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD
#   IMPS/503027177157/SUNIL ENTERPRISES
#   SALARY SAL4521
#   CHQ DEP CHQ0452
#   ATM WDL KORAMANGALA BLR
#   SMS ALERT CHARGES Q2
# UPI trails the counterparty with the sender's UPI handle ("/rajeshtraders@okaxis"),
# recognisable by the "@". NEFT instead trails it with the sender's IFSC code
# ("/HDFC0000456") — 4 letters then 7 digits. Both stripped so neither ends up read
# as part of the name.
_UPI = re.compile(r"^UPI/([A-Za-z0-9]+)/(.+?)(?:/\S+@\S+)?$", re.I)
_NEFT = re.compile(r"^NEFT/([A-Za-z0-9]+)/(.+?)(?:/[A-Z]{4}\d{7})?$", re.I)
_RTGS = re.compile(r"^RTGS/([A-Za-z0-9]+)/(.+?)(?:/[A-Z]{4}\d{7})?$", re.I)
_IMPS = re.compile(r"^IMPS/([A-Za-z0-9]+)/(.+)$", re.I)
_SALARY = re.compile(r"^SALARY\s+([A-Za-z0-9]+)$", re.I)
_CHEQUE = re.compile(r"^CHQ\s+DEP\s+([A-Za-z0-9]+)$", re.I)
# Bank-originated / cash postings. No counterparty is named — the bank IS the
# counterparty — and marking them as such lets an unmatched row be reported as an
# expected reconciling item rather than a discrepancy.
_ATM = re.compile(r"^ATM\s+WDL\b", re.I)
_CHARGE = re.compile(r"^SMS\s+ALERT\b", re.I)

_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of a Kotak Mahindra
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
