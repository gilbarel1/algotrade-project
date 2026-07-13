# S&P 500 Integration Plan (proposed — post-Step-13)

> **Status: proposed future work, not yet part of the design.** `docs/design.md` remains
> the source of truth and is TA-35-only until Step 14 below amends it. Do not start this
> work before Step 13 (chat assistant) is complete and reviewed.

Approved direction (2026-07-13):

- **Both markets, mixed watchlist** — one watchlist can hold `TEVA.TA` and `AAPL` together; each ticker carries a market tag.
- **SEC EDGAR API** as the US earnings source (Maya stays for TASE).
- **One cron, per-market open-hours gate** inside the run.
- **Design-doc amendment first**, then CLAUDE.md-style build steps (doc wins).

## Why this is a design change, not a config tweak

The TA-35 assumption is load-bearing in four places:

1. **Trading calendar** — `quant_service/data/yahoo.py` hardcodes `TASE_CLOSED_WEEKDAYS = (4, 5)` and reindexes onto a Sunday–Thursday grid. A US ticker run through it silently drops every Friday session and forward-fills a manufactured Sunday one.
2. **Earnings** — the Earnings Agent scrapes `maya.tase.co.il`, which lists only Israeli issuers; US names return no disclosures forever.
3. **News** — the RSS feeds are Globes/Ynet; only NewsAPI would cover US names.
4. **Schedule gate** — the cron plus TASE-hours check is Sun–Thu Israeli hours; NYSE runs Mon–Fri Eastern.

## A. Market abstraction (the one new concept)

Every ticker gets a **market** (`tase` | `us`), derived from the Yahoo suffix (`*.TA` → `tase`, bare symbol → `us`) with an optional explicit override in config. A new `markets:` block in `config/universe.yaml` makes each market a bundle of properties instead of global assumptions:

```yaml
markets:
  tase:
    closed_weekdays: [4, 5]          # Fri, Sat
    trading_hours: { tz: "Asia/Jerusalem", days: [0,1,2,3,4],  # Sun–Thu (n8n: 0=Sun)
                     open: "09:30", close: "17:25" }
    earnings_source: maya
    rss_feed_groups: [en_il, he_il]  # keys into rss_feeds
    currency: ILS
  us:
    closed_weekdays: [5, 6]          # Sat, Sun
    trading_hours: { tz: "America/New_York", days: [1,2,3,4,5],
                     open: "09:30", close: "16:00" }
    earnings_source: edgar
    rss_feed_groups: [en_us]
    currency: USD
```

`rss_feeds` becomes keyed by group (`en_il`, `he_il`, `en_us`) instead of `en`/`he`; a new `en_us` group gets 1–2 US finance feeds (verify live URLs at build time, same as was done for Globes/Ynet). US tickers get `search_terms` entries too (EN-only, e.g. `AAPL: ["Apple Inc", "Apple"]` — same collision-avoidance care as "Teva").

The rubric, Risk Manager, dual-sentiment mechanism, schemas, costs, and evals are **market-agnostic and unchanged**. FinBERT/HeBERT routing already handles US news correctly (everything is EN → FinBERT; HeBERT simply never fires).

## B. Design-doc amendment (Step 14 — doc first)

Section-by-section changes to `docs/design.md`:

- **§1 Scope**: watchlist becomes "TA-35 and S&P 500 constituents, mixed"; run modes note the per-market gate.
- **§3.2 Earnings Agent**: add EDGAR as the US source. Pipeline (classify → self-consistency n=3 @ 0.3 → majority-vote/ambiguous) unchanged; only the fetch source routes by market.
- **§4.1 Sources table**: add row — US earnings disclosures: SEC EDGAR (`data.sec.gov` submissions + filing archives; free JSON API, requires a declared `User-Agent`, ~10 req/s limit; ticker→CIK via `company_tickers.json`).
- **§4.3 Cleaning**: "reindex onto the TASE calendar" → "reindex onto the **market's** session grid (closed weekdays per `markets:` config)". Multi-day-gap dropping already absorbs exchange holidays for both markets — no holiday calendar needed.
- **§4.4 Runtime universe**: add the `markets:` block and keyed `rss_feeds`; document market derivation from suffix.
- **§5 `/earnings/fetch`**: note per-market source routing (Maya | EDGAR); response shape unchanged (`url` points at the EDGAR filing index for US).
- **§6.1 Triggers**: cron widens to cover both windows (`0 10-23 * * 0-5` in Asia/Jerusalem); the in-workflow gate becomes per-market — a scheduled run analyzes only tickers whose market is currently in-session; if none, exit cleanly with no `runs` row (existing behavior, generalized).
- **§8 Report**: per-ticker header shows market + currency; executive summary groups counts by market. Times still rendered Asia/Jerusalem (§11.2 unchanged).
- **§13 Limitations**: add — EDGAR excerpts come from 8-K press-release exhibits (EX-99.*) whose formatting varies; extraction falls back to `ambiguous` exactly as with Maya. NewsAPI free tier (100 req/day) is tighter with a bigger universe.

