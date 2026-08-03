# Acceptance checklist — proving the system works

Three layers, cheapest first. The first two cost nothing and take under two minutes; only
the third spends OpenRouter credit.

Tick these before recording the demo or submitting — a claim in the README that you have
not seen with your own eyes is a claim a grader can break.

---

## Layer 1 · Automatic, offline, free

No service, no keys, no network.

```bash
npm run test
```

**Pass:** `112 passed`. Proves the *decision logic* — market gate and DST handling, the §3.4
rubric clamp (executed as the workflow's own JavaScript), every Pydantic boundary, cost
arithmetic, the workflow-id ladder, degraded-message wording.

```bash
npm run lint
```

**Pass:** `All checks passed!`

> Both also run in CI on every push — the badge at the top of the README is this layer.

---

## Layer 2 · Automatic, live service, free

Needs `npm run dev` running. Costs nothing: no LLM calls, only Yahoo, local transformers,
Maya/EDGAR and the PDF renderer.

```bash
npm run smoke
```

**Pass:** `All endpoints OK.` This exercises every §5 endpoint with real data — OHLC and
indicators from Yahoo, FinBERT/DictaBERT scoring, news and earnings fetching, the deterministic
rubric facts, `/validate` accepting valid payloads *and rejecting malformed ones*, PDF
rendering, and the orchestration writes to `runs` / `recommendations` / `costs`.

> The first run is slow (tens of seconds): it downloads FinBERT + DictaBERT into `HF_HOME`.
> Later runs reuse the cache.

**Per-market schedule gate** (§6.1) — the one piece the smoke test cannot reach, because it
depends on the clock. Pin it with the dev-only override:

```bash
curl -s -X POST http://127.0.0.1:8000/runs/start -H "Content-Type: application/json" -d "{\"mode\":\"scheduled\"}"
```

Run it three times with `MARKET_GATE_FAKE_NOW` set in the service's environment:

| Fake now | Expected |
| --- | --- |
| `2026-07-30T08:00:00+00:00` (11:00 Israel, Thu) | watchlist filtered to **`.TA` names only** |
| `2026-07-30T17:00:00+00:00` (20:00 Israel, Thu) | filtered to **US names only** |
| `2026-08-01T12:00:00+00:00` (Saturday) | `"skipped": true`, `run_id: null`, **no `runs` row written** |

---

## Layer 3 · Live pipeline, costs real money

Roughly **$0.02–0.04 per ticker**, 1–2 minutes each. Do these once, deliberately.

### 3a · The full team on one ticker

Open <http://localhost:8001>, ask **"what do you think about Teva?"**

**Pass:**
- a recommendation + conviction + rationale comes back within ~2 minutes
- a PDF link appears and opens
- `npm run costs` shows a new run with per-agent rows (technical, earnings, risk_manager)
- the PDF's reasoning trace shows **all three** Risk Manager passes

### 3b · The refusal guarantee

Same chat, ask **"what's your price target for Teva?"**

**Pass:** it declines rather than inventing a number. ❗ If it ever answers with a figure the
pipeline did not produce, that is a real bug — the router-not-analyst rule (§6.5) is broken.

### 3c · Mixed watchlist — the claim the summary document makes

Set `watchlist: ["TEVA.TA", "AAPL"]` in `config/universe.yaml`, then run the orchestrator
manually from the n8n editor.

**Pass:** **one** PDF containing both tickers, each showing its own market and currency
(`TASE · ILS`, `US · USD`), and an executive summary grouped by market.

### 3d · Evaluation harness

```bash
npm run eval
```

**Pass:** the one-page summary prints metrics for every agent. Paste the output into the
README if the numbers have moved. `npm run eval -- --no-llm` runs the free transformer arm
only.

---

## Known gaps that are *not* bugs

These are configuration, not defects — but they change what a demo shows:

| Gap | Effect | Fix |
| --- | --- | --- |
| `NEWSAPI_API_KEY` is a placeholder | Sentiment degrades every run: zero articles, no dual-score split. Conviction is capped and the call cannot be directional. | A free key from newsapi.org |
| `EDGAR_USER_AGENT` is a placeholder | US earnings degrade rather than fetching; SEC requires a declared contact. | `your-name your-email@example.com` in `.env` |

With both placeholders in place the system still runs and still produces a report — it just
demonstrates *honest degradation* rather than the full evidence path. That is a legitimate
story (see [`demo_script.md`](demo_script.md), Variant B), but it is a choice worth making
knowingly rather than by accident.
