from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# Formats seen on an ICICI current-account statement. ICICI's own Cheque Number
# column reads "-" on every row but a real cheque transaction, so the reference id
# printed here in the narration is the ONLY reference most rows carry.
#   UPI/500584440248/INV2241/RAJESH KUMAR/UTIB/rajeshtrader
#   NEFT-NFT8340-AMAZON SELLER SERVICES PVT LTD
#   RTGS/RTG3300/MAHESH ELECTRICALS PVT LTD
#   SAL-SAL4521-JUL26
#   MMT/IMPS/503027177157/FREIGHT/SUNIL ENTERPRISES
#   CLG/CHQ0452
#   ATM/S1CA1234/KORAMANGALA/BLR
#   BIL/ONL/500204026/SMS ALERT CHG
# UPI trails the counterparty with the sender's bank code and UPI handle
# ("/UTIB/rajeshtrader") — a 3-4 letter bank code followed by a handle, stripped
# here so it doesn't get read as part of the name.
_UPI = re.compile(
    r"^UPI/([A-Za-z0-9]+)/[A-Za-z0-9-]+/(.+?)(?:/[A-Z]{3,4}/\S+)?$", re.I
)
_NEFT = re.compile(r"^NEFT-([A-Za-z0-9]+)-(.+)$", re.I)
_RTGS = re.compile(r"^RTGS/([A-Za-z0-9]+)/(.+)$", re.I)
_SALARY = re.compile(r"^SAL-([A-Za-z0-9]+)-\S+$", re.I)
_IMPS = re.compile(r"^MMT/IMPS/([A-Za-z0-9]+)/[A-Za-z0-9-]+/(.+)$", re.I)
_CHEQUE = re.compile(r"^CLG/([A-Za-z0-9]+)$", re.I)
# Bank-originated postings. No counterparty is named — the bank IS the counterparty
# — and marking them as such lets an unmatched row be reported as an expected
# reconciling item rather than a discrepancy.
_ATM = re.compile(r"^ATM/", re.I)
_CHARGE = re.compile(r"^BIL/ONL/", re.I)

_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of an ICICI narration.

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
