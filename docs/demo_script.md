# Demo script — 5-minute walk-through

A tight, defensible tour of the system. Timings are guidance; the point is to hit the four things
that make this more than a single-prompt LLM: **dual sentiment**, **self-consistency earnings**,
**the three-pass Risk Manager**, and **honest degradation**.

**Before you start:** `npm run dev` running (svc :8000, chat :8001, n8n :5678), the orchestrator
**imported and published**, and a recent report open at `reports/YYYY-MM-DD/HHMM/report.pdf`. Keep
[`results.md`](results.md) open as a fallback if a live run is slow.

---

### 0:00 — What it is (30s)

> "It's a virtual investment team. Four agents — sentiment, earnings, technical, and a risk manager
> — each analyze a stock independently; a coordinator synthesizes them, runs a critique pass, and
> writes a PDF recommendation. The watchlist mixes Tel Aviv and US names in one list. Everything is
> local; it analyzes, it doesn't trade."

Show the README hero image or the architecture diagram
([`architecture.svg`](architecture.svg)). One sentence on the split: **n8n orchestrates and makes
the LLM calls; a local FastAPI service does everything that needs a real ML/PDF library.**

### 0:30 — Kick off a live run via chat (30s)

Open <http://localhost:8001>, type **"what do you think about Netflix?"**. While it runs (~40–80s),
narrate: *"the chat agent is a router — it resolves 'Netflix' to NFLX, sees a US ticker, and calls
the exact same pipeline a scheduled run uses. It has no analytical authority of its own."*

> If a live run is risky on the day, skip the wait and walk the pre-rendered run in
> [`results.md`](results.md) instead — same talking points.

### 1:00 — The n8n canvas (45s)

Open the **Orchestrator** in the n8n editor. Point at the **fan-out** (three agents in parallel per
ticker), then the **Risk Manager** downstream. Open the **Earnings** sub-workflow and show the
**Merge Classifications** node — *"self-consistency needs every candidate's validation outcome
reunited before selection; this is where the 'never invent numbers' rule is enforced structurally,
not by a prompt."*

### 1:45 — The report: dual sentiment (45s)

Open the report's per-ticker page (screenshot: `screenshots/report_ticker_page.png`). Point at the
**Sentiment** panel: **LLM −0.23 vs. model −0.51, disagreement 0.37 ⚠ split**.

> "Every headline is scored twice — an LLM and a fine-tuned transformer, FinBERT for English,
> HeBERT for Hebrew. When they disagree we show the split. It isn't noise; it caps the risk
> manager's conviction later."

### 2:30 — The report: earnings never invented (30s)

Same page, **Earnings** panel. The disclosure is classified high-materiality, but revenue / EPS /
guidance are all **`ambiguous`**, styled distinctly.

> "The figures didn't survive self-consistency — three samples didn't agree on a verbatim number —
> so the report says `ambiguous` rather than guessing. That's the guarantee: it never fabricates a
> financial figure."

### 3:00 — The headline: three-pass critique (75s)

Open the **Reasoning Trace** (screenshot: `screenshots/report_reasoning_trace.png`). Walk the three
passes:

1. **Draft:** SHORT, medium.
2. **Devil's-advocate critique:** counters with HOLD — the oversold technical contradicts the
   bearish thesis, the 0.37 sentiment split is unresolved, earnings rests on guidance not results.
3. **Final:** SHORT but conviction **downgraded to low**, explicitly citing the disagreement.

> "This is the differentiator. A naive pipeline ships 'SHORT, medium.' The critique caught a real
> contradiction, and the final call reflects genuine uncertainty. All three passes are printed —
> the reasoning is auditable."

### 4:15 — Cost and evaluation (30s)

Run `npm run costs` — one run, per-agent, priced from token usage; **~$0.04–$0.08** for a
five-ticker run. Then show the evaluation summary (in [`results.md`](results.md) / README): accuracy,
MAE, agreement correlation, earnings precision/recall.

> "Every LLM call is cost-logged, and every agent is scored against hand-labeled data — including
> that HeBERT is the weakest arm. We measure the limitations instead of hiding them."

### 4:45 — Limitations, close (15s)

> "It's local and educational — no execution, no backtest yet, which is the biggest honest gap.
> News on smaller Tel Aviv names is thin, Hebrew sentiment is general-purpose, and scraping is
> best-effort — but every failure degrades to `ambiguous` or `degraded`, never to a fabricated
> number."

---

## Citations cheat-sheet

| Claim | Where to point |
|---|---|
| Two-layer architecture | [`architecture.svg`](architecture.svg), README "How it works" |
| Router, not analyst | `prompts/chat_assistant_system.md`, `n8n/chat_assistant.workflow.json` |
| Parallel fan-out + critique | `n8n/orchestrator.workflow.json` |
| Self-consistency / Merge Classifications | `n8n/agents/earnings.json` |
| Dual sentiment | report ticker page; `quant_service/nlp/finbert.py`, `hebert.py` |
| Rubric & conviction caps | `config/rubric.yaml` |
| Cost logging | `npm run costs`; `costs` table; `quant_service/ops/cost_log.py` |
| Evaluation | `npm run eval`; `eval/*_labeled.jsonl`; [`results.md`](results.md) |
| Limitations | [`design.md` §13](design.md) |
