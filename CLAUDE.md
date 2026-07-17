# CLAUDE.md — Build Instructions

This repository implements an **n8n-based multi-agent system that produces an automated investment recommendation report for a TA-35 watchlist**. The complete technical design lives in **`docs/design.md`** — the single source of truth for architecture, contracts, schemas, and parameters.

Read `docs/design.md` in full at the start of every session before writing or changing any code.

---

## Prime directives (non-negotiable)

1. **Build one step at a time.** Implement exactly one step from the Build Order below, then **stop**. Never implement two steps in a single turn. Never jump ahead.
2. **Stop and wait for review after every step.** Do not start the next step until the reviewer explicitly says to proceed.
3. **The design doc is the source of truth.** Implement strictly against `docs/design.md`. Do not invent endpoints, fields, tables, parameters, or libraries that are not in the doc. If code and doc ever conflict, the doc wins.
4. **If something is ambiguous or looks wrong, ask — do not guess.** Surface the question in your report and stop.
5. **If manual actions / human verifications are needed during the build - stop and ask.** - do not continue until all required actions are done.
6. **Every step must be runnable and verifiable.** A step is not "done" until there is a concrete way to test it and confirm it matches the design.

---

## Required report after each step

After implementing a step and **before** writing this report, run the
`step-review` skill (`.claude/skills/step-review/`): it checks the step's diff
for design fidelity, guardrail compliance, and edge-case handling. Blocking
findings are fixed before reporting; everything else lands in "Open questions /
deviations".

End every step with this summary, then stop:

```
### Step <N> — <name>

**Design sections implemented:** §<x.y>, §<x.y>

**What I built:**
- <files created/changed and the key decisions made>

**How to verify:**
- <exact commands to run>
- <expected result — what the reviewer should see if it's correct>

**Stubbed / deferred (intentionally not done yet):**
- <anything left as a placeholder, and which later step will complete it>

**Open questions / deviations from the design (if any):**
- <anything you were unsure about, or had to decide that the doc didn't cover>

STOPPING HERE for review. I will not start Step <N+1> until told to proceed.
```

---

## Build Order

Each step maps to sections of `docs/design.md`. Build in the order listed and do not reorder.

> **Why Step 15 (README) is last.** The README and demo docs are the grader-facing deliverable, so
> they are written **once, last**, with the chat assistant (Step 12) and S&P 500 support (Steps 13–14)
> already in place — otherwise the intro, architecture blurb, quick-start, limitations, roadmap,
> `docs/results.md` and `docs/demo_script.md` would all be rewritten as soon as those steps landed.
>
> **Escape hatch.** Steps 12 (bonus), 13, and 14 (scope extension) are all optional relative to the
> graded deliverable. If any stalls, drop it and go straight to Step 15 — a finished README matters
> more than a bonus feature or a second market.

- [ ] **Step 0 — Scaffold (skeleton, no real logic).**
  Create the repo layout (§10). **Place the design document at `docs/design.md`** (the file may be delivered as `Technical_Design_Plan.md` — copy or rename so the path matches every reference in this file). Stand up the FastAPI app with all four endpoints (`/ohlc`, `/indicators`, `/sentiment`, `/report`) returning **hardcoded stub JSON matching the §5 contracts exactly**. Create the DuckDB schema (§4.2 — including `costs`), `config/universe.yaml` and `config/rubric.yaml` (§4.4), `.env.example` (§11.1), Jinja2 template skeleton (`templates/report.html.j2`), empty Pydantic schema modules (`schemas/`), and `.gitignore` covering `.env`, `*.duckdb`, `reports/`, the HF cache, and `__pycache__/`. *Verify:* service starts, every endpoint returns its contract-shaped stub, schemas/config/template files match the doc, and `docs/design.md` exists at that path.
- [ ] **Step 1 — Data ingestion: OHLC + cleaning (Milestone A, part 1).**
  Yahoo Finance ingestion for the watchlist (§4.1), cleaning rules (§4.3), `prices` cache (§4.2). *Verify:* pull the watchlist; clean rows in DuckDB; confirm TASE-calendar alignment and adjusted-close usage.
- [ ] **Step 2 — `/ohlc` + `/indicators` real (Milestone A, part 2).**
  Replace stubs with real implementations (`pandas-ta`). *Verify:* real values for a sample ticker; responses match §5 contracts.
