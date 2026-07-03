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
