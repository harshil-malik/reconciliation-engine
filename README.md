# Reconciliation Engine

Bank-statement-vs-ledger reconciliation for chartered accountants. Ingests financial
records in several formats, normalizes them, matches transactions deterministically,
flags anomalies, and produces an audit-ready result where every row is traceable to
*why* it landed where it did.

Runs **fully offline** against a local Qwen model served by llama.cpp. No API key, no
telemetry, and nothing — not even a webfont — leaves the machine holding the client's
financial data.

See `reconciliation-engine-spec.md` for the design rationale behind each stage.

## Pipeline

| Stage | What it does | Model? | Module |
| --- | --- | --- | --- |
| 0 — Ingestion | CSV / Excel / PDF → canonical `Transaction` | No | `app/ingestion/` |
| 1 — Matching | Exact amount → date tolerance → fuzzy tiebreak | No | `app/matching/` |
| 1.5 — Near-matching | Fee/rounding-sized gaps with a corresponding payee | No | `app/matching/near_matcher.py` |
| 2 — AI net | Embeddings shortlist → LLM confirmation, on Stage 1.5's leftovers | Yes | `app/ai_matching/` |
| 3 — Anomalies | Duplicates, threshold avoidance, round numbers, reversals, timing gaps — across the *full* reconciled set | No | `app/anomaly/` |
| Report | Nine-tab `.xlsx` plus a browser dashboard | — | `app/report/` |

Stages 2 and 3 are deliberately separate: a missed match wastes an accountant's time,
a missed anomaly hides risk. The review queue splits into "couldn't match" and
"flagged as suspicious" so it is obvious which kind of attention each row needs.

**The model does very little.** Stage 1.5 was measured against Stage 2 and replaced
100% of its output on every dataset tested, including a real statement. Stage 2
remains only for semantic linkage string comparison cannot reach. Treat it as a
rarely-firing backstop.

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

### 3. Download the model weights

~2.5 GB combined, into a gitignored `models/` directory.

```bash
mkdir -p models && cd models
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
curl -L -C - --retry 10 --retry-all-errors \
  -O https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf
cd ..
```

`-C -` resumes, so an interrupted download picks up rather than restarting.

> **Do not use `llama-server -hf` to fetch the weights.** It downloads over a single
> connection that can go dead while still appearing ESTABLISHED: the log sits at the
> CORS banner, the blob under `~/.cache/huggingface/hub` stops growing, and there is
> no error and no timeout. It cost about 40 minutes here before anyone noticed.

### 4. Start the two model servers

llama-server hosts one model per process, so the chat and embedding models run on
separate ports.

```bash
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 8192 &
llama-server -m models/Qwen3-Embedding-0.6B-Q8_0.gguf --port 8081 --embeddings &
```

`-c 8192` sets the context window — raise it if a long statement gets truncated.
Neither server binds its health endpoint until its weights finish loading, so
"connection refused" during startup is normal.

### 5. Run the app

```bash
source .venv/bin/activate && uvicorn app.main:app --port 8000
```

Open http://localhost:8000, drop in a bank statement and a ledger, and the results
appear in the browser with the workbook available to download.

Generate a test pair first if you have nothing to hand:

```bash
python scripts/make_sample_data.py     # writes sample_data/
```

## Reading the result

Nine tabs, organised by what a reviewer must *do* rather than by which stage produced
a row:

| Tab | Purpose |
| --- | --- |
| **Summary** | The balancing proof. `UNEXPLAINED` must be `0.00` — anything else means a row was mismatched, double-counted or dropped. |
| Matched | Every pairing, with its date gap, amount gap and corroboration |
| Review - Amount | The two sides disagree about the money |
| Review - Date | Booked on different dates |
| Review - Weak Evidence | Nothing but the figures ties the pair together |
| Unmatched - Bank | On the statement, not in the books — each row explained |
| Unmatched - Ledger | In the books, not on the statement — the opposite meaning |
| AI Matched | Model-asserted pairs, kept separate as the least certain |
| Anomalies | Risk flags, which may also appear above: Stage 3 sees everything |

### Anomaly thresholds

Stage 3's thresholds are per engagement, not fixed policy — a client whose approval
limit is ₹25,00,000 gets nothing useful from flags calibrated to ₹50,000. `GET
/anomaly-config` returns the defaults; edit the fields that matter and post the
result back as an `anomaly_config` JSON form field alongside the files:

