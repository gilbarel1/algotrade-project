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
                  │  /ohlc /indicators /sentiment /report          │
                  │  /validate /riskmanager/context                │
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
| **OpenRouter account** | — | LLM access from Step 3 onward (Technical + Sentiment agents). |
| **NewsAPI account** | free tier | English news for the Sentiment Agent (Step 5). Optional — RSS still works without it. |

You can stand up and verify the quant service (Steps 0–2) **without n8n and without any API
keys**. The n8n agents need an OpenRouter key; the Sentiment Agent additionally uses a
NewsAPI key for English coverage (it degrades gracefully to RSS-only without one).

---

## Repository structure

```
algotrade-project/
├── quant_service/              # FastAPI service — all ML, indicators, PDF (§5)
│   ├── app.py                  #   app entrypoint: uvicorn app:app --port 8000
│   ├── routers/                #   one module per endpoint: ohlc, indicators, sentiment, news, earnings, report, validate, riskmanager
│   ├── data/                   #   yahoo, newsapi, rss, news_store, maya, earnings_store, textclean, cache, ingest, tls
│   ├── indicators/             #   calc  (pandas-ta computation behind /indicators)
│   ├── nlp/                    #   finbert, hebert, language_detect   (sentiment models)
│   ├── pdf/                    #   render (WeasyPrint), charts (matplotlib)
│   ├── schemas/                #   Pydantic models — validated at every LLM boundary
│   ├── ops/                    #   cost_log, cost_report             (observability)
│   ├── templates/              #   report.html.j2 + report.css        (Jinja2 → PDF)
│   ├── store_init.py           #   create the DuckDB schema (run once)
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

> Current state (through **Step 7**): the **Risk Manager sub-workflow**
> (`n8n/agents/risk_manager.json`) is live — it runs **once per ticker** after the three
> analysis agents and consumes their outputs through a **three-stage critique loop** (§3.4):
> **draft → devil's-advocate critique → final**, all Claude Haiku 4.5 at temperature 0, each
> pass a Pydantic-validated boundary (`POST /validate`, agents `"risk_draft"` / `"risk_critique"`
> / `"risk_final"`, one stricter retry then `degraded`). Prompts live in
> `prompts/risk_manager_{draft,critique,final}.md` and the rubric in `config/rubric.yaml`; both,
> plus the **deterministic** §3.4 rubric facts (directional mapping, strong-signal flags,
> agreement counts, applicable conviction caps), are served by the new **`POST
> /riskmanager/context`** endpoint — so the rubric is a *mechanism*, not just prompt text. After
> the final pass an **Apply Rubric Clamp** node re-enforces the agreement-count ceiling and the
> conviction caps deterministically, appending an audit note to the rationale rather than
> rewriting it. Output is the §6.3 shape with all three passes visible. Cost logging and the
> `recommendations`-table write are deferred to Step 8. See `n8n/README_credentials.md → Risk
> Manager`.
>
> Earlier state (through **Step 6**): the **Earnings Agent sub-workflow**
> (`n8n/agents/earnings.json`) is live — it calls **`POST /earnings/fetch`** (the Maya
> disclosure page is a JS SPA behind bot protection, so it's rendered **server-side in
> headless Playwright Chromium**, EN page primary / HE fallback, cleaned and term-matched per
> §4.3), classifies/translates the latest disclosure with Claude Haiku 4.5 at temperature 0
> (validated via `POST /validate`, agent `"earnings"`), then extracts `{revenue, eps,
> guidance}` with **self-consistency sampling** (§3.2: three samples at temperature 0.3, each
> Pydantic-validated as agent `"earnings_extraction"`, then a deterministic majority vote —
> a figure is committed with `confidence: 2|3` only when ≥2 samples agree *verbatim after
> units normalization*, anything else is `"ambiguous"`, **never fabricated**). The classified
> disclosure + voted figures persist to the `earnings` table via **`POST /earnings/store`**.
> One-time setup on the service machine: `python -m playwright install chromium`. See
> `n8n/README_credentials.md → Earnings Agent`.
>
> Earlier state (through **Step 5**): the **Sentiment Agent sub-workflow**
> (`n8n/agents/sentiment.json`) is live — it calls **`POST /news/fetch`** (NewsAPI EN +
> Globes/Ynet RSS EN/HE, cleaned and deduped server-side, few-shot examples bundled in),
> scores each headline twice (Claude Haiku 4.5 few-shot **and** `/sentiment`'s
> FinBERT/HeBERT), computes the mean per-article **disagreement**, validates the LLM output
> via `POST /validate` (agent `"sentiment"`), and persists both scores to the `news` table
> via **`POST /news/store`** (§3.1, §9.4). News fetch/clean/persist run in the quant service
> so n8n moves only compact items. To exercise it end-to-end set `NEWSAPI_API_KEY` in the
> service environment (RSS still works without it) and widen `window_minutes` for a demo (the
> 2h default is often empty). See `n8n/README_credentials.md → Sentiment Agent`.
>
> Earlier state (through **Step 4**): **`/ohlc`, `/indicators`, and `/sentiment` are real.**
> `/ohlc` and `/indicators` serve cached OHLC from DuckDB and compute RSI/MACD/Bollinger/ATR
> with `pandas-ta` (§5, §3.3), **lazily (re-)ingesting** whenever the cache can't cover the
> requested `lookback_days` — so a symbol you haven't pre-pulled is fetched on first request,
> and a later request for a wider window transparently widens the cache. **`/sentiment` now runs
> fine-tuned transformers** (§5, §7): each item is routed by language (explicit field wins, else
> Hebrew-codepoint detection) to **FinBERT** (`ProsusAI/finbert`, EN) or **HeBERT**
> (`avichr/heBERT_sentiment_analysis`, HE), scored as `P(pos)−P(neg)` in `-1..+1`, batched per
> model, with the model tag returned per item; weights cache under `HF_HOME` (**first call
> downloads ~0.9 GB and is slow; later calls reuse the in-process pipeline**). The **Technical
> Agent n8n sub-workflow** (`n8n/agents/technical.json`) is live: it calls `/ohlc` + `/indicators`,
> narrates with Gemini Flash-Lite, and validates the LLM output via the **`POST /validate`**
> endpoint (Pydantic schema in `schemas/technical.py`), with a stricter-retry-then-`degraded`
> path (§3.3, §9.4). `/report` still returns a **stub matching the §5 contract** (made real in
> Step 9). `python -m data.ingest` remains the batch pre-warm path (§4.1, §4.3). The quant-service
> endpoints (including `/validate`) are verifiable without n8n or any API keys via
> `python smoke_test.py`; running the n8n sub-workflow end-to-end needs n8n + an OpenRouter key
> (see `n8n/README_credentials.md`).

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
# Step 4 — /sentiment (FinBERT/HeBERT) also needs:
pip install transformers torch
# Step 5 — news fetch for the Sentiment Agent also needs:
pip install httpx feedparser beautifulsoup4 truststore
# Step 6 — Maya scraping for the Earnings Agent also needs:
pip install playwright
python -m playwright install chromium   # one-time headless-browser download (~150 MB)
```

