# n8n setup — credentials, env, and workflow import

## Credentials (§6.4)

The OpenRouter credential is created **once at the n8n level** and reused by each agent
sub-workflow's own OpenRouter Chat Model node:

1. n8n → *Credentials* → *Add credential* → **OpenRouter** → paste `OPENROUTER_API_KEY`.
2. On n8n < 1.78 there is no OpenRouter node: use the **OpenAI** credential/node with
   base URL `https://openrouter.ai/api/v1` instead.

Workflow JSONs in this repo reference the credential as `REPLACE_AFTER_IMPORT` — after
importing a workflow, open each Chat Model node and re-select your OpenRouter credential.

## Environment

The workflows read `QUANT_SERVICE_URL` via `{{ $env.QUANT_SERVICE_URL }}`:

- n8n run directly on the host: `QUANT_SERVICE_URL=http://localhost:8000`.
- n8n in Docker (service on the host): `QUANT_SERVICE_URL=http://host.docker.internal:8000`.
- `$env` access requires `N8N_BLOCK_ENV_ACCESS_IN_NODE` to be unset or `false` (the default).

## Importing an agent sub-workflow

1. n8n → *Workflows* → *Import from File* → pick `n8n/agents/<agent>.json`.
2. Re-select the OpenRouter credential on the Chat Model node(s) (see above).
3. Save. Sub-workflows start with an **Execute Workflow Trigger** — run them via
   *Execute workflow* with pinned/manual input, or from the orchestrator (Step 8).

### Technical Agent (`agents/technical.json`, Step 3)

Input: `{ "ticker": "TEVA.TA", "lookback_days": 180, "run_id": "r_test" }`.

Flow: `POST /ohlc` → `POST /indicators` → Gemini 2.5 Flash-Lite narration
(temperature 0) → `POST /validate` (Pydantic schema `schemas/technical.py`,
served by the quant service) → on invalid: one stricter retry → on second
failure: `status: "degraded"` output. Candle arrays never enter the prompt —
only indicator values and short summaries (§2 guardrail).

Output (§3.3 + §3.4 status):
`{ ticker, as_of, indicators: {rsi_14, macd{…}, bbands{…}, atr_14}, signal, summary, status }`.

Cost logging to the `costs` table is deferred to Step 8 (orchestrator).

### Sentiment Agent (`agents/sentiment.json`, Step 5)

Input: `{ "ticker": "TEVA.TA", "window_minutes": 120, "run_id": "r_test" }`.
The 2-hour default window is often empty for the quieter TA-35 names — widen it
for a demo, e.g. `"window_minutes": 43200` (30 days), which is also the NewsAPI
free-tier cap.

Flow (§3.1): `POST /news/fetch` (NewsAPI EN + Globes/Ynet RSS EN/HE, cleaned
server-side, returns items **and** the few-shot examples) → branch:

- **Model scorer** — `POST /sentiment` (FinBERT for EN, HeBERT for HE).
- **LLM scorer** — Claude Haiku 4.5 (temperature 0), few-shot from the fetch
  response, scores each headline `-1..+1` → `POST /validate` (agent
  `"sentiment"`, `schemas/sentiment.py`) → on invalid: one stricter retry → on
  second failure the LLM side is marked degraded (the model side still counts).

The two scores are combined in **Compute & Assemble**: aggregate `llm_sentiment`
/ `model_sentiment` are the per-article means and `disagreement` is the mean
per-article `|llm − model|` (§3.1). Both scores per article are written to the
`news` table via `POST /news/store`. Only headlines/summaries and ids reach the
LLM — never article bodies (§2 guardrail).

Output (§3.1 + §3.4 status):
`{ ticker, window, llm_sentiment, model_sentiment, disagreement, n_articles, top_items[], summary, status }`.

Three terminal states: `ok` with scores; `ok` with `n_articles: 0` and neutral
scores when there is genuinely no recent coverage (§13 — never padded); and
`degraded` (with a reason) when a source, the LLM boundary, or the `news` write
fails. Cost logging is deferred to Step 8.

### Earnings Agent (`agents/earnings.json`, Step 6)

Input: `{ "ticker": "TEVA.TA", "window_days": 5, "run_id": "r_test" }`.
The 5-day default window is often empty — widen it for a demo, e.g.
`"window_days": 30`. **Server prerequisite:** the quant service machine needs
the Playwright browser once: `python -m playwright install chromium`.

Flow (§3.2): `POST /earnings/fetch` (Maya EN page rendered server-side in
headless Chromium, HE fallback, §4.3 cleaning; returns compact items — the
newest with a bounded text excerpt — **and** the few-shot examples from
`prompts/earnings_examples.jsonl`) → two LLM boundaries, both Claude Haiku 4.5:

- **Classify/translate** (temperature 0) — `kind`, `materiality`, English
  `summary`, and `title_en` for Hebrew titles → `POST /validate` (agent
  `"earnings"`) → on invalid: one stricter retry → on second failure the
  classified fields are null and the result is degraded (never guessed).
- **Self-consistency extraction** (§3.2: n=3, temperature 0.3) — one Code node
  emits three identical tasks; the single Extract chain runs once per item, so
  three independent samples of `{revenue, eps, guidance}`, each **verbatim
  from the text or null**. Each sample is validated (agent
  `"earnings_extraction"`, stricter retry per sample); a sample that fails
  twice becomes a non-vote. The **Majority Vote** Code node then commits a
  figure only when ≥2 samples agree after units normalization
  (`{value, confidence: 2|3}`) and marks everything else
  `{"value":"ambiguous","confidence":1}` — the vote is deterministic code, so
  the LLM can never vouch for its own numbers.

The classified disclosure + voted figures are written to the `earnings` table
via `POST /earnings/store`.

Output (§3.2 + §3.4 status):
`{ ticker, latest_disclosure{date,type,language,title,url,title_en,summary,extracted}, is_earnings_window, materiality, summary, status }`.

Three terminal states: `ok` with a classified disclosure; `ok` with
`latest_disclosure: null` when the scrape is healthy but nothing matched the
window (never padded); and `degraded` (with a reason) when the scrape, an LLM
boundary, or the `earnings` write fails. Cost logging is deferred to Step 8.

**Local verification notes** (Windows): use `http://127.0.0.1:8000` (not
`localhost`, which n8n resolves to IPv6 first and refuses); if `{{ $env.… }}` is
blocked in your n8n build, switch the HTTP node URL field to Fixed and paste the
URL (this edits only n8n's DB copy, not the committed JSON). Start the quant
service with `NEWSAPI_API_KEY` set in its environment; without a key the NewsAPI
source degrades and the RSS feeds still return items.