```bash
curl -F bank_file=@bank.pdf -F ledger_file=@ledger.pdf \
     -F bank_pdf_template=hdfc \
     -F 'anomaly_config={"approval_thresholds":["2500000"],"timing_gap_days":30}' \
     http://localhost:8000/reconcile -o report.xlsx
```

Unrecognised or malformed settings are a 422 rather than a quiet fall back to the
defaults: a typo'd field name that silently reverted would show a reviewer the flags
they had explicitly asked not to see, with nothing to indicate why.

Every match carries a **corroboration** label — `reference` (a shared cheque/UTR),
`description` (payees correspond), or `amount_and_date_only`. The last is where two
unrelated payments of the same size on the same day would be paired, so those are the
rows worth a second look. They are labelled rather than withheld: measured on real
data, no similarity threshold separates the wrong ones from the right ones.

## Source grounding

Every row the report shows — matched, unmatched or flagged — carries a **source
citation**: which file it came from, where in that file, and the source line
verbatim. In the browser each row opens onto it ("Show source"); in the workbook it
is two columns per side (`bank_source`, `bank_source_text`, and the ledger
equivalents). Alongside it sits the rule that fired and the corroboration label, so
a reviewer sees *why* the row landed where it did and can check the claim against
the document they uploaded.

| Source | Citation | Example |
| --- | --- | --- |
| PDF (text layer) | page and line, a range when a narration wraps | `page 2, lines 37-38` |
| CSV / Excel | the row number the spreadsheet itself shows (header is row 1) | `row 47` |
| Read by the model | *no line* — stated plainly | `read by the model — no source line` |

That last row matters. When the deterministic parser cannot read a layout and the
model is asked instead, there is no line to point at, and the record says so rather
than naming a plausible one. An invented citation is the exact failure this feature
exists to prevent.

For a PDF the citation goes further than a line number: each row carries a box on
the page, and each cell within it its own box, so the dashboard shows the row
highlighted on the document as uploaded, with the figure that triggered the flag
outlined inside it. Boxes are stored as fractions of the page rather than points, so
they stay correct at whatever size the page is rendered.

Only pages a citation actually points at are rendered, once per file and shared by
every row citing them, capped at 12 pages and encoded greyscale — a statement page
costs about 190 KB rather than 437 KB, with nothing lost on black type. Both
libraries are optional: without them a citation keeps its file, page and line and
loses only the picture.

A CSV or Excel row has no page to draw on, so it is shown as the row it is: the
columns as the file names them, the values as printed, and the cell that triggered
the flag marked. Each column carries the role ingestion assigned it, so the mapping
the reviewer is being shown is the one matching actually used.

One thing a citation deliberately does not hide: where the per-row balance audit
corrected a misread figure, the cited line still shows what the statement *printed*,
while the amount used is the corrected one. Both are on screen together — that
disagreement is evidence a reviewer should see, not something to paper over.

## How correctness is checked

The engine does not ask to be trusted. `scripts/verify_reconciliation.py` proves a
reconciliation using arithmetic the source documents must satisfy:

```bash
python scripts/verify_reconciliation.py <bank.pdf> <ledger.pdf>
```

1. **Self-consistency** — the printed figures agree with the file's own running
   balance. A failure here is the *document's* problem, not the engine's.
2. **Completeness** — every transaction appears exactly once. Nothing invented,
   nothing dropped.
3. **The reconciliation identity** — every rupee of difference is attributable to a
   named item. This is the check an accountant applies to a BRS.
4. **Matched-pair sanity.**

### Which way round a ledger reads

Whether a ledger's Debit means money *in* depends on which account it covers, and the
file rarely says. Reading it backwards inverts every amount and fails silently —
nothing errors, the rows just stop agreeing. So the ledger is parsed under both
conventions and whichever produces more *corroborated* matches against the bank
wins. This runs for CSV and Excel exports as much as for PDFs; the ambiguity is a
property of double-entry bookkeeping, not of the file format. A sheet carrying one
signed Amount column reads the same either way and is reported as unambiguous.

Pick a ledger format explicitly to override the detection — worth doing for a ledger
too small, or overlapping the statement period too little, for the evidence to
separate the two readings. The log says which way it went, and says so loudly when
the call was a tie.