- [ ] **Step 3 — Technical Agent sub-workflow (Milestone B1).**
  n8n sub-workflow per §3.3 and §6.2: HTTP calls to `/ohlc` and `/indicators`, then Gemini Flash-Lite narration. Pydantic schema validates the Flash-Lite output before returning. *Verify:* run the sub-workflow on one ticker; output matches §3.3 shape; a deliberately-malformed LLM response triggers the retry path.
- [ ] **Step 4 — `/sentiment` real: FinBERT + HeBERT (Milestone B2, part 1).**
  Implement the quant-service `/sentiment` endpoint per §5: language detection, FinBERT for EN, HeBERT for HE, batch input, model name in the response. Cache the HF models in `HF_HOME`. *Verify:* batch of EN+HE items returns per-item scores with the correct model tag; second call is fast (cached weights).
- [ ] **Step 5 — Sentiment Agent sub-workflow (Milestone B2, part 2).**
  Dual-model sentiment per §3.1: NewsAPI + RSS, LLM scoring with **few-shot examples loaded from `prompts/sentiment_examples.jsonl`**, parallel call to `/sentiment`, disagreement metric, Pydantic-validated output, persistence into `news`. *Verify:* sub-workflow returns the §3.1 shape; both `llm_sentiment` and `model_sentiment` populated; `disagreement` reflects their absolute difference; the news table stores both scores.
- [ ] **Step 6 — Earnings Agent sub-workflow with self-consistency (Milestone B3).**
  Scrape `maya.tase.co.il/en/reports/companies` (Hebrew page as fallback), LLM translates and classifies. Number extraction uses **self-consistency sampling: n=3 at temperature 0.3, majority-vote commit, "ambiguous" otherwise** (§3.2). Pydantic validates the output. Persist to `earnings`. *Verify:* run on a ticker with a recent disclosure and one without; numbers that appear verbatim are committed with `confidence: 3`; figures that don't appear are explicitly `"ambiguous"`, **never fabricated**.
- [ ] **Step 7 — Risk Manager three-stage critique loop (Milestone C, part 1).**
  Three sequential LLM passes per §3.4: **draft → devil's-advocate critique → final**. Prompts live in `prompts/risk_manager_draft.md`, `prompts/risk_manager_critique.md`, `prompts/risk_manager_final.md`. The agreement rubric and the dual-sentiment-disagreement conviction cap (§3.4) are applied in the final pass. Output matches the §6.3 shape, with all three passes visible. *Verify:* a contrived input where one agent disagrees with the other two produces a critique that names the disagreement and a final whose rationale references it.
- [ ] **Step 8 — Orchestrator fan-out + cost logging (Milestone C, part 2).**
  Top-level workflow: `run_id`, parallel fan-out across the watchlist (concurrency 3), gather outputs, call Risk Manager per ticker, write `runs` and `recommendations`. Every LLM call writes to `costs` (§4.2). *Verify:* one orchestrator run produces a `runs` row, one `recommendations` row per ticker (with `draft`, `critique`, `final` populated), and a `costs` row per LLM call.
- [ ] **Step 9 — `/report` real (Milestone D, part 1).**
  WeasyPrint + Jinja2 per §8: per-ticker pages, executive summary, **dual-sentiment panel showing both scores and disagreement**, **reasoning trace showing draft/critique/final**, **earnings figures with `confidence` markers (ambiguous figures visually distinct)**, citation blocks, methodology footer, chart PNG thumbnails. *Verify:* PDF produced at `reports/YYYY-MM-DD/HHMM/report.pdf`; every block in §8.1 is present and renders correctly.
- [ ] **Step 10 — Schedule trigger gated by TASE hours (Milestone D, part 2).**
  Schedule Trigger using `schedule_cron`; gate inside the workflow on TASE trading hours (Asia/Jerusalem) per §6.1 and §11.2. *Verify:* scheduled run inside hours produces a report; scheduled run outside hours exits cleanly with no `runs` row.
