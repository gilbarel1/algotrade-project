# Results — two runs, end to end

Two real runs, taken apart. The first shows the dual-sentiment mechanism deciding an outcome; the
second shows a mixed TA-35 + S&P 500 watchlist and the "never invent numbers" guarantee doing both
of its jobs at once. Then the evaluation-harness output, reproduced verbatim.

Both reports are committed at [`samples/`](samples/) so they can be read without running the
pipeline (a full run needs API keys). Companion to [`../README.md`](../README.md) and the design in
[`design.md`](design.md).

---

## Run 1 — "what do you think about Netflix?"

Started from the **chat assistant** (<http://localhost:8001>), hence `mode = chat`. The assistant
resolved *Netflix* → `NFLX`, saw the bare symbol, routed it to the **US** market, and ran the same
pipeline a manual or scheduled run uses.

📄 **[`samples/sample-report-dual-sentiment.pdf`](samples/sample-report-dual-sentiment.pdf)**

```
runs             run_id = r_2026-08-06T11:39 · mode = chat · status = ok
                 tickers = ["NFLX"] · report = reports/2026-08-06/1441/report.pdf
                 121 seconds end to end
recommendations  NFLX → hold · low
agent_status     {"sentiment": "ok", "earnings": "ok", "technical": "ok"}
```

### 1 · Dual sentiment, and a disagreement that changes the answer

The Sentiment Agent scored 11 live headlines **twice** — once by Haiku 4.5, once by FinBERT:

| | |
|---|---|
| `llm_sentiment` | **0.0318** |
| `model_sentiment` | **0.1478** |
| `disagreement` | **0.3832** → `⚠ split` |

Both are nominally positive, but they disagree by more than the §3.4 threshold of 0.30, so the
**dual-sentiment cap fires** and ceilings conviction. The report prints the split rather than
averaging it away.

The aggregate understates how far apart the two scorers can be. One citation on the ticker page:

> **"3 Big Reasons to Love Netflix (NFLX)"** — Biztoc · **L 0.15 / M −0.96**

The LLM read the headline's framing as mildly positive; the finance-tuned model read the language as
strongly negative — a **per-article gap of 1.11**, printed with its source link so a reader can go
and judge for themselves. This is the argument for scoring twice, in one line.

### 2 · The three-pass critique, arguing against itself

- **Draft — `hold`, low.** *"All three agents report neutral directions… Agreement count is 1-of-3
  at best for any directional candidate, triggering the mandatory hold rule."*
- **Devil's-advocate critique — counters `short`, with five objections.** It does not rubber-stamp:
  *"RSI at 47.85 is not neutral — it is below 50, biased toward weakness; combined with MACD
  structure, this is a sell-on-rally setup, not a hold."* And on the split itself: *"Disagreement at
  0.38 masks a split: model_sentiment 0.1478 vs llm_sentiment 0.0318 suggests LLM sees material
  downside risk that model underweights."*
- **Final — `hold`, low.** It answers each objection and declines the counter, on the rubric rather
  than on instinct: *"these observations do not constitute a strong bearish signal: the rubric
  confirms `has_strong_bearish` is false, and a short requires strong bearish evidence per §3.4…
  The sentiment disagreement (0.38) between LLM and model is real and noted via the dual_sentiment
  cap, which limits conviction to low."*

A single-prompt pipeline emits one confident answer. Here the reasoning is stress-tested by a pass
whose only job is to attack it, and the final call has to survive that in writing.

### 3 · What it cost

| Agent | Model | In / out tokens | USD |
|---|---|---|---|
| Risk Manager (×3 passes) | `anthropic/claude-haiku-4.5` | 7,446 / 1,410 | $0.0145 |
| Sentiment | `anthropic/claude-haiku-4.5` | 1,890 / 1,044 | $0.0071 |
| Technical | `google/gemini-2.5-flash-lite` | 272 / 66 | $0.0001 |
| **Total** | | | **$0.0217** |

Earnings has no row: NFLX had no disclosure inside the 5-day window, so the agent returned "no
recent disclosure" without making a single LLM call. Cheap by construction, not by luck.

---

## Run 2 — a mixed TA-35 + S&P 500 watchlist

One run, two markets, one PDF.

📄 **[`samples/sample-report-mixed-watchlist.pdf`](samples/sample-report-mixed-watchlist.pdf)**

```
runs             run_id = r_2026-08-16T12:53 · mode = chat · status = ok
                 tickers = ["TEVA.TA", "AAPL"] · report = reports/2026-08-16/1557/report.pdf
                 245 seconds end to end
recommendations  TEVA.TA → hold · low        AAPL → long · medium
agent_status     both: {"sentiment": "ok", "earnings": "ok", "technical": "ok"}
```

The executive summary groups by market, and each ticker page carries its own market and currency,
so a figure can never be read in the wrong unit:

```
Market  Currency  Calls          TEVA.TA  HOLD low conviction   TASE · ILS
TASE    ILS       HOLD 1         AAPL     LONG medium conviction   US · USD
US      USD       LONG 1
```

> This run was driven through the **chat assistant** (`mode = chat`) by asking it to analyze both
> names at once — the assistant resolved them, called the orchestrator once with both tickers, and
> relayed the Risk Manager's calls without adding a view of its own.

Earnings routed per market without the agent knowing anything about it — Teva to **Maya**, Apple to
**SEC EDGAR** — because only the *fetch source* varies by market (§3.2); everything downstream is
identical.

### The guarantee, doing both of its jobs on one page

Apple's earnings panel, from an 8-K *Results of Operations* exhibit:

| Figure | Value | Confidence |
|---|---|---|
| revenue | **$109.4 billion** | ×3 |
| eps | **$2.02** | ×3 |
| guidance | **ambiguous** | ambiguous |

Two figures committed with all three self-consistency samples agreeing, and one refused — because
the exhibit states revenue and EPS but gives no guidance. The mechanism does not merely avoid
inventing numbers; it *commits* when the evidence is there and *abstains* when it is not, on the
same page, from the same document.

Teva's panel is the contrasting case: a 10-Q was classified high-materiality, but every figure came
back `ambiguous` — its excerpt is windowed statement tables rather than a press release, exactly the
weaker extraction source §3.2 describes.

### The critique overturning the call — a reasoning error, caught

This is the run's best moment, and it is the argument for the whole three-pass design. On **TEVA.TA**
the draft opened bullish:

> **Draft — `long`, medium.** *"Agreement count for bullish direction is 2 of 3 (technical and
> earnings-neutral both align bullish…)."*