## C. Build steps (CLAUDE.md style — one step, stop, review)

**Step 14 — Design amendment + market config schema.**
Amend design.md per (B); restructure `config/universe.yaml` (`markets:` block, keyed `rss_feeds`, market-derivation rule); add a `market(symbol)` helper (new `quant_service/data/markets.py`: suffix → market, plus accessor for the market's config). No behavior change for the existing TA-35 flow.
*Verify:* existing smoke test still green; `market("TEVA.TA")=="tase"`, `market("AAPL")=="us"`.

**Step 15 — Market-aware calendar + ingestion.**
`data/yahoo.py`: replace `TASE_CLOSED_WEEKDAYS`/`_tase_sessions` with a per-market closed-weekday grid from `markets.py`; `data/cache.py` gap heuristics take the market's weekend length. `prices` table unchanged (symbol is already the key).
*Verify:* ingest `AAPL` — Fridays present, no Sunday rows; ingest `TEVA.TA` — unchanged vs. current output (regression check on an existing symbol).

**Step 16 — EDGAR earnings source.**
New `data/edgar.py`: ticker→CIK from `company_tickers.json` (cached), recent filings from `data.sec.gov/submissions/CIK##########.json`, filter to 8-K/10-Q/10-K within `earnings_window_days`, pull the press-release exhibit (EX-99.*) text for the newest item as the bounded `excerpt`. Plain `httpx` — no Playwright. Declared `User-Agent` (contact email) per SEC policy; TTL-cache like Maya. `/earnings/fetch` routes by `market(ticker)`; response contract unchanged so the n8n Earnings Agent sub-workflow needs **no changes**. Failure degrades, never 500s.
*Verify:* `/earnings/fetch` for `AAPL` right after a real filing returns items with an excerpt; the agent commits only verbatim figures (`confidence: 3`) and marks absent ones `ambiguous`; `TEVA.TA` still routes to Maya.

**Step 17 — News feeds + per-market schedule gate.**
`/news/fetch` selects RSS groups by the ticker's market (NewsAPI path unchanged); add the `en_us` feeds and US `search_terms`. Schedule gate: `/runs/start` (scheduled mode only) filters the watchlist to in-session markets via `markets.trading_hours`; empty ⇒ orchestrator exits before writing anything. Gate placement (service vs. pre-call n8n gate) depends on how Step 10 lands — decide then. Widen `schedule_cron`.
*Verify:* scheduled run at 20:00 Israel time analyzes only US names; at 11:00 only TASE names; manual/chat runs are never filtered.

**Step 18 — Report + docs + eval.**
Report template: market/currency per ticker page, market-grouped executive summary. README: markets section, updated limitations. Optional: extend `eval/earnings_labeled.jsonl` with 3–5 EDGAR disclosures and `eval/sentiment_labeled.jsonl` with US items.
*Verify:* one mixed-watchlist run (`["TEVA.TA","AAPL"]`) produces a single PDF with both tickers, correct currencies, and a market-grouped summary; `runs`/`recommendations`/`costs` rows all present.

## D. Known risks / open points (recorded, not resolved)

- **NewsAPI quota**: 100 req/day free tier across a larger universe — trim the watchlist or paid tier; flag in §13.
- **EDGAR excerpt quality**: EX-99.* exhibits are HTML of wildly varying structure; the bounded-excerpt approach may need per-filing-type tuning. The `ambiguous` fallback makes this safe but possibly thin at first.
- **`/runs/start` gate placement**: depends on the (not-yet-built) Step 10 implementation — decide at Step 17.
- **DST edge**: Israel and US DST transitions are offset by ~2–3 weeks; per-market `tz` handles this as long as gating computes "now" in each market's own tz, never a fixed offset.
