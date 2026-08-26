---
name: check-reconciliation
description: Verify the reconciliation engine after any change to extraction, matching, anomaly rules or the confirmer prompt. Runs the test suite, the arithmetic verifier against the known-good fixture, and — when the model is involved — the confirmer eval. Use before committing anything that touches how rows are read or paired.
---

Run these in order. Stop at the first failure: a later step's output is
uninterpretable if an earlier one is broken.

## 1. Test suite

```bash
cd ~/v-01 && source .venv/bin/activate && python -m pytest -q
```

Every test names the bug it exists for. If one fails, read its docstring before
changing it — several encode failures found on real statements, and "fixing" the
test would reintroduce the bug.

## 2. Arithmetic verifier against the known-good fixture

```bash
python scripts/verify_reconciliation.py sample_data/bank_statement.pdf \
                                        sample_data/internal_ledger.pdf
```

If `sample_data/` is missing, regenerate it: `python scripts/make_sample_data.py`.

All four checks must pass. Expected shape: 13 matched by Stage 1, 1 by Stage 1.5,
one legitimately unmatched row per side, and the Kumar Stationers duplicate flagged
despite both its legs reconciling.

Note which check fails:

- **check 1 (self-consistency)** — the FILE contradicts itself, not the engine
- **checks 2-4** — the engine. Completeness, the reconciliation identity, or
  matched-pair sanity

## 3. Confirmer eval — only if the model was touched

Required after ANY change to the confirmer prompt, the model, or
`confidence_threshold`. Needs both llama-servers running.

```bash
python scripts/eval_confirmer.py
```

**FALSE-POS must be 0.** A wrong match silently hides a real discrepancy; a missed
match only costs a reviewer a minute. Recall is secondary.

This has swung from 0 to 4 false positives on a prompt edit that looked like an
improvement. Never eyeball it.

## 4. If a real statement is involved

```bash
python scripts/verify_reconciliation.py private/<bank>.pdf private/<ledger>.pdf
```

Watch the terminal, not just the workbook — several bugs looked fine in Excel and
were only visible in the logs:

- `Parsed N rows ... without the model` — the deterministic path handled it
- `Corrected N amount(s) ... against the running balance` — read each one; a few is
  normal, many means the column geometry is being misread
- `does not foot` — a row was dropped or double-counted
- `prints no opening balance` — row 1 rests on column position alone; check it by hand

## Rules this project keeps relearning

- Deterministic beats model. Every time logic moved out of the model, accuracy rose.
- Measure thresholds, never guess. Pick the middle of a plateau and record the
  measurement in a comment beside the value.
- Fail loudly. A wrong number in a financial report is worse than no report.
- Verify against the source document, not the generated Excel.
