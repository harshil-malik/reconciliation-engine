---
name: add-bank-template
description: Add support for a new bank's statement format (ICICI, SBI, Axis, Kotak — anything beyond HDFC). Covers inspecting the text layer, wiring the template, handling that bank's narration formats, and locking each layout quirk into a regression test. Use when a statement from an unsupported bank needs to parse.
---

Per the spec, banks are added and validated **one at a time**, never as a generic
parser. Work in this order — earlier steps are cheaper and catch more.

## 1. Look at the text layer BEFORE writing any code

```bash
python -c "
from pypdf import PdfReader
t=''.join(p.extract_text(extraction_mode='layout') or '' for p in PdfReader('private/NEW.pdf').pages)
for i,l in enumerate([x.rstrip() for x in t.split(chr(10))][:50]): print(f'{i:3d}|{l}')
"
```

| What you see | What it means |
| --- | --- |
| Empty | Scanned PDF. Out of scope — needs OCR or a hosted vision model. Stop. |
| Columns aligned | `layout_table.py` should handle it. Best case. |
| Columns collapsed | Layout mode failed; the model fallback runs and will be unreliable. |
| No running-balance column | **Significant.** The strongest safety net cannot run. |
| Two reference-like columns | The rightmost wins — an internal voucher number is useless for matching. |

## 2. Try the existing deterministic parser first

Most of the work is already generic. Run `parse_layout_table` on the text and see
how far it gets before writing anything bank-specific.

## 3. Create the template

Copy `app/ingestion/bank_templates/hdfc.py`. It must declare:

- `template_name`
- `debit_is_inflow` — **False** for a bank statement (a Deposit is money in). Getting
  this backwards inverts every amount and fails silently.
- `build_prompt()` — only used when the deterministic parser cannot read the table
- `extra_references()` / `counterparty()` — if the bank buries transaction ids in the
  narration, as HDFC does

Register it in `_BANK_TEMPLATES` in `app/main.py`. It appears in the UI automatically.

## 4. Narration formats

If the bank uses its own prefixes, add a parser modelled on
`hdfc_narration.py`, returning `kind`, `reference` and `counterparty`.

This matters more than it looks: on HDFC it lifted reference-backed matches from 5
to 14 of 16, because the bank prints a placeholder in its reference column while
carrying the real id in the narration.

Return **nothing** rather than a guess for bank charges and interest — inventing a
counterparty manufactures matching signal that does not exist.

## 5. Fix in this order of preference

1. `app/ingestion/layout_table.py` — column detection, row filtering, continuations.
   Generic, no model, best returns.
2. The bank template — only affects the model fallback path.
3. The model — last resort.

## 6. Lock every quirk into a test

For each real-world oddity found, add a test to `tests/test_layout_table.py` using
the actual layout, **anonymised**. Real statements have produced: opening-balance
rows that look like transactions, all-zero placeholder references, references wider
than their heading, voucher columns before the narration, page furniture between
blocks, and wrapped narrations carrying invoice numbers.

Then run `/check-reconciliation`.

## Never commit the statement itself

Real files live in `private/`, which is gitignored. Test fixtures must use invented
identifiers of the **same character length**, since the column-alignment tests depend
on exact positions.
