# Workflow — what to do next

The plan. `PROGRESS.md` holds the current state.

A real HDFC statement and its client ledger now reconcile correctly, and the four
bugs that first contact exposed are fixed and regression-tested. Two gaps remain
before HDFC can be called finished, and both need a document we do not yet have.

---

## Priority 1 — A multi-page statement

The only real statement tested was single-page. Page furniture is handled and
unit-tested (`test_layout_table.py`), but never against a genuine multi-page file.

Expect: repeated column headers, page numbers, "continued" markers, and possibly a
per-page subtotal block. The parser skips furniture it recognises; a real file will
have shapes the synthetic one does not.

```bash
python -c "
from pypdf import PdfReader
t=''.join(p.extract_text(extraction_mode='layout') or '' for p in PdfReader('private/REAL.pdf').pages)
lines=[x.rstrip() for x in t.split(chr(10))]
print(f'{len(lines)} lines')
for i,l in enumerate(lines):
    if l.strip(): print(f'{i:3d}|{l}')
" | less
```

Read the page boundaries specifically. Then extract and confirm the row count
matches the statement's own, and that it **foots** — the foot check is what catches
a page's worth of rows being silently dropped.

## Priority 2 — A statement spanning a month boundary

We ignore the `Value Dt` column entirely and match on transaction date. That was
correct for the file tested, but a cheque issued 30-Jun and cleared 02-Jul appears
in the June ledger and the July statement. Matching on transaction date alone would
miss it, and it would surface as an unmatched row on each side — a false discrepancy
that looks exactly like a real one.

Two real cheques on the tested statement carried a value-date lag (`txn 05/07, value
07/07`), so the column is populated in the text layer. It is not, however, extracted:
`layout_table._header_columns` classifies "Value Dt" as a date and first-occurrence
wins, so the leading Date column keeps the slot and the value date is deliberately
skipped over (`_read_reference` even names it as something not to swallow). There is
no `value_date` on `Transaction`. Capturing it is a prerequisite for this work, not
something already done.

Decide, with a real month-boundary file in hand, whether Stage 1 should consider
value date as an alternative when transaction date fails.

## Priority 3 — More bank templates

Four of the five target banks have none. Use `/add-bank-template`, which encodes the
process: read the text layer before writing code, prefer the deterministic parser
over the template and the template over the model, and lock every quirk into a test.

Add one at a time, validated against a real statement each, per the spec.

---

## Running a real statement

Use `/test-real-statement`. In short:

```bash
cp ~/Downloads/<file>.pdf ~/v-01/private/
git check-ignore -q private/<file>.pdf && echo IGNORED || echo "STOP"
python scripts/verify_reconciliation.py private/<bank>.pdf private/<ledger>.pdf
```

Read the terminal as carefully as the output — several bugs looked fine in the
workbook and were only visible in the logs:

- `Parsed N rows ... without the model` — the deterministic path handled it
- `foots: N rows total X` — the file agrees with its own printed totals
- `Corrected N amount(s) ... against the running balance` — a few is normal; many
  means the column geometry is being misread
- `does not foot` — a row was dropped or double-counted
- `prints no opening balance` — row 1 rests on column position alone; check it
- `PDFExtractionError` — extraction refused rather than publish wrong figures

## Reading the result

Nine workbook tabs, mirrored by the browser dashboard:

- **Summary** — the balancing proof. `UNEXPLAINED` must be `0.00`; anything else
  means a row was mismatched, double-counted or dropped.
- **Matched** — every pairing, with its date gap, amount gap and corroboration
- **Review - Amount / Date / Weak Evidence** — the pairings a person should look at.
  `amount_and_date_only` is where two unrelated same-size same-day payments would be
  paired.
- **Unmatched - Bank / Ledger** — opposite meanings on a reconciliation, each row
  explained. On a real file these should be recognisable: uncleared cheques,
  un-booked charges, deposits in transit.
- **Anomalies** — if it floods, calibrate `AnomalyConfig` for this client rather
  than assuming the rule is wrong.

## The standing gap list

Roughly in order of value:

- **Persist an `AnomalyConfig` per client.** The config now reaches the API — both
  reconcile routes take an `anomaly_config` JSON field and `GET /anomaly-config`
  returns the defaults to edit — but nothing stores it, so an engagement's thresholds
  have to be re-sent every run.
- **Source grounding, tiers 2 and 3.** Tier 1 (file + page/line + verbatim text) is
  built. Tier 2 would add cell bounding boxes: measured on the real statement,
  pypdf's `visitor_text` hook yields 160 positioned fragments across 31 y-rows with
  23 distinct x positions, so per-cell coordinates are available with no new
  dependency. Tier 3 (a rendered page image with the cell highlighted) needs a
  rasteriser — PyMuPDF or pdf2image — and is the only part that does. Consider
  whether it earns the dependency: highlighting the character range in the source
  line already shown may serve a reviewer as well, and works for CSV and Excel too.
- **Scanned PDF support** — needs OCR or a hosted vision extractor. Currently
  refused with a clear error, a defensible v1 position.
- **Stage 3's AI layer** — specced, never built. Rules only. Keep it strictly
  separate from Stage 2 matching, per the spec's explicit design rule.
- **Auth and rate limiting** — before this is exposed to anyone else. An upload size
  cap is in place (`MAX_UPLOAD_MB`, default 25).
- **Re-run `scripts/eval_confirmer.py` with real pairs** once known-answer matches
  from actual statements exist. The current 15 cases are constructions.
- **Reconsider whether Stage 2 earns its place** — it has contributed nothing on any
  dataset since Stage 1.5 landed. Keeping it costs nothing when it does not fire,
  but do not mistake it for a working stage.

## Rules of engagement, learned the hard way

- **Deterministic beats model, every time.** Everything moved out of the model made
  the system better. Reach for code first.
- **Measure thresholds; never guess.** Pick the middle of a plateau, and record the
  measurement in a comment next to the value.
- **Fail loudly.** A wrong number in a financial report is worse than no report.
- **Re-run `scripts/eval_confirmer.py` after any confirmer prompt change.** It has
  swung from 0 to 4 false positives on a plausible-looking edit.
- **Verify against the source document, not the generated output.** Several bugs
  looked fine in the workbook and were visible only in the terminal or the PDF.
- **A test's docstring says which bug it exists for.** Read it before changing the
  test — several encode failures found on real statements.
