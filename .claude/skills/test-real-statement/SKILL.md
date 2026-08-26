---
name: test-real-statement
description: Safely test the engine against a real client bank statement or ledger. Covers handling the file so it never reaches git, what to check in the extraction, and how to read the terminal diagnostics. Use whenever a genuine (non-synthetic) financial document is being run through the pipeline.
---

Real statements are client financial data. Handle them accordingly, even redacted.

## 1. Put the file somewhere it cannot be committed

```bash
cp ~/Downloads/<file>.pdf ~/v-01/private/
git check-ignore -q private/<file>.pdf && echo "IGNORED" || echo "NOT IGNORED - STOP"
```

`private/` is gitignored. Confirm it, do not assume it.

Encrypted PDFs (HDFC e-statements often are) must be unlocked first — open in
Preview and export a new PDF, which drops the password.

## 2. Extract and check against the printed page

```bash
python -c "
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.bank_templates.hdfc import HDFCBankTemplate
from app.ingestion.vision_client import LocalPDFExtractor
for t in parse_pdf('private/<file>.pdf', source='bank', template=HDFCBankTemplate(), extractor=LocalPDFExtractor()):
    print(f'{t.date} {t.amount:>14} {str(t.reference or \"\"):18s} {t.description[:50]}')
"
```

Spot-check by hand: first row, last row, the largest amount, any row with an odd
layout. Confirm the row count matches the statement's own.

## 3. Read the terminal as carefully as the rows

- `Parsed N rows ... without the model` — deterministic path. Best case.
- `Corrected N amount(s) ... against the running balance` — the audit caught misreads.
  A few is normal; many means the column geometry is wrong.
- `does not foot` — a row was dropped or double-counted. Rejected, correctly.
- `prints no opening balance` — row 1 rests on column position alone. Verify by hand.
- `PDFExtractionError` — extraction refused rather than publish wrong figures. The
  message names the row; compare it against the statement.

## 4. Verify arithmetically

```bash
python scripts/verify_reconciliation.py private/<bank>.pdf private/<ledger>.pdf
```

A failure in check 1 is the document contradicting itself. Failures in 2-4 point at
the engine.

## 5. Read the report as a reviewer

- **Summary** — `UNEXPLAINED` must be 0.00
- **Review - Amount / Date / Weak Evidence** — the pairings needing a human
- **Unmatched - Bank / Ledger** — each says why; on a real reconciliation these
  should be explainable (uncleared cheques, un-booked charges, deposits in transit)

## 6. If a fixture is derived from the file

Use invented identifiers of the **same character length** — column-alignment tests
depend on exact positions. Account holder, account number, customer id, IFSC,
transaction ids and counterparty names must all be replaced.

If real identifiers ever reach a commit, they must be purged from history with
`git filter-repo --replace-text` **before** any push. Scrubbing the working tree
alone leaves them in history.