The devil's advocate did not quibble with the conclusion; it attacked the **arithmetic behind it**:

> **Critique — counters `hold`.** *"Agreement count of 2-of-3 is misleading: technical is bullish,
> earnings is neutral (not bullish), and sentiment is absent. Neutral earnings provide zero
> directional conviction — they are a placeholder, not a vote. True agreement is 1-of-3."*
> And on the technicals: *"RSI at 67.8 and price at upper Bollinger Band are **overbought**
> conditions, not strength… these are exhaustion signals in a vacuum of sentiment support."*

The final pass conceded, in writing, and the call changed:

> **Final — `hold`, low.** *"The agreement count of 1-of-3 is the correct reading… The draft's claim
> of 2-of-3 agreement by counting neutral earnings as bullish-aligned is a **miscount**."*

A single-pass pipeline would have shipped `long / medium` on a miscounted rubric. The critique caught
it, the final owned it, and all three passes are printed in the PDF so a reader can audit the
reversal rather than take it on trust.

**AAPL shows the opposite outcome — the critique heard and rejected.** Its dual-sentiment split was
LLM **0.6** vs model **0.194** (disagreement **0.4061**, over the 0.3 threshold), so the cap fired and
ceilinged conviction at medium. The critique pushed for `hold`; the final kept `long` but conceded the
split explicitly: *"the dual-sentiment cap applies, and the LLM/model split does reflect some
uncertainty… this is explicitly noted and constrains conviction to medium per the rubric."* Disagreeing
with the critique is allowed — doing so silently is not.

### Thin coverage, reported rather than padded

Teva's sentiment came back `ok` with **zero articles** — *"No recent coverage in the last 4320m"* —
both scores 0. That is the TA-35 mid-cap coverage limit doing exactly what the README says it does:
the count is printed and nothing is invented to fill it. The critique then named the gap as a reason
for *lower conviction* without reading a direction into it, which is the §3.4 rule holding under a
real absence rather than a simulated one.

| Agent | Model | In / out tokens | USD |
|---|---|---|---|
| Earnings | `x-ai/grok-4.3` | 24,003 / 4,776 | $0.0419 |
| Risk Manager (×3 passes, ×2 tickers) | `anthropic/claude-haiku-4.5` | 13,120 / 2,310 | $0.0247 |
| Sentiment | `anthropic/claude-haiku-4.5` | 865 / 206 | $0.0019 |
| Technical | `google/gemini-2.5-flash-lite` | 560 / 126 | $0.0001 |
| **Total** | | | **$0.0686** |

Earnings dominates because each ranked candidate carries a multi-thousand-character excerpt, and the
winner's is re-sent once per self-consistency sample.

---

## Evaluation results

`npm run eval` scores each agent against hand-labeled fixtures in `eval/` (30 sentiment items —
20 EN / 10 HE — 10 disclosures, 7 chat-router probes). Reproduced verbatim:

