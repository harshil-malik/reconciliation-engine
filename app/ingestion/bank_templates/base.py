from __future__ import annotations

from typing import Protocol


class BankPDFTemplate(Protocol):
    """Per-source PDF extraction template (a bank statement layout, or a ledger
    export layout).

    Each target format gets its own template (its own prompt, tuned to that
    format's column layout) rather than one generic PDF-parsing prompt — per the
    spec, PDF extraction is built and validated one format at a time.
    """

    template_name: str

    def build_prompt(self) -> str:
        """Instructions sent to the vision LLM alongside the PDF bytes."""
        ...

    def parse_response(self, response_text: str) -> list[dict]:
        """Parse the vision LLM's response into raw row dicts with keys:
        date, description, reference, debit, credit (all pre-normalization, as
        printed in the statement)."""
        ...
