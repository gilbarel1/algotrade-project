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

The workflows read `QUANT_SERVICE_URL` via `{{ $env.QUANT_SERVICE_URL }}`, so it must be set
in **n8n's own environment** (not the service's, and not `.env` alone):

- n8n run directly on the host: `QUANT_SERVICE_URL=http://127.0.0.1:8000`. Use the IPv4
  literal, **not** `localhost` — Node resolves `localhost` to IPv6 first and uvicorn binds
  IPv4, which surfaces as `ECONNREFUSED ::1:8000`.
- n8n in Docker (service on the host): `QUANT_SERVICE_URL=http://host.docker.internal:8000`.
- **`$env` is blocked by default and must be explicitly enabled:** without
  `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` every `$env` expression fails with *"access to env
  vars denied"* (the gate is literally `!== 'false'`, so unset is **blocked**, not allowed).
  Set both variables in the shell that launches n8n and restart it:

  ```powershell
  $env:N8N_BLOCK_ENV_ACCESS_IN_NODE = "false"
  $env:QUANT_SERVICE_URL            = "http://127.0.0.1:8000"
  npx n8n
  ```

## The n8n API key (Step 8 — cost logging)

`/costs/harvest` reads LLM token usage out of n8n's REST API, because **n8n never exposes it
to the workflow itself**: the LLM chain node emits only its text, and a Code node cannot read
the Chat Model sub-node's run data. The usage *is* recorded in the execution, so the quant
service reads it back from there.

1. n8n → *Settings* → *n8n API* → **Create an API key**.
2. Export it for the **quant service** (not n8n): `N8N_API_KEY=<key>`, plus
   `N8N_API_URL=http://localhost:5678`.

Without the key the harvest degrades cleanly (`degraded: N8N_API_KEY is not set …`, no
`costs` rows) — it never fails a run.

## Importing an agent sub-workflow

1. n8n → *Workflows* → *Import from File* → pick `n8n/agents/<agent>.json`.
2. Re-select the OpenRouter credential on the Chat Model node(s) (see above).
3. Save. Sub-workflows start with an **Execute Workflow Trigger** — run them via
   *Execute workflow* with pinned/manual input, or from the orchestrator.

## Importing the orchestrator (`orchestrator.workflow.json`, Step 8)

The top-level workflow (§6.1). It needs **no credential** — it only talks to the quant
service over HTTP — but it calls the four agents **by workflow id**:

1. Import the four agent sub-workflows first and note each id (the editor URL is
   `/workflow/<id>`).
2. Import `n8n/orchestrator.workflow.json`. Its four **Execute Sub-workflow** nodes (Technical,
   Sentiment, Earnings, Risk Manager) carry the ids of the workflows in *this* n8n instance —
   if yours differ, re-pick the workflow in each node.
3. **Nothing to copy into config.** `/costs/harvest` resolves each agent's workflow id by
   **name** through the n8n API (§4.4), keyed on the `name` fields in this repo's `n8n/*.json`,
   so attribution works immediately after an import. Ids are minted per import and must never be
   committed — a tracked id 404s on every other machine and the harvest degrades silently. Set
   `N8N_WF_<AGENT>` in `.env` (gitignored) only if you renamed a workflow in n8n;
   `config/universe.yaml → n8n_workflow_ids` remains as a last-resort default and ships empty.
4. **Publish the sub-workflows too.** n8n refuses to publish a workflow whose referenced
   sub-workflows are unpublished (*"references workflow … which is not published"*), so publish
   the four agents before the orchestrator, and the orchestrator before the chat assistant.
5. Run it with **Execute workflow**. It fans out over `watchlist` from `config/universe.yaml`
   (35 names by default — trim it for a demo run; the list is read from the service at
   `/runs/start`, so no workflow edit is needed).

**Re-importing after Step 10:** if the orchestrator already exists in your instance, don't
create a second copy — open the existing workflow and re-import over it (or paste the new
nodes), so its id stays stable for anything that references it. Step 10 added a **Schedule
Trigger** and a **TASE Hours Gate** on a second entry path; the agent-id wiring (step 2 above)
is unchanged — and re-importing resets it, so re-pick the four nodes afterwards.

