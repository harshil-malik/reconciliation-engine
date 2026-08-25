from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel

from app.local_llm import LlamaCppClient
from app.schema import Transaction


class ConfirmationResult(BaseModel):
    is_match: bool
    confidence: float
    reasoning: str


# Property order is load-bearing, not cosmetic. Grammar-constrained decoding emits
# fields in schema order, so `reasoning` must come first: it makes the model state
# its rationale before committing to a verdict, rather than picking a verdict and
# rationalizing afterwards. With the verdict first, a 3B model was observed
# answering is_match=False directly after reasoning that described a valid match
# ("different date, likely due to a clearing lag") — the cheap local equivalent of
# chain-of-thought, and worth several points of recall here.
_CONFIRMATION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "is_match": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["reasoning", "is_match", "confidence"],
}


class MatchConfirmer(Protocol):
    def confirm(
        self, bank_txn: Transaction, ledger_txn: Transaction
    ) -> ConfirmationResult: ...


_PROMPT_TEMPLATE = """\
You are confirming whether a bank statement row and a ledger row refer to the same \
underlying transaction, for financial reconciliation.

Bank row:
  description: {bank_description}
  reference: {bank_reference}
  amount: {bank_amount}
  date: {bank_date}

Ledger row:
  description: {ledger_description}
  reference: {ledger_reference}
  amount: {ledger_amount}
  date: {ledger_date}

Already computed for you (do not recompute):
  amount difference: {amount_diff} ({amount_pct} of the bank amount)
  date difference: {date_diff} day(s)

Decide if these are the same transaction.

These differences are NORMAL between a bank statement and a ledger, and on their own \
are NOT reasons to reject a match:
  - dates a few days apart (bank clearing lag vs ledger entry date)
  - small amount differences from bank fees or rounding
  - abbreviated, reworded, or shorthand descriptions of the same counterparty \
(e.g. "R.K. SUPPLIES" vs "Rajesh Kumar Supplies")

But a shared amount and date are NOT evidence of a match on their own. The \
counterparty must be recognizably the SAME party in both rows — the same name, an \
abbreviation of it, or an obvious shorthand for it.

Reject the match when:
  - the two rows name different parties (e.g. "GLOBEX LTD" vs "Initech Pvt Ltd" are \
different companies, not a rewording — reject)
  - neither row identifies a party that can be tied to the other
  - the purposes are clearly different (e.g. office rent vs raw material purchase)
  - the amounts are too far apart to be a fee or rounding difference

When in doubt, reject. An unmatched row costs an accountant a minute of review; a \
wrong match silently hides a real discrepancy in the books.

Return ONLY a JSON object (no prose, no markdown fences). State `reasoning` FIRST and \
let it lead you to the verdict; `confidence` is your probability that the two rows are \
the SAME transaction (so a rejected match should have a LOW confidence):
  {{"reasoning": "<one short sentence>", "is_match": <true|false>, "confidence": <0.0-1.0>}}
"""


def _build_prompt(bank_txn: Transaction, ledger_txn: Transaction) -> str:
    """Render the confirmation prompt, with the amount/date deltas precomputed.

    Two details here are load-bearing for small local models:

    1. Amounts are formatted canonically (fixed 2dp), because the model proved
       sensitive to cosmetic formatting — the same pair scored 0.8 written as
       "-10050" and 0.3 written as "-10050.00". Normalizing removes that variance.
    2. The differences are computed in Python rather than left to the model. A 3B
       model is unreliable at arithmetic, and "is this gap explainable as a bank
       fee?" is a judgment call it can only make once the gap is stated correctly.
    """
    amount_diff = abs(bank_txn.amount - ledger_txn.amount)
    bank_magnitude = abs(bank_txn.amount)
    amount_pct = (
        f"{(amount_diff / bank_magnitude * 100):.2f}%"
        if bank_magnitude
        else "n/a"
    )
    return _PROMPT_TEMPLATE.format(
        bank_description=bank_txn.description,
        bank_reference=bank_txn.reference or "none",
        bank_amount=f"{bank_txn.amount:.2f}",
        bank_date=bank_txn.date.isoformat(),
        ledger_description=ledger_txn.description,
        ledger_reference=ledger_txn.reference or "none",
        ledger_amount=f"{ledger_txn.amount:.2f}",
        ledger_date=ledger_txn.date.isoformat(),
        amount_diff=f"{amount_diff:.2f}",
        amount_pct=amount_pct,
        date_diff=abs((bank_txn.date - ledger_txn.date).days),
    )


class LocalMatchConfirmer:
    """Confirms candidate pairs with a locally hosted model via llama.cpp — the
    default, so Stage 2 runs offline with no API key.

    Decoding is grammar-constrained to `_CONFIRMATION_SCHEMA`, which matters more
    here than the exact model choice: a 3B model asked politely for JSON will
    sometimes answer in prose, but it cannot when sampling is restricted to tokens
    that keep the output schema-valid.

    A small model is also a weaker judge than a frontier one, so its confidence
    scores skew less reliable. That is contained by design — Stage 1 already
    resolves the majority of rows deterministically, this only ever sees Stage 1's
    leftovers, and anything below `confidence_threshold` falls through to the CA
    review queue rather than being asserted as a match.
    """

    def __init__(self, client: LlamaCppClient | None = None):
        self._client = client or LlamaCppClient()

    def confirm(
        self, bank_txn: Transaction, ledger_txn: Transaction
    ) -> ConfirmationResult:
        prompt = _build_prompt(bank_txn, ledger_txn)
        text = self._client.complete_json(
            prompt, json_schema=_CONFIRMATION_SCHEMA, max_tokens=256
        )
        return _parse_confirmation(text)


class ClaudeMatchConfirmer:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"):
        import os

        import anthropic

        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._model = model

    def confirm(
        self, bank_txn: Transaction, ledger_txn: Transaction
    ) -> ConfirmationResult:
        prompt = _build_prompt(bank_txn, ledger_txn)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return _parse_confirmation(text)


class GeminiMatchConfirmer:
    """Alternate MatchConfirmer implementation to ClaudeMatchConfirmer above, using
    Gemini instead — same prompt, same JSON contract, just a different model behind
    the same protocol so main.py can point at whichever provider's key is set."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-3.6-flash"):
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        # Lazy for the same reason as the other Gemini clients: instantiated as a
        # FastAPI dependency on every /reconcile call, even when Stage 1 resolves
        # everything and confirm() never gets called.
        if self._client is None:
            import os

            from google import genai

            self._client = genai.Client(
                api_key=self._api_key or os.environ.get("GEMINI_API_KEY")
            )
        return self._client

    def confirm(
        self, bank_txn: Transaction, ledger_txn: Transaction
    ) -> ConfirmationResult:
        prompt = _build_prompt(bank_txn, ledger_txn)
        response = self._get_client().models.generate_content(
            model=self._model, contents=prompt
        )
        return _parse_confirmation(response.text or "")


def _parse_confirmation(text: str) -> ConfirmationResult:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if match is None:
        raise ValueError(
            f"No JSON object found in confirmation response: {text[:200]!r}"
        )
    return ConfirmationResult(**json.loads(match.group(0)))
