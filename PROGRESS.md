# PROGRESS — where the project stands

State, not plan. `workflow.md` holds the plan; this file says what exists and why.

**Last updated:** 2026-08-27, after closing three gaps from the standing list:
ledger convention detection for CSV/Excel, per-client anomaly thresholds, and an
upload size cap.

> Numbers here go stale. Re-check with the commands under "Check current state"
> rather than believing anything written down.

---

## What this is

`~/v-01` is a FastAPI bank reconciliation engine for chartered accountants. It
ingests a bank statement and a client ledger (CSV/Excel/text-PDF), normalizes both
to one canonical `Transaction` schema, matches them, flags anomalies, and produces
an audit-ready result where every row states why it landed where it did — as a
nine-tab `.xlsx` and as an in-browser dashboard. Design rationale is in
`reconciliation-engine-spec.md`.

Runs fully offline against a local Qwen model via llama.cpp. No API keys, and
nothing — not even a webfont — leaves the machine holding the client's data.

## The pipeline

| Stage | What it does | Model? |
| --- | --- | --- |
| 0 Ingestion | CSV/Excel/PDF → canonical `Transaction` | **No** — PDF tables read geometrically |
| 1 Matching | exact amount → date tolerance → fuzzy tiebreak | No |
| 1.5 Near-matching | fee/rounding-sized gaps + corresponding payee | No |
| 2 AI net | embeddings shortlist → LLM confirmation | Yes |
| 3 Anomalies | duplicates, thresholds, round numbers, reversals, gaps | No |
| Report | nine tabs, organised by what a reviewer must DO | — |

**The model does almost nothing, deliberately.** Stage 1.5 was measured against
Stage 2 and replaced 100% of its output on every dataset tested, including the real
statement. Stage 2 is retained only for semantic linkage string comparison cannot
reach (bank `NEFT PAYMT INV-4471` ↔ ledger `Invoice 4471 settlement Zenith` scores
0.29). Treat it as a rarely-firing backstop, not a working stage.

## The principle that kept proving right

Every time work moved *out* of the model into deterministic code, accuracy went up
and cost went to zero. The model's failures were never fixable by better prompting —
they were fixed by not asking it:

- reading PDF columns by geometric position rather than asking which column a number
  sat in
- auditing every amount against the statement's own running balance
- footing the whole file against its printed totals
- rejecting opposite-direction pairs, unidentifiable rows and implausible amount gaps
  in code, before any model call
- deciding near-matches on a fee-sized gap plus a corresponding payee

**Corollary: measure thresholds, never guess.** Three times a plausible threshold sat
on a knife-edge — 0.45 missed a real pair scoring 0.44; 0.7 confidence lost recall a
0.5 kept. Pick the middle of a plateau and record the measurement in a comment beside
the value.

## Verified working

- **174 tests**, fully offline (model clients use `httpx.MockTransport`).
- **Real HDFC statement + client ledger** reconcile correctly: 15 matched by Stage 1,
  1 by Stage 1.5 (a deliberate ₹500 typo), 0 needed the model, and the 3 remaining
  rows are genuine reconciling items — two un-booked bank charges and a deposit in
  transit. 14 of 16 matches backed by a shared transaction id. All four verifier
  checks pass, and both documents foot to their own printed totals.
- **Generated fixture**: 13 Stage 1 + 1 Stage 1.5, one legitimately unmatched row per
  side, and a duplicate payment flagged despite both legs reconciling perfectly.
- **Local models live** — Qwen2.5-3B-Instruct (chat, :8080) and Qwen3-Embedding-0.6B
  (:8081), in `models/` (gitignored).
- **Browser dashboard** at `/` backed by `/reconcile/preview`, which returns the
  result as JSON with the workbook embedded as base64 — so downloading cannot re-run
  the pipeline or produce a workbook that differs from the screen.
- **Git**: private repo at
  `https://github.com/harshil4857398475435/reconciliation-engine`.
  History was rewritten once with `git filter-repo` to purge identifiers taken from
  real statement PDFs, before the first push; replacements are length-preserving so
  the column-alignment fixtures still test the same character positions.
  **Never commit anything from `private/`** — it holds real client files.

