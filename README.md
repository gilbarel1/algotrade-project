# algotrade-project

An **n8n-based multi-agent system that produces an automated investment recommendation
report for a TA-35 watchlist**. Four specialist agents — **Sentiment, Earnings, Technical,
and Risk Manager** — independently analyze each ticker; a coordinating workflow synthesizes
their conclusions, runs a deliberate three-stage critique pass, and renders a justified
**long / short / hold / avoid** recommendation as a PDF report saved to disk.

Two things distinguish it from a baseline LLM pipeline:
1. **Dual sentiment** — every headline is scored twice (an LLM *and* a fine-tuned
   transformer: FinBERT for English, HeBERT for Hebrew); their disagreement is surfaced,
   not hidden.
2. **Self-critiquing Risk Manager** — a draft → devil's-advocate critique → final loop that
   stress-tests its own reasoning, all three passes visible in the report.

> The complete technical design lives in **[`docs/design.md`](docs/design.md)** — the single
> source of truth for architecture, contracts, schemas, and parameters. The build proceeds
> **one step at a time** per **[`CLAUDE.md`](CLAUDE.md)**.

---

## Architecture at a glance

Two layers joined by one HTTP boundary (§2 of the design):

```
                  ┌─────────────────────────────────────────────┐
   Trigger ─────► │  n8n orchestration  (workflows + OpenRouter) │
 (manual /        │  fan-out per ticker → 3 agents → Risk Manager│
  scheduled)      └───────────────┬─────────────────────────────┘
                                  │ HTTP (JSON only — small payloads)
                                  ▼
                  ┌─────────────────────────────────────────────┐
                  │  quant_service  (Python + FastAPI)           │
                  │  /ohlc  /indicators  /sentiment  /report     │
                  │  pandas-ta · FinBERT/HeBERT · WeasyPrint     │
                  └───────────────┬─────────────────────────────┘
                                  ▼
                          DuckDB (store.duckdb)
              prices · news · earnings · runs · recommendations · costs · evals
```

- **n8n** orchestrates and makes all LLM calls (via OpenRouter). It holds **no ML code**.
- **quant_service** does everything that needs a real library — technical indicators,
  transformer sentiment, and PDF rendering — and owns the DuckDB cache.
- **Why the split?** n8n's embedded Python can't import `pandas-ta`, Transformers, or PDF
  libraries, so all heavy data and computation stay server-side; only short text and scores
  cross the LLM boundary.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | The quant service. (We develop on 3.13; see *Known gotchas* below.) |
| **Git** | any recent | — |
| **n8n** | 2.x (self-hosted) | Needed from Step 3 onward, not for the quant service itself. |
| **OpenRouter account** | — | LLM access from Step 3 onward. |

You can stand up and verify the quant service (Steps 0–2) **without n8n and without any API
keys**.

---

## Repository structure

```
algotrade-project/
├── quant_service/              # FastAPI service — all ML, indicators, PDF (§5)
│   ├── app.py                  #   app entrypoint: uvicorn app:app --port 8000
│   ├── routers/                #   one module per endpoint: ohlc, indicators, sentiment, report
│   ├── data/                   #   yahoo, newsapi, maya, rss, cache  (data ingestion)
│   ├── nlp/                    #   finbert, hebert, language_detect   (sentiment models)
│   ├── pdf/                    #   render (WeasyPrint), charts (matplotlib)
│   ├── schemas/                #   Pydantic models — validated at every LLM boundary
│   ├── ops/                    #   cost_log, cost_report             (observability)
│   ├── templates/              #   report.html.j2 + report.css        (Jinja2 → PDF)
│   ├── store_init.py           #   create the DuckDB schema (run once)
│   ├── ingest.py               #   pull + clean watchlist OHLC → prices cache (Step 1)
│   ├── smoke_test.py           #   cross-platform endpoint check
│   ├── store.duckdb            #   local cache/persistence (gitignored)
│   └── requirements.txt
├── n8n/                        # n8n workflows (§6)
│   ├── orchestrator.workflow.json
│   ├── agents/                 #   sentiment, earnings, technical, risk_manager sub-workflows
│   └── README_credentials.md   #   how to wire the OpenRouter credential
├── prompts/                    # version-controlled prompts (§7)
│   ├── sentiment_examples.jsonl, earnings_examples.jsonl   # few-shot examples
│   └── risk_manager_{draft,critique,final}.md             # the 3 critique passes
├── eval/                       # evaluation harness (§9)
│   ├── sentiment_labeled.jsonl, earnings_labeled.jsonl    # labeled datasets
│   └── run.py                  #   python -m eval.run → metrics → evals table
├── config/
│   ├── universe.yaml           #   watchlist, windows, lookback, cron, report dir (§4.4)
│   └── rubric.yaml             #   Risk Manager decision thresholds (§3.4)
├── reports/                    # generated PDFs — reports/YYYY-MM-DD/HHMM/report.pdf (gitignored)
├── docs/                       # design.md (source of truth) + results, demo, architecture
├── .env.example                # copy to .env and fill in keys (only the example is committed)
├── CLAUDE.md                   # build instructions & guardrails
└── README.md
```

