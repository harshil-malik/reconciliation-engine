from __future__ import annotations

from app.ingestion.bank_templates.base import parse_json_rows

_PROMPT_TEMPLATE = """\
You are reading the extracted TEXT of an internal accounting ledger export (Tally,
Zoho Books, QuickBooks, or similar). The text was pulled out of a PDF, so the table
is represented with spacing rather than drawn gridlines.

The ledger table has columns like: Date, Particulars (or Narration), Voucher No.
(or Reference), Debit, Credit, and often a running Balance.

Return ONLY a JSON object (no prose, no markdown code fences) of the form
{{"rows": [ ... ]}}, where each element is:
  {{"date": "<Date, as printed>", "description": "<Particulars/Narration>",
   "reference": "<Voucher No./Reference, or null if blank>",
   "debit": "<Debit amount as printed, or \\"0\\" if blank>",
   "credit": "<Credit amount as printed, or \\"0\\" if blank>",
   "balance": "<running Balance as printed, or null if there is no balance column>"}}

READING THE NUMBERS ON A ROW
A transaction row carries an amount in EITHER the Debit or the Credit column, never
both, optionally followed by a running Balance.
  - When a row ends with a running Balance, that LAST number is the balance. Put it
    in "balance". It is a running total — NEVER report it as "debit" or "credit".
  - {direction_hint}

CONTINUATION LINES
A narration often wraps onto the following line. A line that does NOT begin with a
date is a continuation of the row above it: append its text to that row's
description. NEVER emit it as a transaction of its own. A transaction row always
begins with a date and its description is text — a row whose description would be a
bare number is a misread, not a transaction.

Always include every key, "reference" and "balance" included — use null when the
value is absent rather than omitting the key.

Skip header/footer/summary rows (opening balance, closing balance, page totals,
carried-forward lines).
Preserve numbers and text exactly as printed — do not reformat dates or strip
leading zeros or thousands separators.
"""

_PARTY_HINT = (
    "Record the Debit column amount in \"debit\" and the Credit column amount in "
    "\"credit\", exactly as the ledger labels them."
)

_BANK_HINT = (
    "This is a BANK account ledger, so Debit means money received INTO the bank and "
    "Credit means money paid OUT. Still record each amount under the column the "
    "ledger prints it in — \"debit\" for the Debit column, \"credit\" for the Credit "
    "column — and do not try to convert anything."
)


class GenericLedgerTemplate:
    """Expense/party-style ledger — a supplier, customer, or expense account.

    In these books a Debit is an expense or a payment out, matching the sign
    convention of a bank statement's Withdrawal column, so `debit_is_inflow` is
    False. If you are reconciling the client's *bank* account against a bank
    statement, use BankAccountLedgerTemplate instead — its signs are mirrored.
    """

    template_name = "generic_ledger"
    debit_is_inflow = False

    def build_prompt(self) -> str:
        return _PROMPT_TEMPLATE.format(direction_hint=_PARTY_HINT)

    def parse_response(self, response_text: str) -> list[dict]:
        return parse_json_rows(response_text)


class BankAccountLedgerTemplate:
    """The client's own Bank A/c ledger — the usual counterpart to a bank statement.

    This is the mirror image of the statement: the bank's books and the client's
    books record the same event from opposite sides. Money arriving is a Deposit
    (credit) on the statement but a Debit to the bank asset account in the client's
    ledger, so `debit_is_inflow` is True.

    Choosing the wrong one of these two templates inverts every amount in the file,
    which does not fail loudly — it just makes almost nothing reconcile.
    """

    template_name = "bank_account_ledger"
    debit_is_inflow = True

    def build_prompt(self) -> str:
        return _PROMPT_TEMPLATE.format(direction_hint=_BANK_HINT)

    def parse_response(self, response_text: str) -> list[dict]:
        return parse_json_rows(response_text)