- [ ] **Step 11 — Evaluation harness (Milestone E, part 1).**
  Create `eval/sentiment_labeled.jsonl` (30 items, ~20 EN ~10 HE) and `eval/earnings_labeled.jsonl` (10 Maya disclosures). Implement `python -m eval.run` to score all agents (§9) and print a one-page summary; the harness's own LLM calls are cost-logged to `costs` under an `eval-*` run id. *Verify:* `python -m eval.run` produces metrics for every row in the §9.2 table; the summary fits on one page.
- [ ] **Step 12 — Chat assistant front end (bonus).**
  Conversational entry point to the team per **§6.5**: `n8n/chat_assistant.workflow.json` — Chat Trigger → AI Agent (Haiku 4.5, temp 0) with Simple Memory and a **Call n8n Workflow Tool** bound to the orchestrator. Give the orchestrator an **Execute Workflow Trigger** taking an optional `tickers` input (alongside the existing Manual/Schedule triggers) so one ad-hoc ticker can be analyzed without duplicating the pipeline. **The chat agent is a router, not an analyst** (§6.5): it may only call the tool and relay what it returns — it never emits a recommendation, conviction, score, or financial figure of its own. Add the chat workflow's id to `n8n_workflow_ids` so its tokens reach `costs` (§9.4). *Verify:* asking "what do you think about Teva?" runs the full pipeline for `TEVA.TA` and returns the **Risk Manager's** final call + conviction (a new `runs` row with `mode: "chat"`, a `recommendations` row, and `costs` rows including the `chat` agent); asking for a price target or a figure the tools did not return makes the assistant decline rather than invent one.
