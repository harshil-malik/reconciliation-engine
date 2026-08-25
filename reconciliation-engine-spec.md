# AI-Powered Financial Reconciliation & Anomaly Detection Engine — Build Spec

## Purpose
Automate bank-statement-vs-ledger reconciliation for chartered accountants. Replace manual spreadsheet cross-referencing with an engine that ingests financial records in multiple formats, normalizes them, matches transactions deterministically first with AI as a fallback net, flags anomalies, and exports audit-ready reports.

This is a rebuild — a first version was built and found buggy. Building clean from scratch, stage by stage, testing each stage in isolation before stacking the next on top.

## Stack
- **Backend**: FastAPI (Python) — chosen over MERN for this project because the workload is compute/dataframe/LLM-heavy, not a typical CRUD app.
- **Data processing**: pandas
- **PDF extraction**: vision-LLM-based extraction (per-bank templated prompts), not traditional table parsers like pdfplumber/camelot, since layouts vary by bank and text-based vision extraction is more robust across the target banks.
- **Matching**: exact/rule-based logic first, then embeddings + LLM confirmation for the fallback net.

## Scope for v1
- Input formats: CSV, Excel (.xlsx), text-based PDF (not scanned/image PDFs)
- Target: top 5 Indian banks' statement formats specifically (not universal PDF parsing)
- Upload flow: CA drag-and-drops bank statement + internal ledger
- Output: audit-ready report (matched / mismatched / missing / anomalies, each traceable to why)

## Architecture — 3-Stage Pipeline

### Stage 0 — Ingestion & Normalization (build this first, get it rock-solid)
- Parse CSV, Excel, and PDF (via vision-LLM per-bank templates) into one canonical transaction schema:
  - `date`, `amount`, `description`, `reference`, `source` (bank/ledger), `raw_row` (original data for traceability)
- This is the foundation — most downstream bugs trace back to messy normalization, so validate this stage thoroughly with synthetic + real sample data before moving on.
- Build and test per-bank PDF extraction templates one bank at a time, not generically.

### Stage 1 — Deterministic Matching Engine (handles majority of rows)
- Match keys, in order:
  1. Exact amount match (money doesn't fuzz — no tolerance here)
  2. Near/exact date match (tolerance window, e.g. ±2-3 days, to account for bank clearing lag vs ledger entry date)
  3. Fuzzy description/reference match (token similarity or Levenshtein-based, threshold e.g. >0.85) for confirmation when multiple candidates share amount+date
- Output per row: `MATCHED` with the specific rule that fired (fully explainable, no AI cost)
- Rows that don't clear this stage flow into Stage 2.

### Stage 2 — AI Matching Net (fallback only, narrow scope)
- Runs only on rows Stage 1 couldn't resolve.
- Two-step to control cost: embeddings + similarity search first to shortlist plausible candidate pairs, then an LLM call only on close candidates to confirm — avoid O(n×m) LLM calls across all unmatched rows.
- Job is strictly binary/confidence-scored: "is this unmatched bank row the same transaction as this unmatched ledger row?"
- Output per row: `MATCHED (AI)` + confidence score + short natural-language reasoning string (this reasoning is what makes the report audit-ready — a CA should be able to see *why* the AI matched it).
- Anything still unresolved after this stage → CA review queue, bucket: "unmatched" (efficiency problem, not a risk flag).

### Stage 3 — Anomaly Detection (separate from matching, different risk profile)
- Runs on the full reconciled set (matched + unmatched), not just leftovers — an anomaly can exist on an already-matched transaction (e.g. a duplicate payment that matched cleanly on both sides because it happened twice).
- Rule-based first: duplicate payment detection, amounts just under approval thresholds, round-number entries that look manually adjusted, reversed/mirrored entries, timing gaps.
- AI layered on top only for fuzzier judgment calls the rules can't cover.
- Output per flagged row: goes to CA review queue, bucket: "flagged as suspicious" (risk problem, not an efficiency problem).

**Important design rule**: Do not merge Stage 2 (matching) and Stage 3 (anomaly detection) into a single AI pass. Missed matches waste time; missed anomalies hide risk/fraud. Bundling them causes the model to either over-flag (erodes CA trust) or under-flag (buries real anomalies). Keep the CA review section visually split into two distinct buckets — "couldn't match" vs "flagged as suspicious" — so a CA immediately knows what kind of attention each row needs.

### Output / Reporting
- Export audit-ready report (format TBD — likely Excel with separate tabs: Matched / AI-Matched with reasoning / Unmatched / Anomalies)
- Every row must be traceable to *why* it landed in its bucket — which rule fired, or the AI's reasoning string.

## Build Order (test each stage in isolation before stacking the next)
1. File ingestion + normalization (CSV, Excel, PDF via vision-LLM) → canonical schema
2. Stage 1 deterministic matcher — test with synthetic controlled data first
3. Stage 2 AI matching net — embeddings + LLM confirmation
4. Stage 3 anomaly detection — rules first, AI layered after
5. Report export

## Notes for Claude Code
- Keep each stage as an isolated, independently testable module — the previous build's bugs likely came from stages being tangled together.
- Prioritize explainability at every stage: every match/flag needs a human-readable reason attached, not just a boolean.
- Start with synthetic test data for Stage 1 before touching real bank PDFs.
- PDF extraction should be built and validated one target bank at a time, not as a generic parser.