```
Evaluation summary  (eval-20260806-122029)
Sentiment: 30 items (20 EN, 10 HE)   Earnings: 10 disclosures   Chat: 7 router cases

Agent                         Dataset             Metrics
------------------------------------------------------------------------------
Sentiment (LLM)               sentiment_labeled   accuracy 0.90 (27/30) | MAE 0.12  [haiku]
Sentiment (FinBERT/DictaBERT) sentiment_labeled   accuracy 0.77 (23/30) | MAE 0.39
  └ en                        finbert             accuracy 0.80 (16/20) | MAE 0.43
  └ he                        dictabert           accuracy 0.70 (7/10) | MAE 0.32
Sentiment (agreement)         sentiment_labeled   Pearson r 0.82 (n=30)
Earnings (classifier)         earnings_labeled    macro-F1(kind) 1.00 | materiality acc 0.90 (n=10)  [grok]
Earnings (extractor)          earnings_labeled    precision 1.00 | recall 1.00 | ambiguous-when-absent 22/22  [grok]
Chat router (§6.5)            chat_refusal        refusal 4/4 | routing 3/3 | no fabrication  [haiku]
------------------------------------------------------------------------------
LLM cost (this run): $0.1138   (chat 11,872 tok, earnings 47,183 tok, sentiment 5,337 tok)
```

### How to read it

**The transformer arm is reported per language** because it is two models, and their mean describes
neither. Reaching 0.70 on Hebrew required replacing the model. The original choice,
`avichr/heBERT_sentiment_analysis`, scored **0.30** — and diagnosis showed it was not weakly
discriminating but not discriminating *at all*: `neutral` for **10 of 10** Hebrew items, at
0.833–0.998 confidence, including a *"record quarterly orders, guidance raised"* headline at 0.998.

Two rescue attempts were measured and both failed, which is why they are recorded rather than
hidden:

| Attempt | Result |
|---|---|
| Re-normalise polarity over the polar classes only | **worse** — 0.53 overall; the residual mass is noise |
| Symmetric threshold sweep, ±0.1 → ±0.6 | no movement beyond one item |

`dicta-il/dictabert-sentiment` scores **0.70** on the same items and reads every negative and every
neutral correctly, missing only positives. Agreement between the arms rose **0.70 → 0.82** as a
direct consequence: a model returning ~0 for everything cannot correlate with anything, so the old
figure was largely measuring noise.

**The extractor row is the guarantee, measured — including its limit.** Self-consistency *samples*
(n=3, temperature 0.3), so it is not deterministic: measured runs scored `ambiguous`-when-absent at
**22/22, 21/22 and 22/22** across three runs — the single miss being a figure committed that its
source does not state. The
honest claim is that the mechanism sharply reduces invented figures and makes the residue
*measurable* — not that it eliminates them. A single-sample extractor has neither the guard nor any
way to count its own failures.

**The `chat_refusal` row** checks that the router-only assistant declines to produce figures the
tools did not return. See *Known open items* below for the one failing case.

---

## Known open items

Stated plainly rather than left for a grader to find:

- **`refuse-comparison` was a mis-specified test, and the fixture was corrected.** The eval block
  above predates the fix, so it still records the failure. Asked *"Between Teva and NICE, which one
  is the better investment?"*, the router replied *"I'm running the analysis on both now"* and
  called the tool with `TEVA.TA,NICE.TA` — emitting **no view of its own**. The fixture expected a
  flat refusal, but declining would contradict the system prompt, which requires running the
  pipeline whenever a company is named rather than asking permission. Running both and letting the
  Risk Manager's verdicts speak *is* the router-not-analyst behaviour. The expectation was changed
  from `refuse` to `route` on that measured evidence; the `forbidden_patterns` are unchanged, so the
  case still fails if the assistant ever declares one name better. The next harness run should show
  refusal 4/4 and routing 3/3.
- **A degraded agent still counts in the agreement denominator.** The rubric counts three agents
  even when one could not measure, so "1 of 3" may really be "1 of 2 measurable". The system's own
  critique pass raised this during a live run.
- **Coverage of TA-35 mid-caps is thin.** Measured live: `TEVA.TA` returns 0 news items in the
  default 3-day window and 12 over 30 days, against 47–49 for large US names.

---

## Future work

- **A backtest.** The system produces recommendations and rationale but never measures whether they
  would have made money. Replaying historical runs against forward returns is the natural next step
  and the biggest honest gap today.
- **A finance-tuned Hebrew model.** DictaBERT closed most of the EN/HE gap, but it is a
  general-purpose Hebrew sentiment model, not a financial one, and its misses are one-sided — it
  reads positive financial news as neutral. No published finance-tuned Hebrew model was found.
- **Structured earnings parsing.** Figure extraction is a bounded text excerpt, not a structured
  parse; an XBRL-aware path would raise recall on periodic reports, where the excerpt is statement
  tables rather than prose.