Many files are currently **placeholders** that later build steps fill in — see the roadmap
below for which step owns what.

---

## Setup & quick start

> Current state (through **Step 2**): **`/ohlc` and `/indicators` are real** — they serve
> cached OHLC from DuckDB and compute RSI/MACD/Bollinger/ATR with `pandas-ta` (§5, §3.3),
> **lazily (re-)ingesting** whenever the cache can't cover the requested `lookback_days` —
> so a symbol you haven't pre-pulled is fetched on first request, and a later request for a
> wider window transparently widens the cache (order-independent, since the Technical agent
> picks its lookback per task). `/sentiment` and `/report` still return **stubs matching the §5
> contracts** (made real in Steps 4 and 9). `ingest.py` remains the batch pre-warm path
> (§4.1, §4.3). All of this is verifiable without n8n or any API keys.

### 1. Clone and create a virtualenv

```bash
git clone <repo-url>
cd algotrade-project/quant_service
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS/Linux:           source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` lists the **full** stack (used by later steps). To stand up the skeleton
(Step 0) you only need the core subset; **Step 1 ingestion** adds the data libraries:

```bash
# Step 0 — stub endpoints:
pip install fastapi "uvicorn[standard]" "pydantic>=2" duckdb pyyaml jinja2
# Step 1 — real OHLC ingestion also needs:
pip install yfinance certifi pandas numpy
# Step 2 — real /ohlc + /indicators also needs:
pip install pandas-ta
```

### 3. Configure environment

```bash
# Windows (PowerShell):  Copy-Item ..\.env.example ..\.env
# macOS/Linux:           cp ../.env.example ../.env
```

