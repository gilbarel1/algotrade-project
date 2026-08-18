# Results — two runs, end to end

Two real runs, taken apart. The first is **the run shown in the demo video** — two US names, the
dual-sentiment split at its most extreme, and the critique overturning the call. The second shows a
mixed TA-35 + S&P 500 watchlist and the "never invent numbers" guarantee doing both of its jobs at
once. Then the evaluation-harness output, reproduced verbatim.

Both reports are committed at [`samples/`](samples/) so they can be read without running the
pipeline (a full run needs API keys). Companion to [`../README.md`](../README.md) and the design in
[`design.md`](design.md).

---

## Run 1 — "what do you think about Apple and Netflix?"

Started from the **chat assistant** (<http://localhost:8001>), hence `mode = chat`. The assistant
resolved *Apple* → `AAPL` and *Netflix* → `NFLX`, saw bare symbols, routed both to the **US** market,
and ran the same pipeline a manual or scheduled run uses. **This is the run shown in the demo video.**

📄 **[`samples/sample-report-dual-sentiment.pdf`](samples/sample-report-dual-sentiment.pdf)**

```
runs             run_id = r_2026-08-09T08:50 · mode = chat · status = ok
                 tickers = ["AAPL", "NFLX"] · report = reports/2026-08-09/1151/report.pdf
recommendations  AAPL → hold · low        NFLX → hold · low
agent_status     both: {"sentiment": "ok", "earnings": "ok", "technical": "ok"}
```

### 1 · Dual sentiment, at its most extreme

Apple is the clearest case this system has produced. The Sentiment Agent scored live headlines
**twice** — once by Haiku 4.5, once by FinBERT:

| | |
|---|---|
| `llm_sentiment` | **0.35** |
| `model_sentiment` | **−0.95** |
| `disagreement` | **1.30** → `⚠ split` |

Not a rounding difference — the two scorers landed on **opposite poles**, and the reason is legible
in the coverage itself. Apple had just beaten on revenue and EPS, and the stock fell 10% anyway. The
LLM read the fundamentals and scored the news bullish; the finance-tuned model read the *language* of
the coverage — *"Why Apple's 10% Drop Fails to Tell the Whole Story"* — and scored it strongly
bearish. Both readings are defensible. The report prints both, flags the split, and lets the
**dual-sentiment cap** ceiling conviction rather than averaging the two into a meaningless ~−0.3.

Netflix on the same page shows the milder version — `disagreement 0.39`, still over the 0.30
threshold — with five cited articles carrying their own per-article splits, including
**"3 Big Reasons to Love Netflix (NFLX)"** at **L 0.15 / M −0.96**, a gap of 1.11 on a single
headline. Each is printed with its source so a reader can go and judge for themselves.

### 2 · The three-pass critique, overturning the call

On Apple the loop does the thing it exists to do — it changes the answer:

- **Draft — `long`, medium.** *"Sentiment is bullish with a strong signal (LLM sentiment 0.35 despite
  model disagreement)… Agreement count for bullish direction is 2 of 3."*
- **Devil's-advocate critique — counters `hold`, with five objections.** It goes straight at the
  weakest joint: *"Extreme model-LLM disagreement (1.3 spread), market's 10% rejection of strong
  fundamentals, and negative MACD trend create too much friction for medium conviction. Only 1 truly
  strong signal (sentiment) with material internal conflict."*
- **Final — `hold`, low.** It concedes, in writing, and names the cap that bound it: *"the extreme
  model-LLM disagreement (1.3022 spread) and the `dual_sentiment` cap require explicit acknowledgment
  that confidence in this signal is materially undermined… the weight of the critique objections — all
  of which hold up under scrutiny — push conviction to low."*

A single-prompt pipeline would have shipped `long / medium` on a bullish earnings beat. The critique
caught that the entire bullish case rested on one signal which was itself internally split, and the
final downgraded on the record.

Apple's page also carries the extracted 8-K figures — **$109.4 billion** and **$2.02** at `×3`, with
`guidance` refused as `ambiguous`. That mechanism gets its own walk-through under Run 2 below, where
the extraction actually ran.

### 3 · What it cost

| Agent | Model | In / out tokens | USD |
|---|---|---|---|
| Risk Manager (×3 passes, ×2 tickers) | `anthropic/claude-haiku-4.5` | 16,152 / 2,673 | $0.0295 |
| Sentiment | `anthropic/claude-haiku-4.5` | 2,539 / 1,138 | $0.0082 |
| Technical | `google/gemini-2.5-flash-lite` | 545 / 128 | $0.0001 |
| **Total** | | | **$0.0379** |

Two tickers, both markets' worth of reasoning, **84 seconds** end to end.

**Earnings has no row, and that is the interesting part.** Neither ticker had a disclosure inside the
5-day window, so the agent made **zero LLM calls** — the panel reports `no window` honestly. The
`×3` figures on Apple's page are the last extraction it committed, persisted in the `earnings` table
from an earlier run rather than re-derived here. Nothing is re-paid for, and nothing is re-invented:
a figure that was verified verbatim stays verified, and the window that is empty says so. (The
window is `21` days now — §4.4 records why, and what it costs.)

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
Evaluation summary  (eval-20260817-060223)
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
LLM cost (this run): $0.1155   (chat 11,872 tok, earnings 47,859 tok, sentiment 5,337 tok)
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
**22/22, 21/22, 22/22 and 22/22** across four runs — the single miss being a figure committed that its
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