## Tools built for this project

| Script | Answers |
| --- | --- |
| `scripts/verify_reconciliation.py` | Is this reconciliation arithmetically sound? Four independent checks; exit code gates. |
| `scripts/eval_confirmer.py` | Is the configured model good enough to judge matches? Run after ANY prompt/model/threshold change. |
| `scripts/make_sample_data.py` | Regenerates the coherent test fixture in `sample_data/`. |

Project skills in `.claude/skills/` encode the recurring workflows:
`check-reconciliation`, `add-bank-template`, `test-real-statement`.

## Safeguards, and the bug each one exists for

Every one was added because the failure actually happened. Do not remove one without
understanding which silent failure it prevents.

- **Per-row balance audit** (`pdf_parser._reconcile_against_balances`) — consecutive
  balances differ by exactly the transaction amount. Caught a deposit misread as a
  withdrawal, and recovers amounts a statement never printed. Sign-convention
  agnostic. Refuses to publish when the two readings disagree irreconcilably.
- **Whole-file foot check** (`_foot_against_printed_totals`) — the per-row audit
  compares *consecutive* rows, so a row missed entirely leaves its neighbours looking
  consistent. Footing to the printed opening/closing catches the dropped row, which
  is the failure most likely to go unnoticed.
- **Identical-upload rejection** — the same file in both boxes matches every row with
  its own twin and empties Unmatched: the most reassuring possible output, and
  nonsense. Now a 422.
- **Ledger convention auto-detection** — whether a ledger's Debit means money in
  depends on which account it covers, and getting it wrong inverts every amount
  silently. Both readings are tried; the one with more *corroborated* matches wins.
  Scoring by raw match count picks the wrong one — verified. Runs for CSV and Excel
  as well as PDF: the ambiguity belongs to double-entry bookkeeping, not to the file
  format, and the tabular path used to assume "Credit means money in" unconditionally
  — backwards for the client's own Bank A/c ledger, which is the usual counterpart to
  a statement. A sheet with one signed Amount column reads the same either way and is
  reported as unambiguous rather than as a tie.
- **Upload size cap** (`MAX_UPLOAD_MB`, default 25) — an upload is buffered whole,
  both sides at once, so a mis-dropped video or disk image would be read into memory
  before anything inspected it. Checked chunk by chunk while reading rather than from
  the client's Content-Length, which is the very thing being limited.
- **Corroboration labels** — every match records whether it rests on a reference, a
  corresponding description, or nothing but amount and date. Labelled, not blocked:
  measured, no similarity threshold separates the wrong ones from the right ones (an
  unrelated pair scored 0.50 while genuine ones scored 0.36).
- **Stage 2 guards** — opposite direction, unidentifiable rows, and amount gaps too
  large to be a fee are rejected before the model is asked. Added after the model
  matched -12,500 to -8,340 at 0.8 confidence while claiming the amounts were "the
  same".
- **Distinctive-token comparison** — corroboration ignores banking boilerplate and
  business-type suffixes, because "NEFT TO GLOBEX LTD" and "Payment to Initech Pvt
  Ltd" score 0.50 on raw characters, higher than genuine pairs.
- **Mirrored-duplicate collapsing** — an invoice paid twice is found on both sides
  and was reported twice. Merged into one finding where every leg reconciled; a
  duplicate on only ONE side stays separate, because that is the worse problem.

## What the real statement taught us

First contact found four layout bugs, three of which silently degraded matching
rather than failing:

- an `OPENING BALANCE` line is dated and sits in the table like a transaction while
  moving no money — it became a zero-amount row; its balance is now the opening
  balance, which is what lets row 1 be audited
- `0000000000` is how the bank prints "no reference", on 11 of 19 rows. Kept as a
  value it reads as an exact reference match between unrelated transactions
- the `Chq./Ref.No.` heading is 12 characters but the UTRs beneath it are 16, so
  every reference was truncated
- the ledger carries an internal `Voucher No.` column *before* `Particulars`, which
  made the description slice run backwards and emptied every narration in the file