Through the **current state (Step 6)** the simplest path is
`pip install -r requirements.txt` followed by `python -m playwright install chromium`.

### 3. Configure environment

```bash
# Windows (PowerShell):  Copy-Item ..\.env.example ..\.env
# macOS/Linux:           cp ../.env.example ../.env
```

**No keys are needed for the quant-service basics** (`/ohlc`, `/indicators`, `/sentiment`,
`/validate`). For the **current state (Step 5)** fill in two keys as you reach the agents
(see [Configuration & API keys](#configuration--api-keys)):

- `OPENROUTER_API_KEY` — used by n8n for every LLM call (Technical + Sentiment agents).
- `NEWSAPI_API_KEY` — used by the quant service's `/news/fetch` for English news (optional;
  RSS-only works without it).

> **The quant service reads keys from its own process environment — it does *not* load
> `.env`.** `.env` is consumed by **n8n**. So the only key the *service* needs
> (`NEWSAPI_API_KEY`) must be exported in the shell that runs `uvicorn` (see step 5). Leave
> `DUCKDB_PATH` **unset** when you start uvicorn from `quant_service/` — its `.env` value is
> relative to the repo root and would resolve to the wrong path from inside `quant_service/`
> (see *Known gotchas*).

### 4. Initialize the DuckDB schema (§4.2)

```bash
python store_init.py
# Verify the seven tables exist:
python -c "import duckdb; print(sorted(r[0] for r in duckdb.connect('store.duckdb').execute('show tables').fetchall()))"
# Expect: ['costs', 'earnings', 'evals', 'news', 'prices', 'recommendations', 'runs']
```

### 5. Run the service

Export `NEWSAPI_API_KEY` in this shell first (so `/news/fetch` reaches NewsAPI — skip it to
run RSS-only), then start uvicorn:

```powershell
# Windows (PowerShell), from quant_service/:
$env:NEWSAPI_API_KEY = "<your-newsapi-key>"   # optional; omit for RSS-only
python -m uvicorn app:app --port 8000
```

```bash
# macOS/Linux, from quant_service/:
export NEWSAPI_API_KEY="<your-newsapi-key>"   # optional; omit for RSS-only
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
python -m data.ingest                        # full watchlist: TEVA, NICE, LUMI, POLI, ESLT (.TA)
#   python -m data.ingest --symbols TEVA.TA  # one or more explicit symbols
#   python -m data.ingest --lookback-days 90 # override the 180-day default
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

### 8. Run the analysis agents in n8n (Steps 3, 5 & 6)

Three agent sub-workflows are live: the **Technical Agent** (`n8n/agents/technical.json`),
the **Sentiment Agent** (`n8n/agents/sentiment.json`), and the **Earnings Agent**
(`n8n/agents/earnings.json`). All are driven from n8n and reach the quant service over HTTP.
Full walkthrough — credential wiring, per-node URLs, and local Windows quirks — is in
**[`n8n/README_credentials.md`](n8n/README_credentials.md)**; the short version:

1. **Quant service running** (step 5 above), with `NEWSAPI_API_KEY` exported for the Sentiment
   Agent's English coverage.
2. In n8n, create the **OpenRouter** credential once (paste `OPENROUTER_API_KEY`).
3. *Workflows → Import from File →* the agent JSON. Open its **Chat Model** node(s) and re-select
   your OpenRouter credential (imported JSONs carry a `REPLACE_AFTER_IMPORT` placeholder).
   The Earnings Agent has **two** Chat Model nodes (t=0 classification, t=0.3 extraction).
4. Pin mock input on the **Execute Workflow Trigger** and run *Execute workflow*:
   - Technical: `[{ "ticker":"TEVA.TA","lookback_days":180,"run_id":"r_test" }]`
   - Sentiment: `[{ "ticker":"TEVA.TA","window_minutes":43200,"run_id":"r_test" }]`
     (widen the 2-hour default — quiet names are often empty in a 2h window).
   - Earnings: `[{ "ticker":"TEVA.TA","window_days":30,"run_id":"r_test" }]`
     (widen the 5-day default — most names have no disclosure in any given week).

**First, sanity-check the new news endpoints directly** (deterministic, no LLM needed) — with
the service running:

```bash
# fetch + clean recent news (NewsAPI EN + Globes/Ynet RSS EN/HE), with few-shot examples:
curl -s -X POST localhost:8000/news/fetch -H "content-type: application/json" -d '{"ticker":"TEVA.TA","window_minutes":43200}'
# validate the Sentiment Agent's LLM boundary (agent "sentiment"):
curl -s -X POST localhost:8000/validate   -H "content-type: application/json" -d '{"agent":"sentiment","payload":{"items":[{"id":"a1","score":1.5,"reasoning":"x"}],"summary":"s"}}'
```

The first returns cleaned `items` (stable `sha1(url)` ids) plus a `few_shot` array; re-running
yields the same ids. The second returns `valid:false` with `items.0.score: Input should be
less than or equal to 1`. (PowerShell: use `Invoke-RestMethod`, not the `curl` alias — see the
expandable block in step 6.)

**Likewise for the earnings endpoints** (the fetch takes ~15–30 s — it drives a headless
browser):

```bash
# scrape + clean recent Maya disclosures (newest item carries the text excerpt):
curl -s -X POST localhost:8000/earnings/fetch -H "content-type: application/json" -d '{"ticker":"TEVA.TA","window_days":30}'
# validate the two Earnings LLM boundaries:
curl -s -X POST localhost:8000/validate -H "content-type: application/json" -d '{"agent":"earnings","payload":{"kind":"rumor","materiality":"high","summary":"s"}}'
curl -s -X POST localhost:8000/validate -H "content-type: application/json" -d '{"agent":"earnings_extraction","payload":{"revenue":"$4.1B","eps":null,"guidance":null}}'
```

The first returns disclosure `items` (or a `degraded:` summary if Maya blocks the scrape —
never a 500); the second returns `valid:false` (`kind: Input should be 'earnings', …`); the
third returns `valid:true`.

**And for the Risk Manager** (Step 7 — deterministic, no LLM needed to inspect the rubric
facts):

```bash
# rubric facts for a contrived trio (sentiment bullish, technical bearish, earnings neutral):
curl -s -X POST localhost:8000/riskmanager/context -H "content-type: application/json" -d '{"ticker":"TEVA.TA","sentiment":{"llm_sentiment":0.5,"model_sentiment":0.45,"disagreement":0.05,"status":"ok"},"earnings":{"is_earnings_window":false,"materiality":"low","status":"ok"},"technical":{"signal":"bearish_momentum","status":"ok"}}'
# validate the three Risk Manager LLM boundaries:
curl -s -X POST localhost:8000/validate -H "content-type: application/json" -d '{"agent":"risk_draft","payload":{"recommendation":"long","conviction":"medium","rationale":"x","earnings_direction":"neutral"}}'
curl -s -X POST localhost:8000/validate -H "content-type: application/json" -d '{"agent":"risk_critique","payload":{"counter_recommendation":"hold","key_objections":[],"conviction_challenge":"x"}}'
```

The first returns `facts` with `sentiment_direction:"bullish"`, `technical_direction:"bearish"`,
per-earnings-direction `agreement_counts`, and no active caps; the two `agent:"risk_*"`
validations return `valid:true` and `valid:false` (`key_objections: List should have at least
1 item`) respectively. The three pass prompts and the full rubric come back in the same
response, so the n8n workflow never hardcodes either.

A successful Earnings run's **Finalize** node returns the §3.2 shape and writes the
classified disclosure into the `earnings` table:

```bash
python -c "import duckdb; print(duckdb.connect('store.duckdb').sql('SELECT id, symbol, kind, materiality, extracted FROM earnings').df())"
```

A successful Sentiment run's **Finalize** node returns the §3.1 shape
(`{ ticker, window, llm_sentiment, model_sentiment, disagreement, n_articles, top_items[],
summary, status }`) and writes one row per article — **both** scores — into the `news` table:

```bash
python -c "import duckdb; print(duckdb.connect('store.duckdb').sql('SELECT id, symbol, language, llm_sentiment, model_sentiment, disagreement FROM news').df())"
```

---

## Configuration & API keys

Defaults (watchlist, news/earnings windows, lookback, cron, report dir) live in
`config/universe.yaml`; Risk Manager rubric thresholds in `config/rubric.yaml` (§4.4).
Edit those rather than hardcoding values.

Secrets come from environment variables (`.env`, gitignored). For the **current state
(Step 5)** you need two: `OPENROUTER_API_KEY` (used by n8n for LLM calls) and, optionally,
`NEWSAPI_API_KEY` (used by the quant service's `/news/fetch`). The quant service reads keys
from its own process environment and does **not** load `.env` — export `NEWSAPI_API_KEY` in
the shell that runs uvicorn (n8n loads `.env` itself). Everything else keeps its default:

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
| 1 | Data ingestion: Yahoo OHLC + cleaning → `prices` (`python -m data.ingest`) | ✅ done |
| 2 | `/ohlc` + `/indicators` real (`pandas-ta`) | ✅ done |
| 3 | Technical Agent sub-workflow (n8n + Gemini Flash-Lite) + `/validate` endpoint | ✅ done |
| 4 | `/sentiment` real (FinBERT + HeBERT) | ✅ done |
| 5 | Sentiment Agent sub-workflow (dual scoring, few-shot, `news` table) | ✅ done |
| 6 | Earnings Agent (Maya scraping + self-consistency number extraction) | ✅ done |
| 7 | Risk Manager three-stage critique loop (n8n + `/riskmanager/context`) | ✅ done |
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
  bundle before running `python -m data.ingest`.
- **DuckDB CLI version:** the CLI can only open `store.duckdb` if its version matches the
  Python `duckdb` library that wrote it. Check
  `python -c "import duckdb; print(duckdb.__version__)"` and install the same CLI version (an
  older CLI refuses with *"newer DuckDB"*).
- **Single-writer DB:** close the DuckDB CLI / any open connection before running
  `python -m data.ingest`, or you'll get *"file is being used by another process."*
- **`pandas-ta` on numpy 2.x (Step 2):** older `pandas-ta` builds do `from numpy import NaN`,
  an alias **removed in numpy 2.0**, so importing them crashes on the numpy 2.x this project
  uses. `indicators/calc.py` restores the alias with a one-line, non-destructive shim
  (`np.NaN = np.nan`) **before** importing `pandas_ta` — no numpy downgrade, no action needed.
- **First `/sentiment` call is slow (Step 4):** the first request for each language downloads
  the model weights (FinBERT + HeBERT) into `HF_HOME` (default `.hf_cache`,
  gitignored) and loads them into memory — expect tens of seconds and **≈ 1.7 GB on disk** (on
  Windows the cache is copied rather than symlinked, so blobs are duplicated into the snapshot
  dir). Subsequent calls in the same
  process reuse the in-process pipeline and are fast. Behind a **corporate TLS proxy**, the
  Hugging Face download uses `httpx`, which — unlike `yfinance`'s backend — ignores
  `SSL_CERT_FILE`; `nlp/finbert.py: _configure_hf_tls()` reuses the same `configure_tls()` CA
  bundle and registers an httpx client factory that trusts it (chain/hostname/expiry checks stay
  on; only OpenSSL's strict structural check, which some corporate CAs violate, is relaxed) —
  **no action needed** on Windows.
- **Running an n8n sub-workflow locally (Step 3+):** three Windows quirks worth knowing.
  (a) n8n HTTP nodes reach the quant service at **`http://127.0.0.1:8000`**, not `localhost` —
  Node resolves `localhost` to IPv6 (`::1`) first and uvicorn binds IPv4, giving
  `ECONNREFUSED ::1:8000`. (b) `{{ $env.VAR }}` expressions can be blocked in this n8n version
  (`access to env vars denied`); for a quick local run, temporarily switch the node's URL field
  from *Expression* to *Fixed* and type the literal URL (the committed workflow keeps the
  `$env` form). (c) To test a sub-workflow standalone, **pin mock output on its Execute-Workflow
  trigger** (e.g. `[{ "ticker":"TEVA.TA","lookback_days":180,"run_id":"r_test" }]`) — otherwise
  `$json.ticker` is null and `/ohlc` returns 422. See `n8n/README_credentials.md`.
- **Sentiment Agent HTTP nodes (Step 5):** the workflow has *five* HTTP nodes (`Fetch News`,
  `Call /sentiment`, `Validate Scores`, `Validate Retry`, `Persist News`) — if `{{ $env.… }}`
  is blocked, switch the URL to *Fixed* on **each** of them. Its trigger input is
  `{ ticker, window_minutes, run_id }`.
- **`NEWSAPI_API_KEY` must be in the *service* environment (Step 5):** the quant service does
  not read `.env` (only n8n does), so export the key in the shell that runs `uvicorn` before
  starting it. Without it, `/news/fetch` marks NewsAPI degraded and returns RSS-only results —
  functional, but the Sentiment Agent will report `status: "degraded"`.
- **Don't set `DUCKDB_PATH` when starting uvicorn from `quant_service/` (Step 5):** the
  `.env` value `quant_service/store.duckdb` is relative to the **repo root**; exported into a
  shell already inside `quant_service/` it resolves to `quant_service/quant_service/store.duckdb`
  and `/news/store` fails with *"Cannot open file … cannot find the path specified."* Leave it
  unset — the service defaults to the correct absolute path.
- **Earnings Agent HTTP nodes (Step 6):** the workflow has *six* HTTP nodes (`Fetch
  Disclosures`, two classification `Validate …` nodes, two sample `Validate …` nodes,
  `Persist Earnings`) — if `{{ $env.… }}` is blocked, switch the URL to *Fixed* on **each**.
  Its trigger input is `{ ticker, window_days, run_id }`, and it has **two** Chat Model nodes
  to re-credential after import.
- **`/earnings/fetch` needs the Playwright browser (Step 6):** run
  `python -m playwright install chromium` once in the service's environment. Without it the
  endpoint degrades with `chromium missing; run: python -m playwright install chromium`
  (no 500, no fabrication). The scrape itself is **best-effort** (§13): Maya sits behind
  Imperva bot protection, so a `degraded:` summary on some runs is expected behavior — the
  agent then reports "no recent disclosure" rather than guessing. Results are TTL-cached for
  10 minutes, so an orchestrator run scrapes once, not once per ticker.
- **`WeasyPrint` on Windows (Step 9):** needs the GTK runtime; we'll flag specifics when that
  step lands.
