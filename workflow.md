# Workflow — from here onwards

Working plan for finishing the llama.cpp / Qwen offline migration and hardening the
reconciliation engine. Written 2026-08-25.

**Naming note:** you asked for a file named `workfow`; assumed that was a typo and
used `workflow.md` so it renders as Markdown. Rename if you actually meant the former.

---

## Where things stand

| Item | Status |
| --- | --- |
| Stage 0–3 pipeline + report export | Done, 82 tests passing |
| Stage 2 graceful degradation (API failure no longer 500s the request) | Done, verified live |
| llama.cpp integration (local Qwen as default, no API keys) | Code complete, tested against mocked transport |
| llama.cpp installed (Homebrew build 10566) | Done |
| GGUF weights downloaded | Done, both verified byte-exact |
| Live verification against the real Qwen model | Done — embeddings, confirmer, and end-to-end all exercised live |
| Confirmer quality measured (`scripts/eval_confirmer.py`) | Done — 6/8 recall, **zero false positives** |

The migration is functionally complete and running offline with no API key. See
`PROGRESS.md` for what the live tuning found and the honest assessment of the 3B
model — it is usable but marginal, and prompt changes must be re-measured with the
eval script rather than eyeballed.

---

## Step 1 — Weights download ✅ DONE

Both GGUFs are in `models/` (gitignored) and verified byte-exact against the
HuggingFace `content-length`, with valid `GGUF` magic:

- `qwen2.5-3b-instruct-q4_k_m.gguf` — 2,104,932,768 bytes
- `Qwen3-Embedding-0.6B-Q8_0.gguf` — 639,150,592 bytes

If a file is ever lost, re-run the `curl -C -` commands from README.md — resume works
(HF returns `206` with `accept-ranges: bytes`). Do **not** go back to
`llama-server -hf`: that path stalled silently (dead socket, still ESTABLISHED, no
error, no timeout).

**Cleanup owed:** the abandoned `-hf` attempt left ~725 MB orphaned.

```bash
rm -rf ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-3B-Instruct-GGUF \
       ~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B-GGUF
```

## Step 2 — Start both servers

```bash
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192
llama-server -m models/Qwen3-Embedding-0.6B-Q8_0.gguf --port 8081 --embeddings
```

Confirm both are actually serving before moving on — the health endpoint does not
bind until weights finish loading:

```bash
curl -s localhost:8080/health && curl -s localhost:8081/health
```

## Step 3 — Verify each model call in isolation

Test the three integration points separately, so a failure localizes cleanly. This
is the whole point of the staged design — don't debug them as one blob.

1. **Embeddings** — `POST localhost:8081/v1/embeddings` returns a vector of the
   expected dimension, and `LocalEmbeddingClient.embed()` returns one vector per input
   in request order.
2. **Match confirmation** — hand `LocalMatchConfirmer` a pair that should match and a
   pair that clearly should not. Confirm the grammar constraint holds (valid JSON
   every time) and that the two cases actually differ in `is_match`.
3. **PDF extraction** — run `LocalPDFExtractor` against a real text-based bank
   statement PDF and check the extracted rows against the printed page.

## Step 4 — End-to-end `/reconcile` against the real model

```bash
uvicorn app.main:app --reload   # then use the UI at localhost:8000
```

Use a fixture where Stage 1 *cannot* resolve everything (differing amounts or a wide
date gap), so Stage 2 genuinely fires. Check the report's **AI Matched** tab: the
reasoning strings must be specific and correct, not plausible-sounding filler.

## Steps 2–4 — ✅ DONE

Servers start from local GGUFs, all three integration points verified live, and
`/reconcile` returns a report with Stage 2 firing. Details and the four tuning
findings are in `PROGRESS.md`.

## Step 5 — Judge whether the 3B model is good enough — PARTLY DONE

First pass done: `scripts/eval_confirmer.py` measures 15 labelled pairs against the
live model — **6/8 recall, zero false positives** at the tuned default threshold
of 0.5. Run it after any prompt, model, or threshold change.

**Still owed:** those 15 cases are synthetic constructions, not real statements. To
actually close this question, assemble ~20–30 known-answer pairs from real client
data and re-measure:

- **False positives** — pairs the model matched that aren't the same transaction.
  These are the dangerous ones: a wrong match silently hides a real discrepancy.
- **False negatives** — real matches it missed. Merely wasteful; they land in the CA
  queue for manual review, which is the pre-existing status quo.

Tune `confidence_threshold` in `match_with_ai()` accordingly. Bias toward strictness:
Stage 1 already handles the bulk deterministically, so Stage 2 being conservative
costs little, while a confident wrong match costs trust.

If false positives stay high even at a high threshold, the honest conclusion is that
3B is undersized for the judgment task — escalate to a 7B/8B local model before
reaching for a hosted API, since offline operation is the point.

---

## Known gaps — decide, don't drift

- **Scanned PDFs are unsupported.** A text-only local model cannot read them; the
  extractor raises a clear error. Needs either a hosted vision model or a local
  OCR/vision step. Currently out of v1 scope, which is a defensible call.
- **Only one bank template exists (HDFC).** Spec targets the top 5 Indian banks. Add
  them one at a time, validating each against real statements — not generically.
- **Stage 3 anomaly AI layer was never built.** Rules-only today. The spec calls for
  AI on fuzzier judgment calls, layered *on top of* rules and kept strictly separate
  from Stage 2 matching.
- **No auth, no rate limiting, no upload size cap** on the FastAPI app. Fine for
  local single-user use; must be addressed before this is exposed to anyone else.
- **Anomaly thresholds are unvalidated defaults.** `AnomalyConfig` values were chosen
  as reasonable starting points, not calibrated against real engagements. The
  round-number rule in particular looks prone to over-firing — every salary payment
  trips it — which erodes CA trust exactly as the spec warns.
- **`AnomalyConfig` is not wired to the API.** It exists but `/reconcile` always uses
  defaults; there's no way for a CA to adjust thresholds per client.

## Not yet under version control

The repo has no commits. Worth an initial commit before further changes, so the
llama.cpp migration is a reviewable diff rather than an undifferentiated starting
state. `.gitignore` already excludes `.env`, `.venv/`, `models/`, and `*.gguf` —
verify with `git status` that no weights or secrets are staged before committing.
