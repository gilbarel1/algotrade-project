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
An unpublished orchestrator does not error loudly: the chat assistant just replies that the backend
is unavailable, which looks like your system is broken on camera.

> **Publishing the orchestrator also arms `schedule_cron`** (top of every hour, 10:00–23:00). Harmless
> during a recording, but **deactivate it afterwards** or unattended scheduled runs will keep spending
> OpenRouter credit.

**2b · Check the earnings window, or shot 6 has nothing to show.** `earnings_window_days` in
`config/universe.yaml` must be wide enough to catch the last quarterly filing — it is `21`. At the old
value of `5`, **both** demo tickers returned *zero* disclosures despite each having filed ~18 days
earlier, so the Earnings Agent contributes nothing and the self-consistency beat silently disappears.
Verify before recording:

```bash
curl -s -X POST http://127.0.0.1:8000/earnings/fetch -H "Content-Type: application/json" -d "{\"ticker\":\"AAPL\"}"
```

Expect a non-empty `items` array. If it is empty, widen the window rather than discovering it in shot 6.

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
`ambiguous`" *(point at one)*. "The guarantee isn't a prompt asking it nicely — it's the mechanism,
and we measure it: absent figures came back `ambiguous` 21 to 22 times out of 22 across runs. It
samples, so it is not perfect — but unlike a single-shot extractor it can count its own misses."

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

**Say:** "Every agent is scored against hand-labeled data, per language. The Hebrew arm is our
weakest at 0.70 against English's 0.80 — and getting there meant measuring the first model we tried,
finding it returned neutral for ten items out of ten, and replacing it. We publish the weakness
rather than hiding it."

**Must be visible:** per-agent cost rows, and the eval table.

---

### Shot 9 — Limits and close · 4:20–4:40

**Say:** "The honest limits: it's local and educational, there's no execution and no backtest — that's
the biggest gap and the obvious next step. Coverage of smaller Tel Aviv names is thin, Hebrew
sentiment is a general model, and scraping is best-effort. But every failure degrades to `ambiguous`
or `degraded` — never to a fabricated number. That was the design goal."

**If you have ten seconds left, land this — it is the strongest closing claim you have:**
"And these techniques aren't decoration — we measured them. Turn off the few-shot examples and
sentiment drops to exactly what the free local model scores. Turn off self-consistency and invented
figures appear. The critique loop has never once made the system more confident."

> Why this closes well: the rest of the demo *shows* the system working. This is the only line that
> proves the design choices were **necessary** rather than merely present — which is the difference
> between a working project and a considered one.

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
| The techniques are load-bearing | `npm run ablations`; [`ablations.md`](ablations.md) — each one switched off and re-scored |
| Disagreement as the uncertainty signal | README "One idea, applied at three scales" — sample vs. sample, model vs. model, pass vs. pass |
| Tests and CI | `npm run test` (148 offline tests); `.github/workflows/ci.yml` |
| Data integrity over convenience | `quant_service/data/yahoo.py` (grid from the source); `tests/test_ohlc_calendar.py` |
| Limitations | [design §13](design.md); the summary PDF §6 |

**Likely questions:**

- *"Why not one prompt?"* — It would invent figures and hide uncertainty. Both are demonstrable:
  the self-consistency vote and the dual-sentiment split exist to prevent exactly that.
- *"Is there a single design idea behind all of this?"* — Yes, and it's the best answer to give:
  **an LLM can't be trusted to report its own uncertainty, so uncertainty is derived from
  disagreement between independent attempts.** Three scales — samples disagreeing (→ `ambiguous`),
  models disagreeing (→ conviction capped), passes disagreeing (→ call downgraded). The system never
  asks a model "how confident are you?", which is exactly the question that got HeBERT a 0.998
  confidence on a wrong answer.
- *"How do you know it isn't hallucinating numbers?"* — The extractor is measured: 22/22 absent
  figures marked `ambiguous`. It's a mechanism, not an instruction.
- *"How do you know the fancy techniques are doing anything, and not just costing tokens?"* — The
  strongest card in the deck: `npm run ablations` switches each one off and re-scores
  ([`ablations.md`](ablations.md)). Drop the few-shot examples and sentiment falls **90% → 77%**,
  which is exactly what the *free* local transformer scores — so the examples are the entire margin
  the paid LLM contributes. Drop self-consistency and invented figures go **0 → 2**, and the reason is
  the mechanism itself: two of three individual samples each invented a figure, and the vote caught
  both *because the samples disagreed with each other*. The critique loop has moved 8 calls across
  every run on record, all `long → hold`, with **9 conviction downgrades and 0 upgrades** — it has
  never once made the system more confident. Be ready to concede the honest limit too: "changed the
  call" is not "improved the call", and the samples are small.
- *"What would you do next?"* — Backtest the recommendation stream against realized returns. Right
  now we measure reasoning quality, not alpha.
- *"Did anything surprise you?"* — The best answer in the project, if asked. Yahoo returns `.TA` bars
  on a **Mon–Fri** index even though Tel Aviv trades Sun–Thu. Reindexing onto the configured week
  therefore threw away every real Friday and forward-filled a synthetic Sunday — **19% of every TASE
  series**, each phantom carrying a zero return and near-zero true range, quietly deflating ATR and
  flattening RSI. Nothing failed; the numbers were just wrong. The grid now comes from the data, so
  the assumption is gone rather than corrected, and two invariants are pinned: never drop a bar the
  source sent, never invent one it didn't. The cache had to change too — insert-or-replace could not
  *retract* the phantoms, so they outlived the fix until a re-ingest was made authoritative for its
  window. Worth saying plainly: what those Friday bars really are is still unresolved, and it is
  recorded as an open question rather than guessed at.
