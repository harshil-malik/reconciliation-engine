from __future__ import annotations

import json
import re

_PROMPT = """\
You are extracting transaction rows from an HDFC Bank statement PDF.

The statement has a transaction table with these columns: Date, Narration,
Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance.

Return ONLY a JSON array (no prose, no markdown code fences) where each element is:
  {"date": "<Date, as printed>", "description": "<Narration>",
   "reference": "<Chq./Ref.No., or null if blank>",
   "debit": "<Withdrawal Amt as printed, or \\"0\\" if blank>",
   "credit": "<Deposit Amt as printed, or \\"0\\" if blank>"}

Skip header/footer/summary rows (opening balance, statement summary, page totals).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class HDFCBankTemplate:
    template_name = "hdfc"

    def build_prompt(self) -> str:
        return _PROMPT

    def parse_response(self, response_text: str) -> list[dict]:
        text = response_text.strip()
        # Vision models sometimes wrap JSON in markdown fences despite instructions.
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is None:
            raise ValueError(
                f"No JSON array found in vision model response: {text[:200]!r}"
            )
        return json.loads(match.group(0))
