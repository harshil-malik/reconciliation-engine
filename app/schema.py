from __future__ import annotations

import uuid
from datetime import date as date_type
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field

# Used when a statement prints an amount with no narration at all, so the report can
# distinguish "the document said nothing here" from "extraction lost the text".
# Shared because Stage 2 must recognise it: a row with this description carries no
# counterparty evidence and must never be matched on amount and date alone.
NO_NARRATION = "(no narration)"


class SourceRef(BaseModel):
    """Where in the uploaded file this transaction was read from.

    Exists so every flagged row can be traced back to the document it came from. In
    audit an unsourced figure is worthless: a reviewer looking at a mismatch has to
    be able to reach the line that produced it, in the file they themselves uploaded,
    without taking the engine's word for anything. `text` carries the source line
    verbatim so the report can show it rather than describe it.

    `kind` is honest about how the row was obtained, because the three are not
    equally traceable:
      pdf_line        — read from the text layer at a known page and line. Exact.
      sheet_row       — a CSV/Excel row, numbered as the spreadsheet numbers it
                        (header is row 1), so the CA can jump straight to it.
      model_extracted — the deterministic parser could not read the layout and the
                        model was asked instead. There is no line to point at, and
                        claiming one would be the invented citation this whole record
                        exists to prevent.
    """

    kind: Literal["pdf_line", "sheet_row", "model_extracted"]
    # 1-based, as a person counts them. `line_start`/`line_end` differ when a
    # narration wraps across physical lines, which is a range, not a line.
    page: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    text: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """Human-readable locator, e.g. "page 2, lines 37-38" or "row 47"."""
        if self.kind == "model_extracted":
            return "read by the model — no source line"
        if self.line_start is None:
            return "location unknown"
        if self.kind == "sheet_row":
            return f"row {self.line_start}"
        page = f"page {self.page}, " if self.page else ""
        if self.line_end is not None and self.line_end != self.line_start:
            return f"{page}lines {self.line_start}-{self.line_end}"
        return f"{page}line {self.line_start}"


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
    # Where this row came from in `file_name`. Optional only so a Transaction can
    # still be constructed in a test without inventing a provenance for it; every
    # ingestion path in the pipeline populates it.
    source_ref: Optional[SourceRef] = None
    raw_row: dict[str, Any]
