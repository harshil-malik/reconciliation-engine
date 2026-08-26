# Workflow — testing against a real bank statement

The plan. `PROGRESS.md` holds the current state.

**Next session:** a real (redacted) HDFC statement and the matching client ledger
arrive. That is the one thing that will tell us whether any of this generalizes —
everything so far is synthetic plus two sample PDFs, and both of those turned out to
contradict themselves.

---

## Before touching the real file

```bash
cd ~/v-01
source .venv/bin/activate && python -m pytest -q      # expect 165 passed
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192 &
llama-server -m models/Qwen3-Embedding-0.6B-Q8_0.gguf --port 8081 --embeddings &
python scripts/verify_reconciliation.py sample_data/bank_statement.pdf \
                                        sample_data/internal_ledger.pdf
```

The fixture must still pass all four checks. If it doesn't, fix that first — a
known-good baseline is what makes the real file's failures interpretable.

**Handle the real file carefully.** It is client financial data even when redacted:
keep it out of git (add its directory to `.gitignore` before copying it in), and do
not paste its contents anywhere. Everything runs locally, so nothing leaves the
machine on its own — keep it that way.

## Step 1 — Look at the text layer before running anything

Do not start with `/reconcile`. Start by seeing what the extractor sees:

```bash
python -c "
from pypdf import PdfReader
t=''.join(p.extract_text(extraction_mode='layout') or '' for p in PdfReader('REAL.pdf').pages)
for i,l in enumerate([x.rstrip() for x in t.split(chr(10))][:40]): print(f'{i:3d}|{l}')
"
```

What to look for, and what each means:

| Observation | Implication |
| --- | --- |
| Empty / no text | Scanned PDF. Out of scope — needs OCR or a hosted vision model. Stop here. |
| Columns visibly aligned | Good. The deterministic table parser should handle it. |
| Columns collapsed into one stream | Layout mode failed. The model fallback will run and will probably be unreliable. |
| Header repeated mid-document | Page breaks — check the parser doesn't emit header rows as transactions. |
| Multi-line narrations | Check they attach to the row above rather than becoming phantom rows. |
| No running-balance column | **Significant.** The balance audit cannot run, so the strongest safety net is gone and amounts rest on column position alone. |

## Step 2 — Extract, and check the rows against the printed page

```bash
python -c "
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.vision_client import LocalPDFExtractor
for t in parse_pdf('REAL.pdf', source='bank', template=HDFCBankTemplate(), extractor=LocalPDFExtractor()):
    print(f'{t.date} {t.amount:>14} {str(t.reference or \"\"):14s} {t.description[:50]}')
"
```

Read the terminal output as carefully as the rows:

- `Parsed N rows from the PDF table layout, without the model` — the deterministic
  path handled it. Best case.
- `Corrected N amount(s) ... against the running balance` — the audit caught
  misreads. Worth reading each one: a few is normal, many means the column geometry
  is being misread and the template needs work.
- `prints no opening balance, so the first row ... rests on its column position` —
  check row 1 by hand.
- `PDFExtractionError` — extraction refused rather than publishing wrong figures.
  The message names the offending row; compare it against the statement.

Then **spot-check against the actual PDF**: first row, last row, the largest amount,
any row with an unusual layout. Confirm the count matches the statement's own
transaction count.

## Step 3 — Reconcile, then verify arithmetically

```bash
python scripts/verify_reconciliation.py REAL_BANK.pdf REAL_LEDGER.pdf
```

This is the objective test — it doesn't require trusting the engine:

1. **Self-consistency** — do the printed figures agree with the file's own running
   balance? Failure here is the *document's* problem, not the engine's.
2. **Completeness** — every transaction accounted for exactly once.
3. **Reconciliation identity** — is every rupee of difference attributable to a named
   item? This is the check a CA applies to a BRS.
4. **Matched-pair sanity.**

Failures in 2–4 point at the engine. A failure in 1 points at the file.

## Step 4 — Read the report like a reviewer

Open the workbook and go to the **Matched** tab's `corroboration` column first:

- `reference` — strongest, a shared cheque/UTR/voucher number.
- `description` — payees correspond.
- `amount_and_date_only` — **review these**. Nothing but the figures links them, so
  this is where two unrelated same-size same-day payments would be paired.

Then check **Unmatched** — on a real reconciliation these should be explainable
(uncleared cheques, bank charges not yet booked, timing differences). If genuine
matches are sitting there, that is a recall problem worth diagnosing.

Then **Anomalies** — if it floods, the thresholds need calibrating for this client
rather than the rule being wrong. `AnomalyConfig` holds them.

## Step 5 — Expect the HDFC template to need work, and fix it narrowly

Real statements have layouts synthetic ones don't: page headers repeating,
continuation markers, "B/F" and "C/F" lines, multi-currency columns, footers between
transaction blocks.

Fix in this order of preference:

1. **The deterministic parser** (`app/ingestion/layout_table.py`) — column detection,
   row filtering, continuation handling. Best returns, no model involved.
2. **The HDFC template** (`app/ingestion/bank_templates/hdfc.py`) — only affects the
   model fallback path.
3. **Only then** consider the model.

Add a regression test for each real-world quirk found, using the actual text layout
(anonymized) as the fixture. That is how the template stops regressing.

## Then — the standing gap list

Roughly in order of value:

- **More bank templates.** Four of the five target banks are missing. Add one at a
  time, validated against a real statement each, per the spec.
- **Scanned PDF support.** Needs OCR or a hosted vision extractor. Currently refused
  with a clear error, which is a defensible v1 position.
- **Wire `AnomalyConfig` to the API** so thresholds can be set per client.
- **Stage 3's AI layer** — specced, never built. Rules only today. Keep it strictly
  separate from Stage 2 matching, per the spec's explicit design rule.
- **Auth, rate limiting, upload size caps** — before this is exposed to anyone else.
- **Convention auto-detection for CSV/Excel ledgers** (PDF only today).
- **Re-run `scripts/eval_confirmer.py` with real pairs** once there are known-answer
  matches from actual statements. The current 15 cases are my own constructions.

## Rules of engagement, learned the hard way

- **Deterministic beats model, every time.** Each thing moved out of the model made
  the system better. Reach for code first.
- **Measure thresholds; never guess.** Pick the middle of a plateau, and record the
  measurement in a comment next to the value.
- **Fail loudly.** A wrong number in a financial report is worse than no report.
- **Re-run `scripts/eval_confirmer.py` after any confirmer prompt change.** It has
  swung from 0 to 4 false positives on a plausible-looking edit.
- **Verify against the source document, not the Excel output.** Several bugs looked
  fine in the report and were only visible in the terminal or the PDF.
