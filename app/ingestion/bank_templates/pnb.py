from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows
from app.ingestion.bank_templates.pnb_narration import parse_narration

_PROMPT = """\
You are reading the extracted TEXT of a Punjab National Bank (PNB) statement. The
text below was pulled out of a PDF, so the table is represented with spacing
rather than drawn gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Sl. No. | Transaction Date | Instrument Id | Amount | Type | Balance | Remarks

Every row starts with its serial number — this is NOT the date. The date is the
next column. Unlike most bank statements, PNB has NO separate Debit/Credit
columns: there is one "Amount" column, and a "Type" column alongside it holding
either "Dr" (money out) or "Cr" (money in). The Remarks (narration) column comes
LAST, after Balance, not before the amount.

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Transaction Date, as printed>", "description": "<Remarks>",
   "reference": "<Instrument Id, or null if blank or \\"-\\">",
   "debit": "<Amount as printed if Type is Dr, else \\"0\\">",
   "credit": "<Amount as printed if Type is Cr, else \\"0\\">",
   "balance": "<Balance as printed, or null if blank>"}

READING THE NUMBERS ON A ROW
Each row prints exactly two numbers: the Amount, then the Balance.
  - The FIRST number is the Amount. Look at the Type cell right after it: "Dr"
    means this is a "debit" (money out — Remarks describes a payment), "Cr" means
    it is a "credit" (money in). NEVER put a value in both "debit" and "credit".
  - The SECOND number is the Balance — a running total. NEVER report it as
    "debit" or "credit".

CONTINUATION LINES
Remarks occasionally wraps onto the following line. A line that does NOT begin
with a serial number followed by a date is a continuation of the row above it:
append its text to that row's description. NEVER emit it as a transaction of its
own.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class PNBBankTemplate:
    template_name = "pnb"
    # Bank statement, written from the bank's side: a Credit is money in.
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