- [ ] **Step 13 — S&P 500 market abstraction (scope extension; design-first).**
  Amend `docs/design.md` first (doc wins), then implement everything that is verifiable **offline**, per §A/§B/§C-Step-14–15/§C-Step-17-gate of `docs/sp500_integration_plan.md`: the `markets:` block and keyed `rss_feeds`/`search_terms` in `config/universe.yaml`; a `market(symbol)` helper (`quant_service/data/markets.py`: `*.TA`→`tase`, bare→`us`, plus config accessor); market-aware calendar/ingestion in `data/yahoo.py` (replace `TASE_CLOSED_WEEKDAYS`/`_tase_sessions` with a per-market closed-weekday grid) and `data/cache.py` gap heuristics; `/news/fetch` RSS-group selection by market with `en_us` feeds added; and the per-market schedule gate (generalize the TASE-hours gate to the ticker's market, widen `schedule_cron`). Rubric, Risk Manager, schemas, costs, evals stay market-agnostic and unchanged. *Verify:* `market("TEVA.TA")=="tase"`, `market("AAPL")=="us"`; ingest `AAPL` — Fridays present, no Sunday rows; ingest `TEVA.TA` — unchanged vs. current (regression); scheduled run at 20:00 Israel analyzes only US names, at 11:00 only TASE names; manual/chat runs never filtered; existing smoke test green.
- [ ] **Step 14 — SEC EDGAR earnings source + report currency (scope extension; live-verified).**
  Per §C-Step-16 and §C-Step-18(report) of `docs/sp500_integration_plan.md`: new `data/edgar.py` — ticker→CIK from `company_tickers.json` (cached), recent filings from `data.sec.gov/submissions/CIK##########.json`, filter to 8-K/10-Q/10-K within `earnings_window_days`, pull the EX-99.* press-release exhibit text for the newest as the bounded `excerpt`. Plain `httpx` (no Playwright), declared `User-Agent` (contact email) per SEC policy, TTL-cache like Maya, degrade-never-500. `/earnings/fetch` routes by `market(ticker)`; **response contract unchanged** so the n8n Earnings Agent sub-workflow needs no changes. Report template shows market + currency per ticker page and a market-grouped executive summary. *Verify:* `/earnings/fetch` for `AAPL` after a real filing returns items with an excerpt; the agent commits only verbatim figures (`confidence: 3`) and marks absent ones `ambiguous`; `TEVA.TA` still routes to Maya; one mixed-watchlist run (`["TEVA.TA","AAPL"]`) produces a single PDF with both tickers, correct currencies, and a market-grouped summary.
- [ ] **Step 15 — README + supporting docs (Milestone E, part 2). Build last.**
  Write `README.md` for a grader (not just a developer): one-paragraph intro, screenshots of a report page (`docs/screenshots/`), quick-start commands, **the evaluation harness results pasted in**, a "Design highlights" section pointing at the AI techniques in §7, and a `Limitations` section mirroring §13. Document the **chat assistant** (Step 12) and **S&P 500 / mixed watchlist** (Steps 13–14) as entry points alongside the manual and scheduled triggers. Also write `docs/results.md` (walk-through of 1–2 real runs end-to-end with screenshots of the reasoning trace) and `docs/demo_script.md` (5-minute defense outline with file/screen citations). Export `docs/architecture.svg` from the §2 Mermaid diagram. *Verify:* a fresh reader can run the system end-to-end from the README alone; the AI techniques and limitations are visible without reading code.


---

## Technical guardrails (apply at every step)

- **No machine learning inside n8n.** All ML, indicators, and PDF rendering live in the FastAPI quant service and are reached over HTTP (§2).
- **Contract & schema fidelity.** Endpoint shapes match §5 exactly; agent JSON shapes match §3 exactly; tables match §4.2 exactly. Renames or extra fields require a doc update first and a flag in the step report.
- **Pydantic at every LLM boundary.** Every LLM-to-agent return is validated against a Pydantic schema in `schemas/`. A malformed response triggers one automatic retry with a stricter instruction, then a `degraded` result. No try/except that silently swallows a bad response.
- **Heavy data stays server-side.** OHLC arrays, news bodies, rendered HTML never traverse the LLM. The LLM sees short text and scores; everything else moves through DuckDB and the quant service (§2, §4.2).
- **Never invent financial numbers.** The Earnings Agent reports figures only when they appear verbatim in the source. The §3.2 self-consistency rule (n=3, temperature 0.3, majority-vote commit, `ambiguous` otherwise) is the mechanism, not just an instruction.
- **Dual sentiment is a feature, not a bug.** Both scores are stored, both are shown in the PDF, and disagreement caps Risk Manager conviction at `medium` when above the configured threshold (§3.4).
- **Risk Manager runs three passes, always.** Never collapse the loop into one call, even when it "seems obvious." The three passes are the differentiator and must be visible in the PDF (§3.4, §8.1).
- **Hebrew/English handling.** Persisted text retains a `language` field; translations are flagged as such in the report so a reviewer can trace back to the source URL (§3.1, §3.2, §8.1).
- **Few-shot prompts live in `prompts/`**, not hardcoded in workflows. They are version-controlled and used by the evaluation harness.
- **Cost logging on every LLM call.** `{run_id, agent, model, input_tokens, output_tokens, usd_cost, latency_ms}` to `costs` (§4.2, §9.4).
- **Degraded-mode behavior.** On any external failure or rate-limit, return a degraded result with reason; Risk Manager downgrades conviction. No fabrication, no silent fallbacks (§9.4, §11.2).
- **Times.** Store UTC; render Asia/Jerusalem (§11.2).
- **Secrets.** Only `.env.example` is committed; `.env` is gitignored. All keys from env vars (§11.1).
- **Determinism.** `temperature=0` for single-shot LLM calls; `temperature=0.3` only inside self-consistency sampling (§11.2).
- **Defaults from config.** Watchlist, news window, earnings window, lookback, cron, report directory in `config/universe.yaml`; rubric thresholds in `config/rubric.yaml`. Never hardcode (§4.4).

---

## Stack & conventions

- Python service: **FastAPI + uvicorn**, `uvicorn app:app --port 8000`.
- Store: **DuckDB** (`quant_service/store.duckdb`).
- Indicators: `pandas-ta`. NLP: Hugging Face `transformers` for FinBERT (`ProsusAI/finbert`) and HeBERT (`avichr/heBERT_sentiment_analysis`). PDF: **WeasyPrint** over **Jinja2**. Charts: matplotlib PNG.
- Orchestration: **self-hosted n8n 2.x**. LLM access via **OpenRouter** (models per §7).
- In Step 0, create a `README.md` (or `Makefile`) documenting the exact run/test commands so the reviewer can verify each step without guessing.
- Small focused commits per step; a runnable test or script per endpoint as it becomes real.

---

## When to ask vs. proceed

- **Ask (and stop)** if the design is silent or ambiguous; a requirement seems internally inconsistent; an external source behaves differently than the doc assumes; or a step can't be verified as written.
- **Proceed** if the doc specifies it clearly. Implement it as written — do not "improve" the design unilaterally.

