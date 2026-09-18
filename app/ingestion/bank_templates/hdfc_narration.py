from __future__ import annotations

import re

from app.ingestion.bank_templates.base import ParsedNarration

# HDFC packs the real transaction identifier into the narration and prints
# "0000000000" in the Chq./Ref.No. column — on the statement this was built from,
# 11 of 18 rows carried a reference the ledger also holds, while only 5 matched
# through the reference column. For NEFT the two forms differ: the column shows
# "N032025070399999" while both the narration and the ledger use the
# "HDFC0R00099920250703" UTR, so the narration is the one worth matching on.
#
# Formats seen on a real current-account statement:
#   UPI/P2M/900000000001/SWIFTWAY EXPRESS/Courier chg
#   UPI/P2A/900000000002/ANIL GUPTA/Rent share
#   NEFT CR:HDFC0R00099920250703/NORTH SUPPLY PVT LTD/INV-1042
#   RTGS CR:HDFC0R00099920250707/PEAK TRADERS/Advance payment
#   IMPS-900000000005-DIVYA KAPOOR-Consulting fee
#   ACH DR-NACH-OMNI HEALTH INS-Premium
#   CHQ PAID 000412
_UPI = re.compile(r"^UPI/(?:P2[MA]|[A-Z]{2,4})/([A-Za-z0-9]+)/(.+?)(?:/(.*))?$", re.I)
_NEFT_RTGS = re.compile(r"^(?:NEFT|RTGS)\s*(?:CR|DR)?\s*:\s*([A-Za-z0-9]+)/(.+?)(?:/(.*))?$", re.I)
_IMPS = re.compile(r"^IMPS[-/]([A-Za-z0-9]+)[-/](.+?)(?:[-/](.*))?$", re.I)
_CHEQUE = re.compile(r"^CHQ\s+(?:PAID|DEP|ISSUED)?\s*0*([0-9]{3,})", re.I)
_NACH = re.compile(r"^ACH\s+(?:DR|CR)[-/]NACH[-/](.+?)(?:[-/](.*))?$", re.I)
# Bank-originated postings. These name no counterparty — the bank IS the
# counterparty — and identifying them as such is what lets an unmatched row be
# reported as an expected reconciling item rather than a discrepancy.
_CHARGE = re.compile(r"^(?:HDFC\s+)?CHRG\b|^HDFC\s+CHRG\b", re.I)
_INTEREST = re.compile(r"^INT\.?\s*(?:PD|CR)\b", re.I)

# Identifiers shorter than this are too generic to be worth matching on.
_MIN_REFERENCE_LENGTH = 5


def parse_narration(description: str) -> ParsedNarration:
    """Pull the transaction identifier and counterparty out of an HDFC narration.

    Returns empties rather than guessing when the narration is not one of the known
    shapes — bank charges and interest postings name no counterparty, and inventing
    one would create false matching signal.
    """
    text = (description or "").strip()
    if not text:
        return ParsedNarration(None, None, None)

    for kind, pattern in (("upi", _UPI), ("neft_rtgs", _NEFT_RTGS), ("imps", _IMPS)):
        match = pattern.match(text)
        if match:
            reference = match.group(1).strip()
            counterparty = (match.group(2) or "").strip()
            if len(reference) < _MIN_REFERENCE_LENGTH:
                reference = None
            if kind == "neft_rtgs":
                kind = "rtgs" if text.upper().startswith("RTGS") else "neft"
            return ParsedNarration(kind, reference or None, counterparty or None)

    match = _CHEQUE.match(text)
    if match:
        # Cheque numbers are printed with leading zeros on one side and not the
        # other, so the digits are what should be compared.
        return ParsedNarration("cheque", match.group(1), None)

    match = _NACH.match(text)
    if match:
        return ParsedNarration("nach", None, match.group(1).strip() or None)

    if _CHARGE.match(text):
        return ParsedNarration("charge", None, None)
    if _INTEREST.match(text):
        return ParsedNarration("interest", None, None)

    return ParsedNarration(None, None, None)
