from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows
from app.ingestion.bank_templates.bob_narration import parse_narration

_PROMPT = """\
You are reading the extracted TEXT of a Bank of Baroda statement. The text below
was pulled out of a PDF, so the table is represented with spacing rather than
drawn gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Sr | Tran Date | Value Date | Description | Cheque No | Debit | Credit | Balance

Every row starts with its serial number — this is NOT the date. The date is the
next column. Bank of Baroda suffixes every Balance with "Cr" (e.g.
"1,17,550.00 Cr") — that suffix marks the running balance and carries no meaning
of its own; strip it.

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Tran Date, as printed>", "description": "<Description>",
   "reference": "<Cheque No, or null if blank>",
   "debit": "<Debit as printed, or \\"0\\" if blank>",
   "credit": "<Credit as printed, or \\"0\\" if blank>",
   "balance": "<Balance as printed WITHOUT the trailing \\"Cr\\", or null if blank>"}

READING THE NUMBERS ON A ROW
The trailing numbers on a transaction row are, in order: the transaction amount
(in EITHER Debit or Credit, never both) followed by the Balance (which carries a
trailing "Cr").
  - The LAST number on the row is ALWAYS the Balance. Put it in "balance" (without
    "Cr"). It is a running total. NEVER report it as "debit" or "credit".
  - The other number is the transaction amount. Decide which column it sits in by
    how far left or right it appears relative to the "Debit" and "Credit" headers
    above it.
  - If a row shows only ONE number in total, that number is the Balance and this
    is not a transaction row — skip it.

CONTINUATION LINES
A Description often wraps onto the following line or lines. A line that does NOT
begin with a serial number followed by a date is a continuation of the row above
it: append its text to that row's description. NEVER emit it as a transaction of
its own.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class BankOfBarodaTemplate:
    template_name = "bob"
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
