# Reconciliation Engine

AI-powered bank-statement-vs-ledger reconciliation for chartered accountants. Ingests
financial records in multiple formats, normalizes them, matches transactions
deterministically first with AI as a fallback net, flags anomalies, and exports an
audit-ready Excel report where every row is traceable to *why* it landed in its bucket.

Runs **fully offline** by default against a local Qwen model served by llama.cpp — no
API key, and client financial data never leaves the machine doing the reconciliation.

See `reconciliation-engine-spec.md` for the design rationale behind each stage.

## Pipeline

| Stage | What it does | Module |
| --- | --- | --- |
| 0 — Ingestion | CSV / Excel / PDF → canonical `Transaction` schema | `app/ingestion/` |
| 1 — Deterministic matching | Exact amount → date tolerance → fuzzy description tiebreak. No AI, fully explainable. | `app/matching/` |
| 2 — AI matching net | Only Stage 1's leftovers: embeddings shortlist, then LLM confirmation with a reasoning string. | `app/ai_matching/` |
| 3 — Anomaly detection | Duplicates, threshold avoidance, round numbers, reversals, timing gaps — across the *full* reconciled set. | `app/anomaly/` |
| Report | One `.xlsx`, four tabs: Matched / AI Matched / Unmatched / Anomalies. | `app/report/` |

Stages 2 and 3 are deliberately kept separate: a missed match wastes a CA's time, a
missed anomaly hides risk. The CA review queue splits into "couldn't match" and
"flagged as suspicious" so it's obvious which kind of attention each row needs.

## Setup

### 1. Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. llama.cpp

```bash
brew install llama.cpp          # macOS; see llama.cpp's repo for other platforms
```

### 3. Start the two model servers

llama-server hosts one model per process, so the chat model and the embedding model
run on separate ports. Both commands download the GGUF weights on first run (~2.5 GB
combined) and cache them under `~/.cache/huggingface/hub`, so later starts are
instant. The server does not begin serving until its download finishes.

```bash
# Terminal 1 — chat/extraction model (~2 GB), backs PDF extraction + match confirmation
llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M --port 8080 -c 8192

# Terminal 2 — embedding model (~600 MB), backs the Stage 2 shortlist
llama-server -hf Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0 --port 8081 --embeddings
```

`-c 8192` sets the context window — raise it if a long statement gets truncated
during extraction.

#### If the `-hf` download stalls

`-hf` fetches the weights over a single connection that can go dead while still
appearing ESTABLISHED — the log sits at the CORS banner and the blob under
`~/.cache/huggingface/hub` stops growing, with no error and no timeout. Confirm by
checking whether the partial file is still growing:

```bash
find ~/.cache/huggingface -name '*.downloadInProgress' -exec ls -l {} \;
```

If it's stalled, fetch the weights directly with a resumable download instead (the
`models/` directory is gitignored), then point llama-server at the local files:

```bash
mkdir -p models && cd models
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf
```

```bash
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192
llama-server -m models/Qwen3-Embedding-0.6B-Q8_0.gguf --port 8081 --embeddings
```

`curl -C -` resumes where it left off, so a re-run after an interruption picks up
rather than starting over.

### 4. Run the app

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and drag in a bank statement + ledger.

## Desktop app (Windows)

The steps above are the developer setup. For a non-technical user (a CA), the engine
also ships as a **double-clickable Windows app** — no Python, no terminal, no
`llama-server` commands, no localhost URL. The FastAPI backend and both model servers
start and stop automatically inside a native window (`desktop/launcher.py`), and the
whole thing runs **fully offline**: the GGUF weights and a GPU-enabled llama.cpp build
are bundled in.

**GPU acceleration.** The bundled llama.cpp binary is the **Vulkan** build, so it
offloads the Qwen2.5-3B model to any NVIDIA / AMD / Intel GPU (`-ngl 99`). On a machine
with no usable GPU it falls back to CPU automatically — slower, but it still runs. Force
CPU with the `LLAMA_NGL=0` environment variable if a GPU driver misbehaves.

### Run from source (dev)

```powershell
pip install -r requirements.txt
.\build_windows.ps1   # first time only: downloads the Vulkan binary + models
python -m desktop.launcher
```

### Build the distributable

```powershell
.\build_windows.ps1
```

This fetches the assets too large to commit (the Vulkan `llama-server` and the two GGUF
models, ~2.5GB), runs PyInstaller, and produces
`dist\ReconciliationEngine-windows.zip`. Distribute that zip on the website: users unzip
it and run `ReconciliationEngine\ReconciliationEngine.exe`.

