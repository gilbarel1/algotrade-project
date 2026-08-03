# Demo shot list — 4:40 target (assignment allows 3–5 min)

Nine shots. Each one names the window, the action, the words, and what must be visible for the
shot to count. Everything else is cut.

The demo has to land four things and nothing else: **dual sentiment**, **self-consistency
earnings**, **the three-pass Risk Manager**, and **honest degradation**. If a shot doesn't serve
one of those, it's filler.

---

## Before you hit record

**1 · Decide which variant you're recording.** This changes shots 5 and 7.

| | Variant A — live keys | Variant B — no NewsAPI key |
| --- | --- | --- |
| Requires | a real `NEWSAPI_API_KEY` in `.env` | nothing |
| Sentiment panel shows | two scores + a disagreement value | `—` and `degraded` |
| Your line in shot 5 | "scored twice, and they disagree" | "the source failed, so it reports no signal — and that lowers conviction rather than becoming one" |
| Risk of the day | none | you cannot demo the dual-sentiment differentiator live |

> **Variant B is a real demo, not a broken one** — honest degradation is one of the four things
> you're selling. But if you can get a free NewsAPI key beforehand, Variant A shows one more
> differentiator working. Decide before recording; don't discover it mid-take.

**2 · Start everything and verify.**

```bash
npm run doctor
```

```bash
npm run dev
```

Then confirm all three respond before recording:

```bash
curl -s http://127.0.0.1:8000/health && curl -s http://127.0.0.1:8001/health && curl -s http://127.0.0.1:5678/healthz
```

The front end must report `"webhook_configured": true`. All six n8n workflows must be **published**
(the four agents first, then the orchestrator, then the chat assistant — n8n refuses otherwise).

**3 · Pre-warm.** Run one throwaway chat query before recording. The first `/sentiment` call loads
FinBERT + DictaBERT (tens of seconds) and the first Maya scrape is uncached — you do not want either
on camera.

**4 · Have a fallback report open in a background tab**, from `docs/results.md` or a recent
`reports/YYYY-MM-DD/HHMM/report.pdf`. If the live run stalls, cut to it and keep talking.

**5 · Recording hygiene — check each one:**

- [ ] Close any terminal, editor tab, or n8n credential screen that shows a key. **Never** open
      `.env` or an n8n credential on camera.
- [ ] Browser zoom ~125% so text is legible after compression.
- [ ] One window per shot — no desktop, no notifications, no chat apps.
- [ ] Test your mic. Bad audio sinks a good demo faster than a slow run does.

---

## The shots

### Shot 1 — What it is · 0:00–0:25 · `docs/architecture.svg`

**Say:** "A virtual investment team. Four AI agents — sentiment, earnings, technical — each analyze
a stock independently, and a risk manager synthesizes them into a buy, sell or hold call with a
written justification. The watchlist mixes Tel Aviv and US names. It analyzes; it doesn't trade."

**Point at:** the two layers. "n8n orchestrates and makes every LLM call. A local Python service
does everything needing a real library — indicators, transformers, PDF. No ML runs inside n8n."

**Must be visible:** the two-layer split.

---

### Shot 2 — Start a live run · 0:25–0:55 · `http://localhost:8001`

**Do:** type **"what do you think about Netflix?"** and hit send. Leave it running — it fills the
next two shots.

**Say while it spins:** "This is a router, not an analyst. It resolves 'Netflix' to NFLX, sees a US
ticker, and calls the same pipeline a scheduled run uses. It has no analytical authority — ask it
for a price target and it refuses, because the number would have to come from somewhere."

**Must be visible:** your question, and the pending state.

> ⏱ The run takes 60–120s. Shots 3 and 4 are timed to cover it. Do not sit and watch it.

---

### Shot 3 — The orchestration · 0:55–1:35 · n8n editor, Orchestrator workflow

**Point at, in order:** the trigger → the **per-ticker fan-out** (three agents in parallel) → the
**Risk Manager** downstream of all three.

**Say:** "Three specialists run in parallel per ticker. The risk manager only runs once they've all
reported — it never sees raw data, only their conclusions."

**Then open the Earnings sub-workflow** and point at **Merge Classifications** → **Pick Most
Material**.

**Say:** "This is where 'never invent numbers' is enforced structurally. Every candidate disclosure's
validation outcome is reunited before selection — if it weren't, selection would run per-branch and
silently pick from a subset. That's a real bug this design prevents."

**Must be visible:** the parallel fan-out, and the merge node by name.

---

### Shot 4 — The answer · 1:35–1:55 · back to `http://localhost:8001`

**Do:** read the reply off the screen. **Do not read numbers from this script** — say what's
actually there.

**Say:** "Recommendation, conviction, and the reasoning — and a link to the full PDF, which is what
we'll look at now."

**Must be visible:** the call and the conviction.

> **Fallback:** if it's still running, say "this takes about a minute end to end — here's one I ran
> earlier" and switch to the fallback report. Do not apologize; keep the pace.

---

### Shot 5 — Dual sentiment · 1:55–2:25 · report, per-ticker page

**Point at:** the **Sentiment** panel.

**Variant A — say:** "Every headline is scored twice: once by an LLM, once by a fine-tuned
transformer — FinBERT for English, DictaBERT for Hebrew. Here they disagree" *(read the two scores and
the disagreement value off the screen)*. "We don't average that away. Above a threshold it caps the
risk manager's conviction, and the report shows the split."