**Schedule Trigger (Step 10, §6.1/§11.2).** The scheduled path fires on `schedule_cron`
(`0 0 10-23 * * 0-5` — n8n's cron is 6-field, seconds first; hourly 10:00–23:00 Israel time,
Sun–Fri, spanning both the TASE and NYSE windows since Step 13). For it to fire at all: (a) set
`TZ=Asia/Jerusalem` in n8n's env so the cron reads as local time, and (b) toggle the workflow
**Active** — inactive workflows never run on schedule. The cron spans both markets' windows in
Israeli local time; the **per-market gate lives in the quant service** (`/runs/start`, §6.1), which
filters the watchlist to the markets currently in session. When none is, it returns
`skipped: true` with **no `runs` row written** and the workflow dead-ends at a No-Op. Manual and
chat triggers bypass the gate. To test the outside-hours branch without waiting for the weekend,
set `MARKET_GATE_FAKE_NOW` (ISO timestamp **with a UTC offset**) in the *quant service's* env — a
**dev-only** override that pins the gate's clock; leave it unset in production. (This replaces the
former n8n-side `TASE_GATE_FAKE_NOW`, which is no longer read.) See README §7.

### Technical Agent (`agents/technical.json`, Step 3)

Input: `{ "ticker": "TEVA.TA", "lookback_days": 180, "run_id": "r_test" }`.

Flow: `POST /ohlc` → `POST /indicators` → Gemini 2.5 Flash-Lite narration
(temperature 0) → `POST /validate` (Pydantic schema `schemas/technical.py`,
served by the quant service) → on invalid: one stricter retry → on second
failure: `status: "degraded"` output. Candle arrays never enter the prompt —
only indicator values and short summaries (§2 guardrail).

Output (§3.3 + §3.4 status):
`{ ticker, as_of, indicators: {rsi_14, macd{…}, bbands{…}, atr_14}, signal, summary, status }`.

Cost logging happens at the orchestrator level: `/costs/harvest` reads this agent's LLM token
usage out of n8n's execution API after the run (§9.4 — see *The n8n API key* above).

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
fails. Cost logging happens at the orchestrator level (`/costs/harvest`, §9.4).

### Earnings Agent (`agents/earnings.json`, Step 6)

Input: `{ "ticker": "TEVA.TA", "window_days": 5, "run_id": "r_test" }`.
The 5-day default window is often empty — widen it for a demo, e.g.
`"window_days": 30`. **Server prerequisite:** the quant service machine needs
the Playwright browser once: `python -m playwright install chromium`.

Flow (§3.2): `POST /earnings/fetch` (Maya EN page rendered server-side in
headless Chromium, HE fallback, §4.3 cleaning; returns compact items — the
top-ranked candidates each with a bounded text excerpt from the disclosure's PDF
attachment — **and** the few-shot examples from
`prompts/earnings_examples.jsonl`) → two LLM boundaries, both `x-ai/grok-4.3`:

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
`{ ticker, selected_disclosure{date,type,language,title,url,title_en,summary,extracted}, considered[{date,title,url,kind,materiality}], is_earnings_window, materiality, summary, status }`.

**The agent classifies the top 3 candidates, not just the newest filing.** A
ticker's newest disclosure is usually administrative (a Form 4, a trading
notice), so `/earnings/fetch` returns disclosures ranked by relevance and
excerpts the top `earnings_candidates` (`config/universe.yaml`, default 3).
*Build Classify Prompt* emits one item per candidate, the *Classify* chain runs
once per item, and *Pick Most Material* selects the winner from the model's own
`kind`/`materiality` — the server-side rank only chooses who gets asked, so a
mis-ranked candidate simply loses. Self-consistency then extracts figures from
the winner alone: 3 classify + 3 extract calls per ticker.
`selected_disclosure` is that winner; `considered` lists the classified-but-
rejected candidates as the audit trail for the choice.