> The Windows app uses the Edge **WebView2** runtime, which ships with Windows 11. macOS
> is packaged separately (not from this script — PyInstaller can't cross-compile).

## How the local model is used

Every model call uses llama.cpp's **grammar-constrained decoding** (`response_format:
json_schema`), which compiles the expected JSON schema into a GBNF grammar and
restricts sampling to tokens that keep the output valid. This matters far more for a
3B model than for a frontier API model — small models are the ones that drift into
prose or unclosed braces when merely *asked* for JSON.

**PDF extraction reads the text layer, not pixels.** A text-only 3B model has no
vision capability, so `LocalPDFExtractor` pulls embedded text out of the PDF with
pypdf and prompts the model with it, using the same unchanged per-bank templates. This
fits the v1 scope, which covers text-based PDFs and excludes scanned/image statements;
a scanned page yields no text layer and raises a clear error rather than silently
returning nothing.

**Degradation is graceful.** If a model server is unreachable or a call fails, the
affected rows fall through to the CA's "unmatched" review queue and the report still
builds — Stage 1's deterministic matches are never lost to a Stage 2 failure. You can
run the app with no llama.cpp server at all and still get deterministic reconciliation.

### Accuracy tradeoff

A 3B model is a weaker judge than a frontier model, so its Stage 2 confidence scores
are less reliable. That's contained by design: Stage 1 resolves the majority of rows
deterministically without any model, Stage 2 only ever sees the leftovers, and
anything below the confidence threshold goes to the CA queue rather than being
asserted as a match. Raise `confidence_threshold` in `match_with_ai()` to be stricter.

## Configuration

No `.env` file is needed for the default local setup. To override a default, copy
`.env.example` to `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | Chat/extraction server |
| `LLAMA_EMBEDDING_SERVER_URL` | `http://127.0.0.1:8081` | Embeddings server |
| `LLAMA_TIMEOUT_SECONDS` | `300` | Per-request timeout (3B on CPU is slow) |

### Using a hosted model instead

Each AI step sits behind a `Protocol`, so swapping providers means changing what the
dependency provider in `app/main.py` returns — nothing else in the pipeline changes:

| Step | Local (default) | Hosted alternatives |
| --- | --- | --- |
| PDF extraction | `LocalPDFExtractor` | `GeminiVisionExtractor`, `ClaudeVisionExtractor` |
| Embeddings | `LocalEmbeddingClient` | `GeminiEmbeddingClient`, `VoyageEmbeddingClient` |
| Match confirmation | `LocalMatchConfirmer` | `GeminiMatchConfirmer`, `ClaudeMatchConfirmer` |

The hosted vision extractors are genuinely better at PDFs than the local text-layer
path, so they're worth reaching for if you need scanned-statement support. Set the
matching API key in `.env` (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`).

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

The suite is fully offline — model clients are exercised against an in-memory HTTP
transport, so no llama.cpp server or API key is required to run it.

## Adding a bank template

Per the spec, PDF extraction is built and validated one bank at a time rather than as
a generic parser. To add one: create a class in `app/ingestion/bank_templates/`
implementing `build_prompt()` and `parse_response()` (copy `hdfc.py`), then register
it in `_BANK_TEMPLATES` in `app/main.py`. It appears in the UI dropdown automatically.

## Landing page: "Book a demo"

`landing.html` (kept identical to `index.html` at the repo root, so Vercel's static
detection finds an entrypoint — see git history) is the public marketing page,
deployed on Vercel separately from the desktop app above. Its `#demo` section posts
to `api/leads.js`, a Vercel serverless function — the one part of this project that
isn't local-first, since a public lead-capture form needs a server that's up when
the visitor is, not a per-user offline install.

To wire it up on a fresh deploy:

1. **MongoDB Atlas** (free tier is enough) — leads land in the `munim` database's
   `leads` collection. Set `MONGODB_URI` as a Vercel project env var.
2. **Cal.com** — create a 30-min event type (e.g. named `munim-demo`), with a 15-min
   buffer after, a cap of ~3 bookings/day, and reminders at 24h and 1h. Then edit the
   `CAL_LINK` constant near the bottom of `landing.html` (and `index.html`) to your
   event's link, e.g. `yourname/munim-demo`.
3. **Telegram notify** (optional) — message `@BotFather`, `/newbot`, copy the token;
   message your new bot once; open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `"chat":{"id": ...}`
   value. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as Vercel env vars. Without
   these the lead is still saved to Mongo — you just don't get pinged.

See `.env.example` for the full list of env vars `api/leads.js` reads, and
`package.json` for the two npm dependencies (`mongodb`, `zod`) Vercel installs for it.
