from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows
from app.ingestion.bank_templates.kotak_narration import parse_narration

_PROMPT = """\
You are reading the extracted TEXT of a Kotak Mahindra Bank statement. The text
below was pulled out of a PDF, so the table is represented with spacing rather
than drawn gridlines. Work from the text as given.

The transaction table has these columns, left to right:
  Date | Narration | Chq/Ref No | Withdrawal(Dr)/Deposit(Cr) | Balance

Kotak merges withdrawal and deposit into ONE money column: each figure in it is
tagged inline with "(Dr)" (money out) or "(Cr)" (money in), e.g. "12,450.00(Dr)".
The Balance is a second figure, always tagged "(Cr)" since it is a running total,
e.g. "1,17,550.00(Cr)". Every row therefore prints two tagged numbers.

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{"rows": [ ... ]}, where each element is:
  {"date": "<Date, as printed>", "description": "<Narration>",
   "reference": "<Chq/Ref No, or null if blank or \\"-\\">",
   "debit": "<the transaction figure, without its (Dr)/(Cr) tag, if it is tagged (Dr); else \\"0\\">",
   "credit": "<the transaction figure, without its (Dr)/(Cr) tag, if it is tagged (Cr); else \\"0\\">",
   "balance": "<the Balance figure, without its (Cr) tag, or null if blank>"}

READING THE NUMBERS ON A ROW
A row prints two tagged numbers: the transaction amount, then the Balance.
  - The LAST number is ALWAYS the Balance — always tagged "(Cr)". Put it in
    "balance", with the tag stripped. NEVER report it as "debit" or "credit".
  - The FIRST number is the transaction amount. Read ITS OWN tag — not the
    Balance's — to decide direction: "(Dr)" -> "debit", "(Cr)" -> "credit". Do
    not assume every merged-column entry is a debit; Kotak uses both tags in this
    same column.

CONTINUATION LINES
Narration occasionally wraps onto the following line. A line that does NOT begin
with a date is a continuation of the row above it: append its text to that row's
description. NEVER emit it as a transaction of its own.

Always include every key, "reference" and "balance" included — use null when the
cell is blank rather than omitting the key.

Skip header/footer/summary rows (opening balance, statement summary, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed (aside from stripping the (Dr)/(Cr)
tag as instructed above) — do not reformat dates or strip leading zeros or
thousands separators.
"""


class KotakBankTemplate:
    template_name = "kotak"
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
