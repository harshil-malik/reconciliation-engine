# PROGRESS — session state / resume point

Machine-readable-ish notes so a future session can pick up without re-deriving
context. Companion to `workflow.md` (which is the *plan*); this file is the *state*.

**Last updated:** 2026-08-25, during the llama.cpp / Qwen offline migration.

> **Verify before trusting.** The download percentage below was accurate when
> written and is certainly stale now. Always re-check live state (commands in
> "How to check current state") rather than believing this file's numbers.

---

## Project in one paragraph

`~/v-01` is a FastAPI reconciliation engine for chartered accountants: ingests bank
statements + internal ledgers (CSV/Excel/PDF), normalizes to a canonical
`Transaction` schema, matches deterministically (Stage 1), falls back to an AI net
(Stage 2: embeddings shortlist → LLM confirmation), detects anomalies separately
(Stage 3, rules-based), and exports a 4-tab audit-ready `.xlsx`. Design rationale
lives in `reconciliation-engine-spec.md`. Stages are deliberately isolated and
independently testable; Stage 2 (matching) and Stage 3 (anomalies) must never be
merged into one AI pass.

## DONE — verified

- **Stage 0–3 pipeline + report export.** Pre-existing, reviewed, sound.
- **Bug fixed: Stage 2 failures no longer 500 the whole request.**
  `app/ai_matching/matcher.py` wraps the shortlist and per-pair confirm calls; a
  provider failure drops affected rows into the CA "unmatched" bucket instead of
  destroying the report along with Stage 1's good matches. Found by hitting a real
  Gemini 429; verified fixed against the same live error. 2 regression tests.
- **llama.cpp / local Qwen integration — code complete.** No API keys anywhere in
  the default path.
  - `app/local_llm.py` (new) — `LlamaCppClient`, talks to `llama-server`'s
    OpenAI-compatible API. All calls use grammar-constrained decoding
    (`response_format: json_schema`), which is what makes a 3B model reliably emit
    parseable JSON.
  - `LocalPDFExtractor` (`app/ingestion/vision_client.py`) — reads the PDF **text
    layer** via pypdf, because a text-only 3B has no vision. Per-bank templates
    unchanged. Scanned PDFs raise a clear error (out of v1 scope).
  - `LocalEmbeddingClient` (`app/ai_matching/embeddings.py`) — port 8081.
  - `LocalMatchConfirmer` (`app/ai_matching/confirmer.py`) — port 8080.
  - All three are now the defaults in `app/main.py`. Gemini/Claude/Voyage
    implementations remain as documented alternates behind the same `Protocol`s.
- **82 tests passing**, fully offline (model clients tested via
  `httpx.MockTransport`). `tests/test_local_llm.py`, `tests/test_local_pdf_extractor.py`.
- **Docs:** `README.md` (setup, tradeoffs, provider swap table),
  `.env.example` (no key needed now), `workflow.md` (forward plan).
- **llama.cpp installed** — Homebrew build 10566, `/opt/homebrew/bin/llama-server`.

## DONE — model weights downloaded and verified

In `~/v-01/models/` (gitignored via `models/` + `*.gguf`). Both verified byte-exact
against the HuggingFace `content-length`, both with valid `GGUF` magic (`47475546`):

| File | Bytes | Verified |
| --- | --- | --- |
| `qwen2.5-3b-instruct-q4_k_m.gguf` | 2,104,932,768 | ✅ exact |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639,150,592 | ✅ exact |

Re-download command if a file is ever lost or truncated (HuggingFace returns
`206 Partial Content` with `accept-ranges: bytes`, so `-C -` genuinely resumes):

```bash
cd ~/v-01/models
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf
```

**Do not use `llama-server -hf`.** It stalled silently: socket stayed ESTABLISHED,
blob stopped growing, no error, no timeout. Cost ~40 min before detection. The
`curl` path is the known-good route.

## DONE — verified against the real Qwen model

Servers run from local GGUFs; all three integration points exercised live.

- **Embeddings** — 1024-dim. Semantically sane: a true pair scored 0.85 cosine vs
  0.46 for an unrelated pair, so the 0.5 shortlist threshold separates them.
- **Match confirmation** — works, after four rounds of tuning (below).
- **End-to-end `/reconcile`** — 200 OK against live models, Stage 2 firing and
  landing an AI match with reasoning in the report. ~2.5s for a 4-row fixture.

### What the tuning found (all measured, not assumed)

1. **Schema property order is load-bearing.** `is_match` was generated before
   `reasoning`, forcing a verdict then rationalizing — the model returned
   `is_match=False` while reasoning "different date, likely due to a clearing lag".
   Reordering so `reasoning` comes first is poor-man's chain-of-thought and moved
   recall from 2/5 to 5/5 in the first test.