**No API keys are required yet** — fill them in as later steps need them
(see [Configuration & API keys](#configuration--api-keys)).

### 4. Initialize the DuckDB schema (§4.2)

```bash
python store_init.py
# Verify the seven tables exist:
python -c "import duckdb; print(sorted(r[0] for r in duckdb.connect('store.duckdb').execute('show tables').fetchall()))"
# Expect: ['costs', 'earnings', 'evals', 'news', 'prices', 'recommendations', 'runs']
```

### 5. Run the service

```bash
uvicorn app:app --port 8000
```

Leave it running; interactive API docs are at <http://localhost:8000/docs>.

### 6. Verify the endpoints

**Easiest (any OS)** — in a *second* terminal, with the service running:

```bash
cd quant_service
python smoke_test.py
# Expect: "OK" for /ohlc, /indicators, /sentiment, /report, then "All endpoints OK."
```

<details>
<summary><b>Manual requests per shell</b> (click to expand)</summary>

**PowerShell (Windows).** ⚠️ In PowerShell `curl` is an alias for `Invoke-WebRequest`, which
does **not** accept bash-style `-X`/`-H`/`-d` flags — using them yields a **422** because the
JSON body never reaches the service. Use `Invoke-RestMethod`:

```powershell
$body = @{ symbol="TEVA.TA"; lookback_days=180; interval="1d" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/ohlc -Method Post -ContentType "application/json" -Body $body

$body = @{ items=@(@{id="a1";text="Teva beats Q1 estimates";language="en"}, @{id="a2";text="report";language="he"}) } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/sentiment -Method Post -ContentType "application/json" -Body $body
```

(Or use the real `curl.exe` explicitly — not the alias — with escaped quotes:
`curl.exe -X POST http://localhost:8000/ohlc -H "content-type: application/json" -d '{\"symbol\":\"TEVA.TA\"}'`.)

**bash / macOS / Linux** (real curl):

```bash
curl -s -X POST localhost:8000/ohlc       -H "content-type: application/json" -d '{"symbol":"TEVA.TA","lookback_days":180,"interval":"1d"}'
curl -s -X POST localhost:8000/indicators -H "content-type: application/json" -d '{"symbol":"TEVA.TA","lookback_days":120,"indicators":["rsi","macd","bbands","atr"]}'
curl -s -X POST localhost:8000/sentiment  -H "content-type: application/json" -d '{"items":[{"id":"a1","text":"Teva beats Q1 estimates","language":"en"},{"id":"a2","text":"report","language":"he"}]}'
curl -s -X POST localhost:8000/report     -H "content-type: application/json" -d '{"run_id":"r_2026-06-22T13:00","recommendations":[],"summary":"3 long, 1 hold, 1 avoid."}'
```

</details>

Each response should match the corresponding §5 example shape in `docs/design.md`.

### 7. Ingest real market data (Step 1)

With the schema in place, pull and clean the watchlist's daily OHLC into the `prices` cache.
The watchlist and lookback come from `config/universe.yaml` (§4.4); the §4.3 cleaning rules
(adjusted close, TASE Sun–Thu calendar alignment, one-day-gap forward-fill, 8×MAD outlier
flagging) are applied automatically. **No API key needed** — Yahoo Finance is keyless.

```bash
cd quant_service
python ingest.py                         # full watchlist: TEVA, NICE, LUMI, POLI, ESLT (.TA)
#   python ingest.py --symbols TEVA.TA   # one or more explicit symbols
#   python ingest.py --lookback-days 90  # override the 180-day default
```

Expect one row per symbol with `fetched / written / filled / dropped` counts and a date
range (e.g. `178 rows  2025-10-09 … 2026-06-25`), then `Total in prices: N rows`. A
`DEGRADED — <reason>` line means an external failure — **no data is fabricated** (§9.4);
just re-run later. (`written` can exceed `fetched`: stale weekend rows are dropped and
single-day gaps are forward-filled onto the Sun–Thu grid.)

**Inspect what landed** (read-only, safe while nothing else holds the DB open):

```bash
python -c "import duckdb; con=duckdb.connect('store.duckdb', read_only=True); print(con.sql('SELECT symbol, count(*) AS n, min(ts) AS first, max(ts) AS last FROM prices GROUP BY symbol ORDER BY symbol'))"
```

Or browse interactively with the DuckDB CLI (`winget install DuckDB.cli`):

```bash
duckdb store.duckdb        # then:  SELECT * FROM prices LIMIT 20;   (Ctrl-D to exit)
```

---

## Configuration & API keys

Defaults (watchlist, news/earnings windows, lookback, cron, report dir) live in
`config/universe.yaml`; Risk Manager rubric thresholds in `config/rubric.yaml` (§4.4).
Edit those rather than hardcoding values.

Secrets come from environment variables (`.env`, gitignored). **None are required for the
quant-service skeleton** — the endpoints return stubs and read no secrets. Fill keys in as
the build reaches the step that uses them:

| Variable | First needed | Where to get it |
|---|---|---|
| `OPENROUTER_API_KEY` | Step 3 (first LLM call) | <https://openrouter.ai> → sign up → **Keys** → create key. Pay-as-you-go; a few dollars covers many runs. |
| `NEWSAPI_API_KEY` | Step 5 (Sentiment Agent) | <https://newsapi.org/register> → free tier (100 req/day). |
| `ALPHAVANTAGE_API_KEY` | — (not usable) | Listed in the design as the OHLC backup, but **verified to have no TASE (`*.TA`) coverage** (free tier; `TEVA.TLV`/`.TA` → *"Invalid API call"*, and adjusted data is premium). Yahoo Finance is the only working source for this watchlist — safe to leave blank. See [Step 1 notes](#known-gotchas). |
| `QUANT_SERVICE_URL` | Step 3 (n8n → service) | Leave `http://localhost:8000` for local; use `http://host.docker.internal:8000` if n8n runs in Docker. |
| `DUCKDB_PATH` | Step 1 | Leave default `quant_service/store.duckdb`. |
| `REPORT_DIR` | Step 9 | Leave default `reports`. |
| `TZ` | Step 10 | Leave `Asia/Jerusalem` (store UTC, render local). |
| `HF_HOME` | Step 4 | Local Hugging Face cache dir, e.g. `.hf_cache` (avoids re-downloading FinBERT/HeBERT). |

---

## Build roadmap

The system is built and reviewed **one step at a time** (full detail in `CLAUDE.md`).

| Step | What | Status |
|---|---|---|
| 0 | Scaffold: repo layout, FastAPI stubs, DuckDB schema, config, template skeleton | ✅ done |
| 1 | Data ingestion: Yahoo OHLC + cleaning → `prices` (`python ingest.py`) | ✅ done |
| 2 | `/ohlc` + `/indicators` real (`pandas-ta`) | ✅ done |
| 3 | Technical Agent sub-workflow (n8n + Gemini Flash-Lite) | ⬜ |
| 4 | `/sentiment` real (FinBERT + HeBERT) | ⬜ |
| 5 | Sentiment Agent sub-workflow (dual scoring, few-shot, `news` table) | ⬜ |
| 6 | Earnings Agent (Maya scraping + self-consistency number extraction) | ⬜ |
| 7 | Risk Manager three-stage critique loop | ⬜ |
| 8 | Orchestrator fan-out + cost logging | ⬜ |
| 9 | `/report` real (WeasyPrint + Jinja2) | ⬜ |
| 10 | Schedule trigger gated by TASE hours | ⬜ |
| 11 | Evaluation harness (`python -m eval.run`) | ⬜ |
| 12 | README + supporting docs for a grader | ⬜ |

---

## Contributing (for the team)

- **`docs/design.md` is the source of truth.** If code and the doc conflict, the doc wins.
  Changing a contract/schema/table means updating the doc first.
- **One step per change**, with a runnable way to verify it (see `CLAUDE.md` guardrails).
- **Keep secrets out of git** — only `.env.example` is committed; `.env`, `*.duckdb`,
  `reports/`, and the HF cache are gitignored.
- **No ML in n8n** — indicators, transformers, and PDF rendering live only in the quant
  service and are reached over HTTP.

### Known gotchas

- **PowerShell `curl`** is `Invoke-WebRequest`, not real curl — use `python smoke_test.py`,
  `Invoke-RestMethod`, or `curl.exe` (see the expandable section above).
- **Corporate TLS proxy / antivirus (Step 1):** behind a TLS-inspecting proxy, `yfinance`'s
  HTTP backend can fail certificate verification — and yfinance often surfaces it as a
  *misleading* "Too Many Requests / rate limited" (it is **not** actually a rate limit). The
  ingester auto-fixes this on Windows by trusting the OS certificate store
  (`data/yahoo.py: configure_tls()` builds a `certifi` + Windows-root CA bundle) — **no action
  needed**. On other OSes behind such a proxy, point `CURL_CA_BUNDLE` at your corporate CA
  bundle before running `python ingest.py`.
- **DuckDB CLI version:** the CLI can only open `store.duckdb` if its version matches the
  Python `duckdb` library that wrote it. Check
  `python -c "import duckdb; print(duckdb.__version__)"` and install the same CLI version (an
  older CLI refuses with *"newer DuckDB"*).
- **Single-writer DB:** close the DuckDB CLI / any open connection before running
  `ingest.py`, or you'll get *"file is being used by another process."*
- **`pandas-ta` on numpy 2.x (Step 2):** older `pandas-ta` builds do `from numpy import NaN`,
  an alias **removed in numpy 2.0**, so importing them crashes on the numpy 2.x this project
  uses. `indicators_calc.py` restores the alias with a one-line, non-destructive shim
  (`np.NaN = np.nan`) **before** importing `pandas_ta` — no numpy downgrade, no action needed.
- **`WeasyPrint` on Windows (Step 9):** needs the GTK runtime; we'll flag specifics when that
  step lands.