Ingestion has two independent guards of its own. Each amount is checked against its
own running-balance movement, which catches an OCR slip or a misread column; and the
whole file is footed against its printed totals, which catches a *dropped row* — the
per-row check compares consecutive rows, so a missing one leaves its neighbours
looking perfectly consistent. Where the readings disagree irreconcilably, extraction
refuses rather than publishing figures nothing downstream could tell were fiction.

## How the local model is used

Every model call uses llama.cpp's **grammar-constrained decoding**
(`response_format: json_schema`), which compiles the expected schema into a GBNF
grammar and restricts sampling to tokens that keep the output valid. This matters far
more for a 3B model than a frontier one — small models are the ones that drift into
prose or unclosed braces when merely *asked* for JSON.

**PDF tables are read geometrically, not by the model.** In layout-extracted text the
money columns are right-aligned under their headings, so which column a number
belongs to is a hard geometric fact. `layout_table.py` reads that in code — exactly,
instantly and for free. The model is a fallback for layouts this cannot parse, and on
real statements it has not been needed.

**Degradation is graceful.** If a model server is unreachable, affected rows fall
through to the review queue and the report still builds. The app runs with no
llama.cpp server at all — Stages 0, 1, 1.5 and 3 are entirely deterministic.

### Accuracy tradeoff

A 3B model is a weaker judge than a frontier one. That is contained by design: it
sees only what Stages 1 and 1.5 could not resolve, guards reject implausible pairs
before it is asked, and anything below the confidence threshold goes to the review
queue rather than being asserted. `scripts/eval_confirmer.py` measures it against a
labelled set — **run it after any change to the confirmer prompt, model or
threshold.** A plausible-looking prompt edit once swung it from 0 to 4 false
positives.

## Configuration

No `.env` is needed for the default local setup. To override, copy `.env.example`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | Chat/extraction server |
| `LLAMA_EMBEDDING_SERVER_URL` | `http://127.0.0.1:8081` | Embeddings server |
| `LLAMA_TIMEOUT_SECONDS` | `300` | Per-request timeout (3B on CPU is slow) |
| `MAX_UPLOAD_MB` | `25` | Largest accepted upload. Statements are far smaller; raise it only for an unusually long multi-year export. |

### Using a hosted model instead

Each model step sits behind a `Protocol`, so swapping providers means changing what
the dependency provider in `app/main.py` returns — nothing else changes:

| Step | Local (default) | Hosted alternatives |
| --- | --- | --- |
| PDF extraction | `LocalPDFExtractor` | `GeminiVisionExtractor`, `ClaudeVisionExtractor` |
| Embeddings | `LocalEmbeddingClient` | `GeminiEmbeddingClient`, `VoyageEmbeddingClient` |
| Match confirmation | `LocalMatchConfirmer` | `GeminiMatchConfirmer`, `ClaudeMatchConfirmer` |

A hosted *vision* extractor is the one genuine capability gain — it would allow
scanned statements, which the local text-layer path cannot read. Set the matching API
key in `.env`.

## Tests

```bash
source .venv/bin/activate && python -m pytest
```

189 tests, fully offline — model clients run against an in-memory HTTP transport, so
no llama.cpp server or API key is needed.

Each test's docstring names the bug it exists for. Several encode failures found on
real statements; read the docstring before changing a test.

## Adding a bank template

Per the spec, PDF extraction is built and validated one bank at a time rather than as
a generic parser. Copy `app/ingestion/bank_templates/hdfc.py`; a template declares:

- `template_name`
- `debit_is_inflow` — **False** for a bank statement (a Deposit is money in). Getting
  this backwards inverts every amount and fails silently.
- `build_prompt()` — used only when the deterministic parser cannot read the table
- `extra_references()` / `counterparty()` — optional, for banks that bury the real
  transaction id in the narration. HDFC does: parsing them lifted reference-backed
  matches from 5 to 14 of 16.

Register it in `_BANK_TEMPLATES` in `app/main.py` and it appears in the UI.

Fix in this order of preference: the deterministic parser
(`app/ingestion/layout_table.py`) → the template → the model, last resort.

## Handling real client statements

Put them in `private/`, which is gitignored. They are client financial data even when
redacted.

If a test fixture is derived from a real file, replace the account holder, account
number, customer id, IFSC, transaction ids and counterparty names with invented
equivalents **of the same character length** — the column-alignment tests depend on
exact positions. If real identifiers ever reach a commit they must be purged from
history with `git filter-repo --replace-text` before any push; scrubbing the working
tree alone leaves them in history.
