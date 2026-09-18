from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows
from app.ingestion.bank_templates.sbi_narration import parse_narration

_PROMPT = """\
You are reading the extracted TEXT of a State Bank of India (SBI) statement. The
text below was pulled out of a PDF, so the table is represented with spacing
rather than drawn gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Txn Date | Value Date | Description | Ref No./Cheque No. | Debit | Credit | Balance

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Txn Date, as printed>", "description": "<Description>",
   "reference": "<Ref No./Cheque No., or null if blank or \\"-\\">",
   "debit": "<Debit as printed, or \\"0\\" if blank>",
   "credit": "<Credit as printed, or \\"0\\" if blank>",
   "balance": "<Balance as printed, or null if blank>"}

READING THE NUMBERS ON A ROW
The trailing numbers on a transaction row are, in order: the transaction amount
(in EITHER the Debit or the Credit column, never both) followed by the Balance.
  - The LAST number on the row is ALWAYS the Balance. Put it in "balance". It is a
    running total. NEVER report it as "debit" or "credit".
  - The other number is the transaction amount. Decide which column it sits in by
    how far left or right it appears relative to the "Debit" and "Credit" headers
    above it.
  - If a row shows only ONE number in total, that number is the Balance and this
    is not a transaction row — skip it.

Each row's Date column repeats the SAME date twice (Txn Date and Value Date) — use
the first (Txn Date) as "date".

CONTINUATION LINES
A description often wraps onto the following line or lines, such as a second line
of a UPI narration. A line that does NOT begin with a date is a continuation of the
row above it: append its text to that row's description. NEVER emit it as a
transaction of its own.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class SBIBankTemplate:
    template_name = "sbi"
    # Bank statement, written from the bank's side: a Credit (deposit) is money in.
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
