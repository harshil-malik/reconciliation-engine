from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows
from app.ingestion.bank_templates.hdfc_narration import parse_narration

_PROMPT = """\
You are reading the extracted TEXT of an HDFC Bank statement. The text below was
pulled out of a PDF, so the table is represented with spacing rather than drawn
gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Date | Narration | Chq./Ref.No. | Value Dt | Withdrawal Amt. | Deposit Amt. | Closing Balance

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Date, as printed>", "description": "<Narration>",
   "reference": "<Chq./Ref.No., or null if blank>",
   "debit": "<Withdrawal Amt as printed, or \\"0\\" if blank>",
   "credit": "<Deposit Amt as printed, or \\"0\\" if blank>",
   "balance": "<Closing Balance as printed, or null if blank>"}

READING THE NUMBERS ON A ROW
The trailing numbers on a transaction row are, in order: the transaction amount
(in EITHER the Withdrawal or the Deposit column, never both) followed by the
Closing Balance.
  - The LAST number on the row is ALWAYS the Closing Balance. Put it in "balance".
    It is a running total. NEVER report it as "debit" or "credit".
  - The other number is the transaction amount. Decide which column it sits in by
    how far left or right it appears relative to the "Withdrawal Amt." and
    "Deposit Amt." headers above it:
      nearer the Withdrawal heading -> "debit", and "credit" is "0"
      nearer the Deposit heading    -> "credit", and "debit" is "0"
  - If a row shows only ONE number in total, that number is the Closing Balance
    and this is not a transaction row — skip it.

CONTINUATION LINES
A narration often wraps onto the following line or lines. A line that does NOT
begin with a date is a continuation of the row above it: append its text to that
row's description. NEVER emit it as a transaction of its own. A transaction row
always begins with a date, and its description is text — a row whose description
would be a bare number is a misread, not a transaction.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class HDFCBankTemplate:
    template_name = "hdfc"
    # Bank statement, written from the bank's side: a Deposit (credit) is money in.
    debit_is_inflow = False

    def build_prompt(self) -> str:
        return _PROMPT

    def extra_references(self, description: str) -> list[str]:
        reference = parse_narration(description).reference
        return [reference] if reference else []

    def counterparty(self, description: str) -> str | None:
        return parse_narration(description).counterparty

    def parse_response(self, response_text: str) -> list[dict]:
        return parse_json_rows(response_text)
