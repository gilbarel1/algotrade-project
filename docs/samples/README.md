# Sample reports

Two reports the system actually produced, committed so the output can be inspected **without
running the pipeline** — a full run needs an OpenRouter key and a NewsAPI key that a reader of this
repo will not have.

Generated reports normally land in `reports/YYYY-MM-DD/HHMM/report.pdf`, which is gitignored (they
accumulate on every run). These two are copies, kept because each demonstrates a different
guarantee. Both are walked through in [`../results.md`](../results.md).

| File | Run | What it shows |
| --- | --- | --- |
| [`sample-report-dual-sentiment.pdf`](sample-report-dual-sentiment.pdf) | `r_2026-08-09T08:50` · AAPL + NFLX | **Dual sentiment at its most extreme — and the critique overturning a call.** *This is the run shown in the demo video.* On Apple the two scorers land on opposite poles: LLM **0.35** vs FinBERT **−0.95**, disagreement **1.30 ⚠ split**. The reason is legible in the coverage — Apple beat on revenue and EPS and the stock fell 10% anyway, so the LLM scored the fundamentals bullish and the finance-tuned model scored the language of the decline bearish. The draft opened `long`/medium on that bullish signal; the devil's advocate attacked it as *"only 1 truly strong signal with material internal conflict"*, and the final conceded and downgraded to **`hold`/low**, naming the `dual_sentiment` cap. Netflix on the same report shows the milder split (0.39) across five cited articles. Apple's earnings panel carries **$109.4 billion** and **$2.02** at `×3` with `guidance` refused as `ambiguous`. All three agents `ok`. |
| [`sample-report-mixed-watchlist.pdf`](sample-report-mixed-watchlist.pdf) | `r_2026-08-16T12:53` · TEVA.TA + AAPL | **One run, two markets, one PDF — and the critique overturning a call.** Market-grouped executive summary; each ticker page carries its own market and currency (`TASE · ILS`, `US · USD`); earnings routed per market (Maya vs SEC EDGAR). On **TEVA.TA** the draft opened `long`/medium on a *miscounted* agreement (it scored neutral earnings as a bullish vote); the devil's advocate attacked the arithmetic — *"neutral earnings are a placeholder, not a vote… true agreement is 1-of-3"* — and the final conceded in writing and downgraded to **`hold`/low**. On **AAPL** the same loop runs the other way: the critique argued for `hold`, the final kept `long` but conceded the dual-sentiment split (LLM **0.6** vs model **0.194**, disagreement **0.4061**) and let the cap ceiling conviction at medium. Apple's earnings panel shows the "never invent numbers" rule doing both jobs at once — revenue **$109.4 billion** and EPS **$2.02** committed with all three self-consistency samples agreeing (`×3`), `guidance` refused as **`ambiguous`**. Teva's 10-Q is the contrasting source: classified high-materiality, every figure `ambiguous`. All three agents `ok`. |

Every page ends with the methodology footer: the decision rubric, the conviction caps, the
critique-loop description, and the disclaimer that the report is educational and that the loop
audits the reasoning without guaranteeing the call is correct.

> **On the two runs' vintage.** The mixed-watchlist report was generated **after** the §4.3
> session-grid fix, so its TA-35 indicators come from a clean series (Teva's RSI 67.8 is a real reading,
> not a forward-filled one). The dual-sentiment report predates the fix and is **unaffected by it**:
> `AAPL` and `NFLX` are US tickers, and US symbols always used a Mon–Fri grid that already matched their
> feed — verified by re-ingesting `AAPL` after the fix and getting a byte-identical series. The fix and
> what it changed are described in [design §4.3](../design.md) and the README's
> [Limitations](../../README.md#limitations).