**Variant B — say:** "Sentiment is degraded here — the news API rejected our key, so there were zero
articles. Notice what it does *not* do: it doesn't treat 'no news' as bad news. A failed source is
absence of information, so it lowers conviction and contributes nothing directional. That distinction
is enforced in the prompts and in the rubric."

**Must be visible:** the sentiment panel, either the two scores or the degraded state.

---

### Shot 6 — Earnings are never invented · 2:25–2:50 · same page, Earnings panel

**Point at:** the extracted figures and their `confidence` markers.

**Say:** "Figures are extracted by sampling the model three times and committing a number only when
at least two samples agree on text that appears **verbatim** in the filing. Anything else prints as
`ambiguous`" *(point at one)*. "The guarantee isn't a prompt asking it nicely — it's the mechanism.
Our evaluation measures it: 22 out of 22 figures absent from the source came back `ambiguous`."

**Must be visible:** at least one `ambiguous` figure, styled distinctly, and a committed figure if
one exists.

---

### Shot 7 — The three-pass critique · 2:50–4:00 · report, Reasoning Trace ⭐

> **This is the shot the grade turns on. Give it the full 70 seconds; cut elsewhere if you must.**

**Walk the three passes on screen, reading the real text:**

1. **Draft** — the call and conviction.
2. **Devil's-advocate critique** — "a second pass whose only job is to attack the draft. Not a
   rubber stamp — it argues the opposite case using the same evidence."
3. **Final** — "and the final answers each objection explicitly, saying what it incorporated and
   what it dismissed."

**Say:** "This is the differentiator. A single-prompt pipeline emits one confident answer. Here the
reasoning stress-tests itself, and all three passes are printed — so the recommendation is auditable,
not a black box."

**If the trace shows a rubric clamp**, point at it: "and where the model broke a decision rule, a
deterministic clamp overrode it and said so in the report rather than silently rewriting."

**Must be visible:** all three passes, and at least one objection the final answers.

---

### Shot 8 — Cost and evaluation · 4:00–4:20 · terminal

```bash
npm run costs
```

**Say:** "Every LLM call is logged with real token usage — per run, per agent, per model. A
five-ticker run is four to eight cents."

**Then show the evaluation table** (README or `docs/results.md`).

**Say:** "Every agent is scored against hand-labeled data — including that the Hebrew arm is our weakest
at 0.63 against the LLM's 0.90. We measure the weakness instead of hiding it."

**Must be visible:** per-agent cost rows, and the eval table.

---

### Shot 9 — Limits and close · 4:20–4:40

**Say:** "The honest limits: it's local and educational, there's no execution and no backtest — that's
the biggest gap and the obvious next step. Coverage of smaller Tel Aviv names is thin, Hebrew
sentiment is a general model, and scraping is best-effort. But every failure degrades to `ambiguous`
or `degraded` — never to a fabricated number. That was the design goal."

**End on:** the report's methodology footer or the architecture diagram. Don't trail off — stop
talking on a full stop.

---

## If something breaks mid-take

| Symptom | Do this |
| --- | --- |
| Chat run stalls past ~2 min | "Here's one I prepared earlier" → fallback report. Keep talking. |
| n8n editor slow to load | Skip shot 3; spend the time on shot 7 instead. |
| Report shows everything `degraded` | Lean into it — that's Variant B, and it's a legitimate story. |
| You fluff a line | Keep going. One clean take beats four stitched ones. |

---

## Q&A cheat-sheet — where each claim lives

| Claim | Point at |
| --- | --- |
| Two-layer architecture | [`architecture.svg`](architecture.svg); README "How it works" |
| Router, not analyst | `prompts/chat_assistant_system.md`; `n8n/chat_assistant.workflow.json` |
| Parallel fan-out + critique loop | `n8n/orchestrator.workflow.json` |
| Self-consistency / merge-before-select | `n8n/agents/earnings.json` → *Merge Classifications* |
| Dual sentiment | report ticker page; `quant_service/nlp/finbert.py`, `hebert.py` |
| Rubric, conviction caps, the clamp | `config/rubric.yaml`; `n8n/agents/risk_manager.json` → *Apply Rubric Clamp* |
| A degraded agent is not a signal | `prompts/risk_manager_*.md`; design §3.4 |
| Cost logging | `npm run costs`; `costs` table; `quant_service/ops/cost_log.py` |
| Evaluation | `npm run eval`; `eval/*_labeled.jsonl`; [`results.md`](results.md) |
| Tests and CI | `npm run test` (112 offline tests); `.github/workflows/ci.yml` |
| Limitations | [design §13](design.md); the summary PDF §6 |

**Likely questions:**

- *"Why not one prompt?"* — It would invent figures and hide uncertainty. Both are demonstrable:
  the self-consistency vote and the dual-sentiment split exist to prevent exactly that.
- *"How do you know it isn't hallucinating numbers?"* — The extractor is measured: 22/22 absent
  figures marked `ambiguous`. It's a mechanism, not an instruction.
- *"What would you do next?"* — Backtest the recommendation stream against realized returns. Right
  now we measure reasoning quality, not alpha.
