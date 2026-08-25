# PROGRESS — where the project stands

State, not plan. `workflow.md` holds the plan; this file says what exists and why.

**Last updated:** 2026-08-26, after moving the pipeline off API keys onto a local
model and then, largely, off the model altogether.

> Numbers here go stale. Re-check with the commands under "Check current state"
> rather than believing anything written down.

---

## What this is

`~/v-01` is a FastAPI bank reconciliation engine for chartered accountants. It
ingests a bank statement and a client ledger (CSV/Excel/text-PDF), normalizes both
to one canonical `Transaction` schema, matches them, flags anomalies, and exports a
4-tab audit-ready `.xlsx` where every row states why it landed where it did. Design
rationale is in `reconciliation-engine-spec.md`.

Runs fully offline against a local Qwen model via llama.cpp — no API keys, and
client financial data never leaves the machine.

## The pipeline as it now stands

| Stage | What it does | Model? |
| --- | --- | --- |
| 0 Ingestion | CSV/Excel/PDF → canonical `Transaction` | **No** — PDF tables are read geometrically |
| 1 Matching | exact amount → date tolerance → fuzzy tiebreak | No |
| 1.5 Near-matching | fee/rounding-sized gaps + corresponding payee | No |
| 2 AI net | embeddings shortlist → LLM confirmation | Yes |
| 3 Anomalies | duplicates, thresholds, round numbers, reversals, gaps | No |
| Report | Matched / AI Matched / Unmatched / Anomalies | — |

**The model does almost nothing now, deliberately.** Stage 1.5 was measured against
Stage 2 and replaced 100% of its output on every dataset tested. Stage 2 is retained
only for semantic linkage that string comparison cannot reach (e.g. bank
`NEFT PAYMT INV-4471` ↔ ledger `Invoice 4471 settlement Zenith`, which scores 0.29
on similarity). Treat it as a rarely-firing backstop, not a working stage.

## The principle that kept proving right

Every time work moved *out* of the model and into deterministic code, accuracy went
up and cost went to zero. The model's failures were never fixable by better
prompting — they were fixed by not asking it. Specifically:

- reading PDF columns by their geometric position instead of asking the model which
  column a number was in
- auditing every amount against the statement's own running balance
- rejecting opposite-direction pairs, unidentifiable rows, and implausible amount
  gaps in code before any model call
- deciding near-matches on a fee-sized gap plus a corresponding payee

**Corollary:** thresholds must be measured, never guessed. Three times a plausible
threshold sat on a knife-edge — 0.45 missed a real pair scoring 0.44; 0.7 confidence
lost recall a 0.5 kept. Pick the middle of a plateau and record the measurement in a
comment.

## Verified working

- **114 tests**, fully offline (model clients use `httpx.MockTransport`).
- **End-to-end on the generated fixture**: 11 Stage 1 + 1 Stage 1.5 + 2 legitimately
  unmatched + 3 anomalies. `scripts/verify_reconciliation.py` passes all four checks.
- **Local models live** — Qwen2.5-3B-Instruct (chat, :8080) and Qwen3-Embedding-0.6B
  (:8081), both in `models/` (gitignored).
- **Git**: real history, currently through `e7dac35`.

## Tools built for this project

| Script | Answers |
| --- | --- |
| `scripts/verify_reconciliation.py` | Is this reconciliation arithmetically sound? Four independent checks; exit code gates. |
| `scripts/eval_confirmer.py` | Is the configured model good enough to judge matches? Run after ANY prompt/model/threshold change. |
| `scripts/make_sample_data.py` | Regenerates the coherent test fixture in `sample_data/`. |

## Safeguards, and the bug each one exists for

Each was added because the failure actually happened — do not remove one without
understanding which silent failure it prevents.

- **Balance audit** (`pdf_parser._reconcile_against_balances`) — consecutive
  balances differ by exactly the transaction amount. Caught a deposit misread as a
  withdrawal, and recovers amounts the statement never printed. Sign-convention
  agnostic. Refuses to publish when the two readings disagree irreconcilably.
- **Identical-upload rejection** — the same file in both boxes matches every row
  with its own twin and empties Unmatched: the most reassuring possible output, and
  nonsense. Now a 422.
- **Ledger convention auto-detection** — whether a ledger's Debit means money in
  depends on which account it covers, and getting it wrong inverts every amount
  silently. Both readings are tried; the one with more *corroborated* matches wins.
  Scoring by raw match count picks the wrong one — verified.
- **Corroboration labels** — every match records whether it rests on a reference, a
  corresponding description, or nothing but amount and date. The last kind is where
  two unrelated same-size same-day payments would be paired. Labelled, not blocked:
  measured, no similarity threshold separates the wrong ones from the right ones.
- **Stage 2 guards** — opposite direction, unidentifiable rows, and amount gaps too
  large to be a fee are rejected before the model is asked. Added after the model
  matched -12,500 to -8,340 at 0.8 confidence claiming the amounts were "the same".
- **Distinctive-token comparison** — corroboration ignores banking boilerplate and
  business-type suffixes, because "NEFT TO GLOBEX LTD" and "Payment to Initech Pvt
  Ltd" score 0.50 on raw characters, higher than genuine pairs.

## Honest assessment of the 3B model

Stable at `temperature=0` (5/5 identical reruns) and, in the final configuration,
never a false positive — the failure mode that matters. But recall was 6/8 on the
eval, confidence output is coarse (effectively 0.0/0.2/0.5/0.8), and it is fragile
to phrasing: one intermediate prompt revision swung it to 4 false positives out of 7
negatives. **Never change the confirmer prompt without re-running the eval script.**

If more is needed, try a 7B/8B local GGUF (a one-line model path change) before
reaching for a hosted API. Fine-tuning is not warranted — the model decides roughly
one row in twelve, and there is no labelled training data.

## Known limits

- **Never tested on a real bank statement.** Everything is synthetic plus two sample
  PDFs. This is the biggest open risk and tomorrow's job.
- **Only HDFC** of the five target banks has a template.
- **Scanned/image PDFs unsupported** — raise a clear error rather than guessing.
  Would need OCR or a hosted vision model.
- **Stage 3's AI layer** was specced but never built (rules only).
- **`AnomalyConfig` isn't wired to the API** — thresholds can't be set per client.
- **No auth, rate limiting, or upload size cap.** Fine locally; not for anyone else.
- CSV/Excel ledgers don't get convention auto-detection (PDF only).

## Check current state

```bash
cd ~/v-01
pgrep -fl llama-server                     # both model servers up?
curl -s localhost:8080/health              # chat  (binds only after weights load)
curl -s localhost:8081/health              # embeddings
source .venv/bin/activate && python -m pytest -q          # expect 114 passed
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
- **Never use `llama-server -hf`** to fetch weights — it stalled silently (socket
  ESTABLISHED, blob frozen, no error, no timeout). Use the `curl -C -` commands in
  README.md.
- The app runs fine with **no model servers at all**: Stages 0, 1, 1.5 and 3 are
  deterministic, and Stage 2 degrades to the review queue. Good for fast iteration.
- uvicorn leaves the root logger at WARNING; `app/main.py` wires up the app loggers
  explicitly so Stage 0 diagnostics reach the terminal. Watch for the `Stage 0:` line
  — it names the file each side was parsed from.
- User preference (in memory): prefer local llama.cpp models over hosted API-key
  providers here; don't silently switch a default back to a hosted API.
