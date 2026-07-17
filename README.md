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

**Where the build stands:** Steps 0–10 are done — the full team runs end-to-end, persists its
results, and renders the **PDF report** (`/report`): per-ticker pages with the dual-sentiment
panel, the three-pass reasoning trace, earnings figures with confidence markers, news
citations, a price chart, and a methodology footer. The orchestrator also runs on a
**TASE-hours schedule** (Sun–Thu, gated to 09:30–17:30 Asia/Jerusalem) alongside the manual
trigger. See the [roadmap](#build-roadmap).

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | The quant service. |
| **Node.js** | ≥ 20 | Runs the dev scripts and n8n itself (via `npx` — nothing to install by hand). |
| **Git** | any recent | — |
| **OpenRouter account** | — | Every LLM call. Pay-as-you-go; a few dollars covers many runs. |
| **NewsAPI account** | free tier | Optional — English news for the Sentiment Agent; it falls back to RSS without one. |

The quant service and its endpoints work **without n8n and without any API keys**. Keys are
only needed once you run the agents.

---

## Setup & quick start

### 1. Clone, then bootstrap once

```bash
git clone <repo-url>
cd algotrade-project
npm run setup
```

`npm run setup` is idempotent and does the whole bootstrap: creates `quant_service/.venv`,
installs `requirements.txt`, downloads the Playwright Chromium the Earnings agent needs,
copies `.env.example` → `.env` (**never** overwriting an existing one), and creates the seven
DuckDB tables. Re-run it any time.

> First run downloads PyTorch and a headless Chromium — budget a few minutes and ~2 GB.

### 2. Add your keys to `.env`

| Key | Needed for |
|---|---|
| `OPENROUTER_API_KEY` | Every LLM call. **Also paste it into the n8n OpenRouter credential** — n8n reads credentials from its own store, not from `.env`. |
| `NEWSAPI_API_KEY` | English news for the Sentiment Agent. Optional: without it, `/news/fetch` returns RSS-only and the agent reports `status: "degraded"`. |
| `N8N_API_KEY` | Cost logging. n8n → *Settings → n8n API* → create key. Without it the cost harvest degrades cleanly; it never fails a run. |

Everything else in `.env` can keep its default — see [Configuration](#configuration).

### 3. Run everything

```bash
npm run dev
```

One command, both processes: it starts the quant service, waits for its `/health`, then starts
n8n. Output is prefixed `[svc]` / `[n8n]` in a single terminal, and **Ctrl-C stops both**.

| | |
|---|---|
| quant service | <http://localhost:8000> — interactive API docs at `/docs` |
| n8n editor | <http://localhost:5678> |

`npm run dev` also loads `.env` into **both** processes and sets the n8n variables the
workflows require. If you ever start the service or n8n by hand, read *What the runner wires
up* under [Troubleshooting](#troubleshooting) first — there are four footguns it covers for
you.

### 4. Verify

In a second terminal, with `npm run dev` running:

```bash
npm run smoke
# Expect: OK for /ohlc, /indicators, /sentiment, /report, /validate,
# /riskmanager/context, /runs/*, then "All endpoints OK."
```

### 5. Import the n8n workflows (one-time, in the UI)

This is the one part the scripts can't do for you — importing mints new workflow ids and
credentials must be selected in the editor. Full walkthrough:
**[`n8n/README_credentials.md`](n8n/README_credentials.md)**. The short version:

1. In n8n, create the **OpenRouter** credential once (paste `OPENROUTER_API_KEY`).
2. *Workflows → Import from File* for each of `n8n/agents/*.json`, then
   `n8n/orchestrator.workflow.json`.
3. Open each **Chat Model** node and re-select your OpenRouter credential — imported JSONs
   carry a `REPLACE_AFTER_IMPORT` placeholder. (The Earnings Agent has **two** such nodes.)
4. Copy each imported workflow's id (it's in the editor URL, `/workflow/<id>`) into
   `config/universe.yaml → n8n_workflow_ids`. The orchestrator calls its agents **by id**, so
   these must match.

### 6. Run the team

Hit **Execute workflow** on the orchestrator. It opens a run, fans the three analysis agents
out over the watchlist (concurrency 3), calls the Risk Manager per ticker, persists each
result, harvests the run's LLM costs, and closes the run.

> **Trim the watchlist for a first run.** The committed watchlist is all 35 TA-35 names — that
> is 35 × 4 sub-workflows and burns the NewsAPI free tier (100 req/day) in one go. Set
> `watchlist: ["TEVA.TA","ESLT.TA"]` in `config/universe.yaml`; the orchestrator reads it from
> the service, so no workflow edit is needed.

Check what the run wrote:

```bash
npm run costs      # every LLM call of the run, priced per the §7 table
```

Expect one `runs` row, one `recommendations` row per ticker with `draft`, `critique`, `final`
and `agent_status` populated, and `costs` rows per `(run_id, agent, model)`.

To run a **single agent** standalone, pin mock input on its Execute Workflow Trigger:

- Technical — `[{ "ticker":"TEVA.TA","lookback_days":180,"run_id":"r_test" }]`
- Sentiment — `[{ "ticker":"TEVA.TA","window_minutes":43200,"run_id":"r_test" }]`
- Earnings — `[{ "ticker":"TEVA.TA","window_days":30,"run_id":"r_test" }]`

Widen the windows as shown: the 2-hour news and 5-day earnings defaults are often legitimately
empty, which looks like a bug but isn't.

### 7. Scheduled runs (TASE hours)

The orchestrator also carries a **Schedule Trigger** (`schedule_cron` from `config/universe.yaml`,
`0 10-17 * * 0-4` — hourly, Sun–Thu 10:00–17:00). For the cron to fire on *local* time you must set
**`GENERIC_TIMEZONE=Asia/Jerusalem`** in n8n's environment — n8n evaluates Schedule Trigger crons in
`GENERIC_TIMEZONE`, **not** `TZ`; left unset it defaults to `America/New_York` and the schedule
fires at the wrong hours (this is a common n8n footgun). Keep `TZ=Asia/Jerusalem` as well (OS clock).
The workflow must also be **Published/Active** (in n8n 2.x the *Publish* button top-right; older
builds have an Active toggle) — inactive workflows never fire on schedule. n8n does **not** backfill
missed cron ticks, so if the process isn't running at the exact top of the hour that fire is skipped.
The Manual Trigger is unaffected and always runs.

Every scheduled fire passes through an in-workflow **TASE Hours Gate** (Sun–Thu, 09:30–17:30
Asia/Jerusalem) that sits *before* `/runs/start`. Outside those hours it exits at a No-Op with
**no `runs` row written** (§11.2); the manual path bypasses the gate. Scheduled runs are recorded
with `mode: "scheduled"`; manual runs with `mode: "manual"`.

**Testing the gate deterministically** (the gate reads the real clock, so you can't force
off-hours on demand): set `TASE_GATE_FAKE_NOW` in n8n's env to an ISO timestamp and the gate
evaluates *that* instant instead. **This is a dev-only override — leave it unset in production.**

```bash
# Outside hours → gate false, ends at the No-Op, runs count unchanged, no report
TASE_GATE_FAKE_NOW=2026-07-17T12:00:00+03:00   # a Friday

# Inside hours → full pipeline, new runs row with mode "scheduled"
TASE_GATE_FAKE_NOW=2026-07-15T11:00:00+03:00   # a Wednesday midday
```

Execute the **Schedule Trigger** node (n8n's "Execute step") after setting the var and restarting
n8n; confirm the `runs` count in `quant_service/store.duckdb` is unchanged for the Friday case and
grows by one (with `mode='scheduled'`) for the Wednesday case.

---

## Everyday commands

| Command | What it does |
|---|---|
| `npm run dev` | Quant service + n8n, wired. Ctrl-C stops both. |
| `npm run doctor` | Preflight — Node, venv, `.env`, keys, DuckDB, ports. Starts nothing. |
| `npm run smoke` | Endpoint check against the running service. |
| `npm run ingest` | Pull the watchlist's OHLC into the `prices` cache (keyless — Yahoo Finance). |
| `npm run costs` | Per-run LLM cost summary. |
| `npm run db:init` | Create/repair the DuckDB schema. |
| `npm run dev:service` / `npm run dev:n8n` | Just one side, for debugging. |
| `npm run dev -- --reload` | Service with uvicorn auto-reload. |

`npm run ingest` accepts the underlying flags: `npm run ingest -- --symbols TEVA.TA`,
`npm run ingest -- --lookback-days 90`. Ingestion is also **lazy** — `/ohlc` and `/indicators`
fetch any symbol they don't have cached, so this is a pre-warm, not a prerequisite.

---

## Configuration

**Defaults live in config, never in code or workflows.** Watchlist, news/earnings windows,
lookback, cron, report dir and the n8n workflow ids are in `config/universe.yaml`; Risk
Manager decision thresholds are in `config/rubric.yaml` (§4.4). Edit those.

**Secrets live in `.env`** (gitignored; only `.env.example` is committed). `npm run dev` loads
it into both processes.

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | — | <https://openrouter.ai> → **Keys**. |
| `NEWSAPI_API_KEY` | — | <https://newsapi.org/register> → free tier (100 req/day). Optional. |
| `N8N_API_KEY` | — | n8n → *Settings → n8n API*. The only place n8n exposes LLM token usage. |
| `QUANT_SERVICE_URL` | `http://127.0.0.1:8000` | Single source for where the service lives: n8n uses it, and the runner derives uvicorn's port from it. Use `http://host.docker.internal:8000` if n8n runs in Docker. |
| `N8N_API_URL` | `http://localhost:5678` | Where the quant service reaches n8n's REST API. |
| `DUCKDB_PATH` | `quant_service/store.duckdb` | Repo-root-relative; the runner absolutises it. |
| `HF_HOME` | `.hf_cache` | Hugging Face cache (FinBERT/HeBERT weights). |
| `REPORT_DIR` | `reports` | Generated PDFs. |
| `TZ` | `Asia/Jerusalem` | Store UTC, render local. |
| `N8N_PORT` | `5678` | Only if 5678 is taken. |
| `ALPHAVANTAGE_API_KEY` | — | **Leave blank.** Listed in the design as the OHLC backup, but verified to have no TASE (`*.TA`) coverage. Yahoo Finance is the only working source. |

---

## Repository structure

```
algotrade-project/
├── quant_service/              # FastAPI service — all ML, indicators, PDF (§5)
│   ├── app.py                  #   entrypoint: uvicorn app:app --port 8000
│   ├── routers/                #   one module per endpoint
│   ├── data/                   #   yahoo, newsapi, rss, maya, stores, cleaning, cache
│   ├── indicators/             #   pandas-ta computation behind /indicators
│   ├── nlp/                    #   finbert, hebert, language_detect
│   ├── pdf/                    #   render (WeasyPrint), charts (matplotlib)
│   ├── schemas/                #   Pydantic models — validated at every LLM boundary
│   ├── ops/                    #   cost logging + reporting (§9.4)
│   ├── store_init.py           #   DuckDB schema
│   ├── smoke_test.py           #   endpoint check
│   └── requirements.txt
├── n8n/                        # workflows (§6) + README_credentials.md
│   ├── orchestrator.workflow.json
│   └── agents/                 #   sentiment, earnings, technical, risk_manager
├── prompts/                    # version-controlled prompts & few-shot examples (§7)
├── eval/                       # evaluation harness (§9)
├── config/                     # universe.yaml (defaults) + rubric.yaml (thresholds)
├── scripts/                    # the npm dev scripts (Node stdlib only, no dependencies)
├── docs/                       # design.md — the source of truth
├── package.json
├── .env.example
└── CLAUDE.md                   # build instructions & guardrails
```

---

## Build roadmap

Built and reviewed **one step at a time** (detail in `CLAUDE.md`).

| Step | What | Status |
|---|---|---|
| 0 | Scaffold: repo layout, FastAPI stubs, DuckDB schema, config | ✅ done |
| 1 | Data ingestion: Yahoo OHLC + cleaning → `prices` | ✅ done |
| 2 | `/ohlc` + `/indicators` real (`pandas-ta`) | ✅ done |
| 3 | Technical Agent sub-workflow + `/validate` endpoint | ✅ done |
| 4 | `/sentiment` real (FinBERT + HeBERT) | ✅ done |
| 5 | Sentiment Agent sub-workflow (dual scoring, few-shot, `news` table) | ✅ done |
| 6 | Earnings Agent (Maya scraping + self-consistency number extraction) | ✅ done |
| 7 | Risk Manager three-stage critique loop | ✅ done |
| 8 | Orchestrator fan-out + cost logging | ✅ done |
| 9 | `/report` real (WeasyPrint + Jinja2) | ✅ done |
| 10 | Schedule trigger gated by TASE hours | ✅ done |
| 11 | Evaluation harness (`python -m eval.run`) | ⬜ |
| 12 | Chat assistant front end (bonus, §6.5) | ⬜ |
| 13 | S&P 500 market abstraction (config + calendar + news/schedule gate) | ⬜ |
| 14 | SEC EDGAR earnings source + report currency | ⬜ |
| 15 | README + supporting docs for a grader | ⬜ |

Step 15 (README) is intentionally last: it is the grader-facing README and demo docs, so it is
written once, at the end, with the chat assistant (Step 12) and S&P 500 support (Steps 13–14)
already in place.

**S&P 500 support (Steps 13–14):** mixed TA-35 + S&P watchlist, SEC EDGAR earnings, per-market
schedule gate. Full plan: [`docs/sp500_integration_plan.md`](docs/sp500_integration_plan.md).

---

## Contributing

- **`docs/design.md` is the source of truth.** If code and the doc conflict, the doc wins.
  Changing a contract/schema/table means updating the doc first.
- **One step per change**, with a runnable way to verify it (see `CLAUDE.md` guardrails).
- **No ML in n8n** — indicators, transformers, and PDF rendering live only in the quant
  service, reached over HTTP.
- **Keep secrets out of git** — only `.env.example` is committed.
- **Store UTC, render Asia/Jerusalem.** DuckDB's `TIMESTAMP` is timezone-*naive*: binding an
  aware datetime makes it convert to local time and drop the offset. Writers must normalize to
  naive UTC first (`data/run_store.py: _db_utc()`).

---

## Troubleshooting

- **First `/sentiment` call is slow.** It downloads FinBERT + HeBERT into `HF_HOME` (~1.7 GB on
  disk) and loads them into memory — tens of seconds. Later calls reuse the in-process
  pipeline. If it degrades with a *download* error even though the weights are cached (a
  TLS-intercepting network breaks the hub check), add `HF_HUB_OFFLINE=1` to `.env`.
- **Maya (earnings) scraping fails sometimes.** Expected, and honest: Maya sits behind bot
  protection, so `/earnings/fetch` may return a `degraded:` summary. The agent then reports "no
  recent disclosure" rather than guessing — it never fabricates figures.
- **Earnings figures all show `ambiguous`?** If you pulled an older checkout, re-run
  `npm run setup` (or `pip install -r quant_service/requirements.txt`): figure extraction needs
  the new `pypdf` dependency. Without it `/earnings/fetch` still works but falls back to the
  report page, which carries no figures — so every field correctly votes `ambiguous`. Note that
  `ambiguous` is also the *right* answer for a disclosure that genuinely states no figures (a
  meeting notice, a rating affirmation).
- **PowerShell `curl` is not curl** — it's an alias for `Invoke-WebRequest`, which rejects
  `-X`/`-H`/`-d` and yields a confusing **422**. Use `npm run smoke`, `Invoke-RestMethod`, or
  `curl.exe`.
- **"File is being used by another process."** DuckDB is single-writer — close the DuckDB CLI
  or any open connection before ingesting.
- **Everything comes back `degraded` and the Risk Manager says `avoid`.** Almost always the
  wrong Python: Playwright and the TLS truststore live in `quant_service/.venv`. `npm run dev`
  uses it; a hand-started uvicorn on system Python does not. (The `avoid` is *correct* — 2+
  degraded agents force it, §3.4.)
- **`npm run doctor`** checks most of the above (venv, keys, DuckDB, ports) without starting
  anything.

<details>
<summary><b>What the runner wires up</b> — read this before starting the service or n8n by hand</summary>

Each of these is a real footgun that produces a *plausible but wrong* run. `npm run dev`
handles all of them; if you start things manually, you own them:

- **`.env` reaches the service.** The Python code never reads `.env` (only n8n does), so
  `NEWSAPI_API_KEY` / `N8N_API_KEY` must be exported in the shell that runs uvicorn.
- **`DUCKDB_PATH`, `HF_HOME`, `REPORT_DIR` are repo-root-relative**, but uvicorn must run with
  cwd `quant_service/` (its imports are flat). Exporting `DUCKDB_PATH` there resolves it to
  `quant_service/quant_service/store.duckdb` — a second, empty DB. Leave the variable **unset**
  and the service falls back to the correct absolute default.
- **n8n needs `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`**, or every `{{ $env.QUANT_SERVICE_URL }}`
  in the workflows fails with *"access to env vars denied"*. The gate is literally
  `!== 'false'`, so *unset* means blocked.
- **n8n must reach the service at `127.0.0.1`, never `localhost`** — Node resolves `localhost`
  to IPv6 first while uvicorn binds IPv4, giving `ECONNREFUSED ::1:8000`.

Manual equivalent:

```bash
cd quant_service
python -m venv .venv                    # then activate it
pip install -r requirements.txt
python -m playwright install chromium
python store_init.py
```

```powershell
# terminal 1 — from quant_service/, with DUCKDB_PATH left unset:
$env:NEWSAPI_API_KEY = "<key>"          # optional
$env:N8N_API_KEY     = "<key>"
python -m uvicorn app:app --port 8000

# terminal 2:
$env:N8N_BLOCK_ENV_ACCESS_IN_NODE = "false"
$env:QUANT_SERVICE_URL            = "http://127.0.0.1:8000"
npx n8n
```

If an n8n is already running on 5678, `npm run dev` leaves it alone and reuses it — but *that*
instance must have been started with the two variables above.

</details>

<details>
<summary><b>Implementation notes</b> — handled in code; no action needed</summary>

- **Corporate TLS proxy.** `yfinance` can fail certificate verification behind a TLS-inspecting
  proxy, and misreports it as *"Too Many Requests"* (it is **not** a rate limit).
  `data/yahoo.py: configure_tls()` builds a `certifi` + Windows-root CA bundle;
  `nlp/finbert.py: _configure_hf_tls()` reuses it for the Hugging Face download (which uses
  `httpx` and ignores `SSL_CERT_FILE`). On non-Windows behind such a proxy, point
  `CURL_CA_BUNDLE` at your corporate CA bundle.
- **`pandas-ta` on numpy 2.x.** Older builds do `from numpy import NaN`, removed in numpy 2.0.
  `indicators/calc.py` restores the alias with a non-destructive shim before the import — no
  numpy downgrade needed.
- **DuckDB CLI version.** The CLI can only open `store.duckdb` if its version matches the
  Python `duckdb` that wrote it (`python -c "import duckdb; print(duckdb.__version__)"`).
- **Maya scrape is TTL-cached** for 10 minutes, so an orchestrator run scrapes once, not once
  per ticker.
- **The Earnings agent classifies the top 3 disclosures, not the newest one.** A ticker's newest
  filing is almost always administrative — a Form 4, an "Opening of Trading" notice — while the
  results sit far below (Teva's Q1 8-K was 31 rows down; Elbit's newest was a *"we will report on
  Aug 5"* notice with the actual Q1 results 7 rows below). `/earnings/fetch` ranks disclosures by
  title and excerpts the top `earnings_candidates` (`config/universe.yaml`, default 3); the agent
  classifies each and reports the most material. Ranking is *retrieval, not classification* — the
  score never becomes `kind`/`materiality`, so a mis-ranked candidate is just classified `other/low`
  and loses. Figures are extracted from the winner only: 3 classify + 3 extract calls per ticker.
  Because candidates need not clear the Pydantic boundary on the same attempt — one may validate
  first time while a sibling needs the stricter retry — the three classification outcomes are
  reunited by a `Merge Classifications` node (append, 3 inputs) *before* selection runs. Without it,
  selection runs once per branch over a partial set and the sub-workflow returns one result per
  branch, the first of which may be the loser. This is invisible with a model that always validates
  first time, which is how it survived until the `x-ai/grok-4.3` swap surfaced it.
- **Earnings figures come from the disclosure's PDF, not its report page.** Maya publishes a
  disclosure in three layers and only the last has numbers: the report page is an SPA shell
  (its visible text is navigation, the report list, and a live stock quote); its iframe holds a
  ~1 KB cover sheet naming an attachment; the attachment — `mayafiles.tase.co.il/rpdf/…` — is
  the press release carrying revenue, EPS and guidance. `data/maya.py: _pdf_excerpt()` fetches
  that PDF and extracts text (`pypdf`). Because a results PDF opens with several pages of
  SEC/MAGNA boilerplate, the excerpt is *anchored* on the first page holding real currency
  figures rather than the document's start. Unreachable or scanned PDFs (no text layer) degrade
  to the cover sheet, and figures come out `ambiguous` — never invented.
- **`WeasyPrint` on Windows (Step 9) needs the GTK3 runtime.** `pip install weasyprint`
  provides the Python package but not its native libraries, so the first render (or
  `import weasyprint`) fails with `cannot load library 'libgobject-2.0-0.dll'`. Install the
  **GTK3 runtime** once — the [tschoonj GTK-for-Windows installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases)
  (`gtk3-runtime-*-win64.exe`), keeping the "add to PATH" option — then restart the shell.
  `/report` itself never crashes without it: it degrades to `{pdf_path: null, summary:
  "degraded: …"}` and the run is marked `error`.

</details>
