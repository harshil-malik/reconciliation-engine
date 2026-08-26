from __future__ import annotations

import uuid
from datetime import date as date_type
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Used when a statement prints an amount with no narration at all, so the report can
# distinguish "the document said nothing here" from "extraction lost the text".
# Shared because Stage 2 must recognise it: a row with this description carries no
# counterparty evidence and must never be matched on amount and date alone.
NO_NARRATION = "(no narration)"


class Transaction(BaseModel):
    """Canonical transaction record.

    Every ingestion path (CSV, Excel, PDF-via-vision-LLM) must normalize into this
    shape before anything downstream (matching, anomaly detection) runs.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    date: date_type
    # Signed: inflow/credit is positive, outflow/debit is negative. This lets Stage 1's
    # "exact amount match" compare a bank row and a ledger row directly, with no
    # separate debit/credit-direction check needed.
    amount: Decimal
    description: str
    reference: Optional[str] = None
    # Identifiers found elsewhere in the row — chiefly transaction ids that a bank
    # buries in the narration while printing a placeholder in the reference column.
    # Kept alongside `reference` rather than replacing it: which form the ledger
    # records varies, so matching should be free to use either.
    alt_references: list[str] = Field(default_factory=list)
    # Who the money moved to or from, pulled out of the narration where the format
    # allows. Matching against this is sharper than against the whole narration,
    # which is mostly routing noise: "UPI/P2M/900000000001/SWIFTWAY EXPRESS/Courier
    # chg" shares little with "Swiftway Express - Courier charges" as raw strings,
    # but their payees are plainly the same.
    counterparty: Optional[str] = None
    source: Literal["bank", "ledger"]
    file_name: str
    raw_row: dict[str, Any]