Three terminal states: `ok` with a selected disclosure; `ok` with
`selected_disclosure: null` and `considered: []` when the scrape is healthy but
nothing matched the window (never padded); and `degraded` (with a reason) when
the scrape, an LLM boundary, or the `earnings` write fails. Cost logging happens
at the orchestrator level (`/costs/harvest`, §9.4).

### Risk Manager (`agents/risk_manager.json`, Step 7)

Runs **once per ticker** after the three analysis agents (not fanned out) and
consumes their outputs. Input (§6.2) — the three agent payloads as objects:

```json
{
  "ticker": "TEVA.TA",
  "run_id": "r_test",
  "sentiment":  { "llm_sentiment": 0.5, "model_sentiment": 0.45, "disagreement": 0.05, "summary": "Bullish tone.", "status": "ok" },
  "earnings":   { "is_earnings_window": false, "materiality": "low", "summary": "No recent disclosures.", "status": "ok" },
  "technical":  { "signal": "bearish_momentum", "summary": "Momentum fading.", "status": "ok" }
}
```

Flow (§3.4 three-stage critique loop): `POST /riskmanager/context` (loads the
three pass prompts from `prompts/risk_manager_*.md`, the rubric from
`config/rubric.yaml`, and the **deterministic** §3.4 facts — directions, strong
signals, agreement counts, applicable caps) → three sequential LLM passes, all
Claude Haiku 4.5 (temperature 0), each a validated boundary:

- **Draft** (`POST /validate` agent `"risk_draft"`) → the initial
  `{recommendation, conviction, rationale, earnings_direction}`.
- **Devil's-advocate critique** (agent `"risk_critique"`) → argues the opposite
  case: `{counter_recommendation, key_objections[], conviction_challenge}`.
- **Final** (agent `"risk_final"`) → the committed `{recommendation, conviction,
  rationale}`; the rationale addresses each objection and references any cap.

Each pass gets one stricter retry on a schema failure, then falls through to the
degraded output. The **three passes always run** (never collapsed to one call);
after the final pass, the **Apply Rubric Clamp** Code node deterministically
enforces the §3.4 rubric — the agreement-count ceiling (resolved with the draft's
`earnings_direction`), the `short`-needs-strong-bearish rule, the ≥2-degraded
`avoid` rule, and the earnings-event / dual-sentiment / degraded-agent conviction
caps — appending an explicit clamp note to the rationale rather than rewriting it
silently. Only compact panels and scores cross the LLM boundary — no OHLC arrays
or article bodies (§2 guardrail).

Output (§6.3 + §3.4 status): `{ ticker, draft, critique, final, sentiment{…},
earnings{…}, technical{…}, status }`.

Two terminal states: `ok` with all three passes populated and the clamped final;
and `degraded` (final = safe `hold`, completed passes kept, missing passes null,
`status: "degraded"`) when the context fetch fails or a pass fails validation
twice. The orchestrator persists this output to the `recommendations` table via
`POST /recommendations/store`, and logs the three passes' LLM cost via
`POST /costs/harvest` (§9.4).

**Local verification** (Windows): pin a contrived input where one agent
disagrees with the other two (e.g. the trio above — sentiment bullish, technical
bearish, earnings neutral) and run via *Execute workflow*. The critique should
name the disagreeing signal and the final rationale should reference it, with
conviction respecting the 2-of-3 → medium ceiling. Set two agents to
`"status": "degraded"` to confirm the final is forced to `avoid`.

**Local verification notes** (Windows): use `http://127.0.0.1:8000` (not
`localhost`, which n8n resolves to IPv6 first and refuses); if `{{ $env.… }}` is
blocked in your n8n build, switch the HTTP node URL field to Fixed and paste the
URL (this edits only n8n's DB copy, not the committed JSON). Start the quant
service with `NEWSAPI_API_KEY` set in its environment; without a key the NewsAPI
source degrades and the RSS feeds still return items.