**HDFC buries the real transaction id in the narration** while printing a
placeholder in the reference column — and for NEFT the two forms differ outright.
Parsing narrations (`hdfc_narration.py`, yielding `kind`/`reference`/`counterparty`)
lifted reference-backed matches from 5 to 14 of 16.

**A measured negative:** wiring the extracted counterparty into similarity scoring
made matching *worse* — higher on 5 pairs, lower on 8, and it changed the
corroboration verdict on none. `SequenceMatcher` penalises the length mismatch
between a bare payee and a full narration. Extracted and surfaced, not used.

## Honest assessment of the 3B model

Reproducible, but only once pinned. `temperature=0` alone was *not* enough: across
five eval runs one pair scored 0.30 on the first and 0.80 on the next four, which
straddles the 0.5 confidence threshold — the same two rows would be AI-matched or not
depending on the run. Greedy sampling fixes which token wins a comparison, not the
logits being compared, and llama-server reuses KV-cache prefixes between requests by
default. With `seed` pinned and `cache_prompt: false` (`app/local_llm.py`) five runs
are byte-identical. Never a false positive in any run — the failure mode that
matters.

But recall was 6/8 on the eval, confidence output is coarse (effectively
0.0/0.2/0.5/0.8), and it is fragile to phrasing: one intermediate prompt revision
swung it to 4 false positives out of 7 negatives. **Never change the confirmer
prompt without re-running the eval script.**

If more is needed, try a 7B/8B local GGUF (a one-line model path change) before
reaching for a hosted API. Fine-tuning is not warranted — the model decides roughly
one row in sixteen, and there is no labelled training data.

## Known limits

- **Only single-page statements tested.** Page furniture is handled and unit-tested,
  but no genuinely multi-page real statement has been run.
- **The value-date column is ignored.** A cheque issued in June and cleared in July
  would sit in the June ledger and the July statement; matching on transaction date
  alone would miss it. Needs a month-boundary statement to exercise.
- **Only HDFC** of the five target banks has a template.
- **Scanned/image PDFs unsupported** — raise a clear error rather than guessing.
- **Stage 3's AI layer** was specced but never built (rules only).
- **No auth or rate limiting.** Fine locally; not for anyone else. (An upload size
  cap now exists.)
- **Anomaly thresholds are per request, not per client.** `/reconcile` and
  `/reconcile/preview` accept an `anomaly_config` JSON field and `GET /anomaly-config`
  returns the defaults to edit, but nothing stores a config against a client, so the
  same engagement's settings must be sent each run.

## Check current state

```bash
cd ~/v-01
pgrep -fl llama-server                     # both model servers up?
curl -s localhost:8080/health              # chat  (binds only after weights load)
curl -s localhost:8081/health              # embeddings
source .venv/bin/activate && python -m pytest -q          # expect 174 passed
python scripts/verify_reconciliation.py sample_data/bank_statement.pdf \
                                        sample_data/internal_ledger.pdf
git log --oneline | head -5
```

## Start everything

```bash
cd ~/v-01
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192 &
llama-server -m models/Qwen3-Embedding-0.6B-Q8_0.gguf --port 8081 --embeddings &
source .venv/bin/activate && uvicorn app.main:app --port 8000
# then http://localhost:8000
```

## Gotchas

- `llama-server` doesn't bind its health endpoint until weights finish loading, so
  "connection refused" during startup is normal.
- **Never fetch weights with `llama-server -hf`** — it stalled silently (socket
  ESTABLISHED, blob frozen, no error, no timeout) and cost ~40 minutes before
  detection. Use the `curl -C -` commands in README.md.
- The app runs fine with **no model servers at all**: Stages 0, 1, 1.5 and 3 are
  deterministic, and Stage 2 degrades to the review queue. Good for fast iteration.
- uvicorn leaves the root logger at WARNING; `app/main.py` wires the app loggers
  explicitly so Stage 0 diagnostics reach the terminal. Watch for the `Stage 0:` line
  — it names the file each side was parsed from, and catches the same file uploaded
  twice.
- User preference (in memory): prefer local llama.cpp models over hosted API-key
  providers here; don't silently switch a default back to a hosted API.