2. **The `is_match` boolean is badly calibrated; `confidence` is not.** The model
   returned `is_match=False` on pairs it simultaneously scored 0.8. The decision now
   thresholds on `confidence` alone (`app/ai_matching/matcher.py`); the boolean is
   still parsed and kept for the audit trail.
3. **The model is sensitive to cosmetic number formatting.** The same pair scored
   0.8 written as `-10050` and 0.3 as `-10050.00`. Amounts are now formatted
   canonically at 2dp, and the amount/date deltas are computed in Python and handed
   over as stated facts — a 3B model should not be doing the arithmetic.
4. **Opposite-direction pairs are now rejected in code, not by the model.** A local
   3B confidently matched +60,000 against −60,000. Amounts are signed on both sides,
   so a sign mismatch is definitionally not the same transaction. Guard added in
   `match_with_ai`, and it also saves a model call.

### Current measured quality — `scripts/eval_confirmer.py`

15 labelled pairs, run against the live model:

- **Zero false positives at every threshold tested.**
- Recall 6/8 at thresholds 0.3–0.5, 5/8 at 0.6–0.8.
- Default `confidence_threshold` set to **0.5** — the conservative end of the
  false-positive-free plateau. Every true negative scored ≤0.2, every caught true
  match 0.5–0.8.

Caveat: 15 synthetic cases is a small sample and they are my own constructions, not
real statements. Re-run the eval against real client data before trusting the
threshold. The script exists so this gets re-measured rather than re-guessed.

### Honest assessment of the 3B model

Usable but marginal. It is stable at `temperature=0` (5/5 identical reruns) and
never produced a false positive in the final configuration, which is the failure
mode that matters. But it missed 2 of 8 true matches, its confidence output is
coarse (effectively 0.0/0.2/0.5/0.8), and it is fragile to prompt phrasing —
an intermediate prompt revision swung it to 4 false positives out of 7 negatives.
Prompt changes here must be re-measured with the eval script, never eyeballed.

If better recall is needed, try a 7B/8B local model before reaching for a hosted API.

## Cleanup owed

The abandoned `-hf` attempt orphaned ~725 MB (hashed blob paths, not resumable by
curl, so it was written off rather than reused):

```bash
rm -rf ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-3B-Instruct-GGUF \
       ~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B-GGUF
```

## How to check current state

```bash
cd ~/v-01
ls -lh models/*.gguf                      # download progress
pgrep -fl "curl.*gguf"                    # downloads still running?
pgrep -fl llama-server                    # servers up?
curl -s localhost:8080/health             # chat server (binds only after load)
curl -s localhost:8081/health             # embedding server
source .venv/bin/activate && python -m pytest -q   # expect 82 passed
git status                                # repo has NO commits yet
```

## Gotchas worth remembering

- `llama-server` does not bind its health endpoint until weights finish loading, so
  "connection refused" during startup is normal, not a failure.
- Weights cache for `-hf` is `~/.cache/huggingface/hub`, **not**
  `~/Library/Caches/llama.cpp`.
- The app runs fine with no servers at all — Stage 1 still reconciles
  deterministically and Stage 2 degrades to the CA queue. Useful for testing
  non-AI paths quickly.
- User preference (saved to memory): prefer local llama.cpp models over hosted
  API-key providers on this project; don't silently switch a default back to a
  hosted API.

## Open concerns to raise, not bury

- **Stage 1 can match two unrelated transactions.** Observed live: a bank row
  "NEFT TO GLOBEX LTD" matched a ledger row "Payment to Initech Pvt Ltd" on
  `exact_amount_date`, because same amount + same date + exactly one candidate
  matches with **no description check at all**. Stage 2 cannot rescue this — Stage 1
  never passes the row on. This follows the spec as written (fuzzy matching is
  specified only as a tiebreak when multiple candidates exist), and in practice a
  lone same-amount same-date pair usually *is* the same transaction with differently
  worded descriptions, so adding a similarity floor would trade these rare false
  positives for many false negatives. Flagged as a deliberate decision to make, not
  a bug to silently fix.

- **Round-number anomaly rule appears to over-fire** — in a synthetic test every
  salary payment tripped it. Over-flagging erodes CA trust, which the spec calls out
  explicitly. Needs threshold calibration.
- **Repo has zero commits.** This migration isn't a reviewable diff. Worth an
  initial commit; `.gitignore` already covers `.env`, `.venv/`, `models/`, `*.gguf`.
- Only 1 of the 5 target bank templates (HDFC) exists.
- Stage 3's AI layer was specced but never built (rules only).
- No auth / rate limiting / upload size cap on the FastAPI app.
