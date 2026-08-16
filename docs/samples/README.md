# Sample reports

Two reports the system actually produced, committed so the output can be inspected **without
running the pipeline** — a full run needs an OpenRouter key and a NewsAPI key that a reader of this
repo will not have.

Generated reports normally land in `reports/YYYY-MM-DD/HHMM/report.pdf`, which is gitignored (they
accumulate on every run). These two are copies, kept because each demonstrates a different
guarantee. Both are walked through in [`../results.md`](../results.md).

| File | Run | What it shows |
| --- | --- | --- |
| [`sample-report-dual-sentiment.pdf`](sample-report-dual-sentiment.pdf) | `r_2026-08-06T11:39` · NFLX | **Dual sentiment deciding an outcome.** LLM 0.03 vs FinBERT 0.15, disagreement **0.38 ⚠ split** — over the §3.4 threshold, so the cap fires and ceilings conviction. One citation reads `L 0.15 / M −0.96`: a per-article gap of 1.11 between the two scorers, printed rather than averaged away. The reasoning trace shows the critique countering `short` with five objections and the final declining it on the rubric. All three agents `ok`. |
| [`sample-report-mixed-watchlist.pdf`](sample-report-mixed-watchlist.pdf) | `r_2026-08-03T07:08` · TEVA.TA + AAPL | **One run, two markets, one PDF.** Market-grouped executive summary; each ticker page carries its own market and currency (`TASE · ILS`, `US · USD`); earnings routed per market (Maya vs SEC EDGAR). Apple's earnings panel shows the "never invent numbers" rule doing both of its jobs at once — revenue **$109.4 billion** and EPS **$2.02** committed with all three self-consistency samples agreeing (`×3`), and `guidance` refused as **`ambiguous`** because the exhibit does not state it. Sentiment is `degraded` on both tickers (the news key was rejected at the time), which the Risk Manager treats as a measurement gap rather than a bearish signal. |

Every page ends with the methodology footer: the decision rubric, the conviction caps, the
critique-loop description, and the disclaimer that the report is educational and that the loop
audits the reasoning without guaranteeing the call is correct.

> **One caveat on the TA-35 page of the mixed-watchlist sample.** Both runs predate the §4.3 session-grid
> fix. At the time, TASE series were reindexed onto the configured Sun–Thu week, which dropped real Friday
> bars and forward-filled synthetic Sundays into ~19% of the history — so **TEVA.TA's indicator values in
> that PDF (RSI, MACD, Bollinger, ATR) are computed from a partly-fabricated series and are understated**,
> ATR most of all. The dual-sentiment and mixed-market behaviour each sample is kept to demonstrate is
> unaffected, and the NFLX report is unaffected entirely: US tickers always used a Mon–Fri grid that
> matched their feed. Regenerating these needs a live run with API keys; the README's
> [Limitations](../../README.md#limitations) records the same caveat.
