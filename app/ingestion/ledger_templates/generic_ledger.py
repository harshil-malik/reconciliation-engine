from __future__ import annotations

import json
import re

_PROMPT = """\
You are extracting transaction rows from an internal accounting ledger PDF export \
(e.g. from Tally, Zoho Books, QuickBooks, or a similar accounting system).

The ledger has a transaction table, typically with columns like: Date, Particulars \
(or Narration), Voucher No. (or Reference), Debit, Credit.

Return ONLY a JSON array (no prose, no markdown code fences) where each element is:
  {"date": "<Date, as printed>", "description": "<Particulars/Narration>",
   "reference": "<Voucher No./Reference, or null if blank>",
   "debit": "<Debit amount as printed, or \\"0\\" if blank>",
   "credit": "<Credit amount as printed, or \\"0\\" if blank>"}

Skip header/footer/summary rows (opening balance, closing balance, page totals).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class GenericLedgerTemplate:
    """Not bank-specific — internal ledgers come from whatever accounting system
    the CA's client uses, so unlike bank statements this isn't tied to one target
    institution's layout. One generic template covers the common Date/Particulars/
    Voucher/Debit/Credit shape; a client with an unusual export format would get its
    own template later, the same way each bank does.
    """

    template_name = "generic_ledger"

    def build_prompt(self) -> str:
        return _PROMPT

    def parse_response(self, response_text: str) -> list[dict]:
        text = response_text.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is None:
            raise ValueError(
                f"No JSON array found in vision model response: {text[:200]!r}"
            )
        return json.loads(match.group(0))
