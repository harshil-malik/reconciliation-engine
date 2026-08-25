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
    source: Literal["bank", "ledger"]
    file_name: str
    raw_row: dict[str, Any]
