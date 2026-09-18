from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# Formats seen on an SBI current-account statement:
#   TO TRANSFER-UPI/DR/500584440248/RAJESH KUMAR TRADERS/rajeshtraders@okaxis/INV2241
#   BY TRANSFER-NEFT*HDFC0000456*NFT8340*AMAZON SELLER SERVICES PVT LTD
#   BY TRANSFER-RTGS*BARB0000890*RTG3300*MAHESH ELECTRICALS PVT LTD
#   BY TRANSFER-INB SALARY JUL/SAL4521
#   TO TRANSFER-IMPS/503027177157/SUNIL ENTERPRISES
#   BY CLEARING-CHEQUE NO CHQ0452
#   TO ATM WDL-ATM CASH/S1CA1234/KORAMANGALA BLR
#   TO CHARGES-SMS ALERT CHARGES Q2 INCL GST
# Every line carries a "TO"/"BY" direction prefix ahead of the transaction type —
# irrelevant here, since direction already comes from the Debit/Credit column, but
# it must be consumed by every pattern or nothing matches.
# The counterparty is trailed by the sender's bank code, UPI handle and sometimes
# an invoice number ("/UTIB/rajeshtraders@okaxis/INV2241") — stripped here so none
# of that routing noise ends up read as part of the name.
_UPI = re.compile(
    r"^(?:TO|BY)\s+TRANSFER-UPI/(?:DR|CR)/([A-Za-z0-9]+)/(.+?)(?:/[A-Z]{3,4}/.+)?$",
    re.I,
)
# NEFT/RTGS print the receiving IFSC first, then the reference, then the
# counterparty — asterisk-delimited, unlike HDFC's colon-and-slash form.
_NEFT_RTGS = re.compile(
    r"^(?:TO|BY)\s+TRANSFER-(NEFT|RTGS)\*[A-Z0-9]+\*([A-Za-z0-9]+)\*(.+)$", re.I
)
_IMPS = re.compile(r"^(?:TO|BY)\s+TRANSFER-IMPS/([A-Za-z0-9]+)/(.+)$", re.I)
_SALARY = re.compile(r"^BY\s+TRANSFER-INB\s+SALARY\s+\S+/([A-Za-z0-9]+)$", re.I)
_CHEQUE = re.compile(r"^BY\s+CLEARING-CHEQUE\s+NO\s+([A-Za-z0-9]+)$", re.I)
# Bank-originated postings. No counterparty is named — the bank IS the counterparty
# — and marking them as such lets an unmatched row be reported as an expected
# reconciling item rather than a discrepancy.
_ATM = re.compile(r"^TO\s+ATM\s+WDL\b", re.I)
_CHARGE = re.compile(r"^TO\s+CHARGES\b", re.I)

_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of an SBI narration.

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

    match = _NEFT_RTGS.match(text)
    if match:
        kind = match.group(1).lower()
        reference, counterparty = match.group(2).strip(), match.group(3).strip()
        return ParsedNarration(kind, reference or None, counterparty or None)

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
