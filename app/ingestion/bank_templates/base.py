from __future__ import annotations

import json
import re
from typing import Protocol


def parse_json_rows(response_text: str) -> list[dict]:
    """Pull the row array out of a model response.

    Shared by every template because they all ask for the same JSON shape. Accepts
    both a bare ``[...]`` array and the ``{"rows": [...]}`` wrapper that
    grammar-constrained decoding produces, and tolerates markdown fences around
    either — small models add them despite instructions not to.
    """
    match = re.search(r"\[.*\]", response_text.strip(), re.DOTALL)
    if match is None:
        raise ValueError(
            f"No JSON array found in model response: {response_text[:200]!r}"
        )
    return json.loads(match.group(0))


class ParsedNarration:
    """A narration broken into the parts that matter for reconciliation.

    Shared by every bank's narration parser (hdfc_narration.py and friends) so each
    one only has to define its own regexes, not re-declare this container. `kind`
    says what the bank did (upi, neft, rtgs, imps, cheque, salary, atm, charge, ...),
    `reference` is the transaction id, and `counterparty` is who the money moved to
    or from — matching against that is far sharper than against the whole narration,
    which is mostly routing noise.
    """

    __slots__ = ("kind", "reference", "counterparty")

    def __init__(self, kind: str | None, reference: str | None, counterparty: str | None):
        self.kind = kind
        self.reference = reference
        self.counterparty = counterparty

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ParsedNarration(kind={self.kind!r}, reference={self.reference!r}, "
            f"counterparty={self.counterparty!r})"
        )


class BankPDFTemplate(Protocol):
    """Per-source PDF extraction template (a bank statement layout, or a ledger
    export layout).

    Each target format gets its own template (its own prompt, tuned to that
    format's column layout) rather than one generic PDF-parsing prompt — per the
    spec, PDF extraction is built and validated one format at a time.
    """

    template_name: str

    # Which column represents money coming IN, which differs by whose books these
    # are. A bank statement is written from the bank's side: a Deposit (credit) is
    # money in, so this is False. A bank-account ledger in the client's own books is
    # the mirror image — a Debit to the bank asset account is money in — so that
    # template sets it True. Getting this backwards inverts every sign in the file
    # and makes reconciliation fail wholesale, so each template states it explicitly
    # rather than relying on a shared default.
    debit_is_inflow: bool

    def build_prompt(self) -> str:
        """Instructions sent to the vision LLM alongside the PDF bytes."""
        ...

    def extra_references(self, description: str) -> list[str]:
        """Identifiers hidden in the narration, if this format buries any there.

        Optional — `parse_pdf` treats a template without it as having none.
        """
        ...

    def parse_response(self, response_text: str) -> list[dict]:
        """Parse the vision LLM's response into raw row dicts with keys:
        date, description, reference, debit, credit (all pre-normalization, as
        printed in the statement)."""
        ...
