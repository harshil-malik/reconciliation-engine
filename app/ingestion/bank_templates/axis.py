from __future__ import annotations

from app.ingestion.bank_templates.axis_narration import parse_narration
from app.ingestion.bank_templates.base import parse_json_rows

_PROMPT = """\
You are reading the extracted TEXT of an Axis Bank statement. The text below was
pulled out of a PDF, so the table is represented with spacing rather than drawn
gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Tran Date | Chq No | Particulars | Debit | Credit | Balance | Init. Br

Chq No sits BEFORE Particulars and is blank on almost every row (only cheque
transactions carry a value there). Init. Br is a trailing branch code
(e.g. "KORMNGL") printed after Balance — it is not part of the transaction data
and must not be read as a reference or an amount.

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Tran Date, as printed>", "description": "<Particulars>",
   "reference": "<Chq No, or null if blank>",
   "debit": "<Debit as printed, or \\"0\\" if blank>",
   "credit": "<Credit as printed, or \\"0\\" if blank>",
   "balance": "<Balance as printed, or null if blank>"}

READING THE NUMBERS ON A ROW
The trailing numbers on a transaction row are, in order: the transaction amount
(in EITHER Debit or Credit, never both) followed by the Balance. Ignore the
branch code text that follows the Balance — it is not a number to report.
  - Of the two numbers, the LAST one is ALWAYS the Balance. Put it in "balance".
    It is a running total. NEVER report it as "debit" or "credit".
  - The other number is the transaction amount. Decide which column it sits in by
    how far left or right it appears relative to the "Debit" and "Credit" headers
    above it.
  - If a row shows only ONE number in total, that number is the Balance and this
    is not a transaction row — skip it.

CONTINUATION LINES
Particulars occasionally wraps onto the following line. A line that does NOT
begin with a date is a continuation of the row above it: append its text to that
row's description. NEVER emit it as a transaction of its own.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""


class AxisBankTemplate:
    template_name = "axis"
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
