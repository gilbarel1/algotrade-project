"""WeasyPrint + Jinja2 PDF rendering for POST /report (§8, §8.2).

Two responsibilities:

- ``build_context`` turns the orchestrator's request — ``{run_id,
  recommendations, summary}`` where each recommendation is the condensed §6.3
  Risk Manager output — into the full template context. The §8.1 blocks the
  payload does *not* carry (news citations, the Maya disclosure + extracted
  figures, the indicator snapshot, the price chart) are **enriched server-side
  from DuckDB** (`news`/`earnings` tables written by the agents during the run,
  and the `prices` cache) — the §2 "heavy data stays server-side" split. Every
  enrichment is best-effort per ticker: a missing row degrades that block to
  "unavailable", never the whole report.

- ``render_pdf`` renders `templates/report.html.j2` with WeasyPrint to
  ``reports/YYYY-MM-DD/HHMM/report.pdf`` (partition in Asia/Jerusalem, §11.2)
  and returns the repo-relative path the orchestrator threads into
  ``/runs/finish``.

WeasyPrint is imported lazily inside ``render_pdf`` so importing this module (or
the whole service) never requires the GTK runtime — only actually rendering a
PDF does.

``python -m pdf.render [--ticker TEVA.TA]`` renders a one-ticker preview from
live DB enrichment (§8.2 single-ticker development preview).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from data import cache, earnings_store, markets, news_store, run_store
from indicators import calc
from pdf import charts

# pdf/render.py -> pdf/ -> quant_service/ -> repo root.
_QUANT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_QUANT_DIR)
_TEMPLATES_DIR = os.path.join(_QUANT_DIR, "templates")
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "universe.yaml")
_RUBRIC_PATH = os.path.join(_REPO_ROOT, "config", "rubric.yaml")

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# How much price history to pull so the 50-day MA is warm at the left edge of
# the 90-day chart window and the indicator snapshot has enough bars.
_CHART_LOOKBACK_DAYS = 150

_FIGURE_ORDER = ("revenue", "eps", "guidance")
_CONVICTION_ORDER = {"high": 2, "medium": 1, "low": 0}


def _load_yaml(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except OSError:
        return {}


def _report_dir() -> str:
    """Resolve the report output dir: REPORT_DIR env > universe.report_dir > 'reports'."""
    cfg = _load_yaml(_CONFIG_PATH)
    return os.environ.get("REPORT_DIR") or cfg.get("report_dir") or "reports"


def _disagreement_threshold() -> float:
    """The §3.4 dual-sentiment cap threshold from config/rubric.yaml (never hardcoded)."""
    rubric = _load_yaml(_RUBRIC_PATH)
    try:
        return float(
            rubric["conviction_caps"]["dual_sentiment"]["disagreement_threshold"]
        )
    except (KeyError, TypeError, ValueError):
        return 0.3


# --------------------------------------------------------------------------
# Context building
# --------------------------------------------------------------------------


def build_context(
    con,
    run_id: str,
    recommendations: List[Dict[str, Any]],
    summary: str,
) -> dict:
    """Build the full §8.1 template context from the request + DuckDB enrichment."""
    threshold = _disagreement_threshold()
    recs = [r for r in (recommendations or []) if isinstance(r, dict)]

    enriched = [_enrich_ticker(con, rec, threshold) for rec in recs]

    # --- Header (§8.1): run metadata from the `runs` row when available. ------
    run = None
    try:
        run = run_store.get_run(con, run_id)
    except Exception:  # noqa: BLE001 - header degrades to request-derived values
        run = None
    # `runs.tickers` is a DuckDB JSON column: get_run returns it as the raw JSON
    # *string*, so parse it to a list before the template joins/counts it.
    watchlist = _coerce_tickers(run.get("tickers")) if run else []
    if not watchlist:
        watchlist = [e["ticker"] for e in enriched if e.get("ticker")]
    header = {
        "run_id": run_id,
        "generated_at": datetime.now(_IL_TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "mode": (run or {}).get("mode") or "unknown",
        "watchlist": watchlist,
        "n_tickers": len(watchlist),
    }

    # --- Executive summary (§8.1): counts + top-3 highest-conviction calls. ---
    counts: Dict[str, int] = {}
    for e in enriched:
        call = (e.get("final") or {}).get("recommendation") or "unknown"
        counts[call] = counts.get(call, 0) + 1

    directional = [
        e
        for e in enriched
        if (e.get("final") or {}).get("recommendation") in ("long", "short", "avoid")
    ]
    directional.sort(
        key=lambda e: _CONVICTION_ORDER.get(
            (e.get("final") or {}).get("conviction"), -1
        ),
        reverse=True,
    )
    top3 = [
        {
            "ticker": e["ticker"],
            "recommendation": (e["final"] or {}).get("recommendation"),
            "conviction": (e["final"] or {}).get("conviction"),
            "rationale": _one_line((e["final"] or {}).get("rationale")),
        }
        for e in directional[:3]
    ]

    methodology = {
        "disagreement_threshold": threshold,
        "rubric": _load_yaml(_RUBRIC_PATH),
    }

    return {
        "run_id": run_id,
        "summary": summary or "",
        "header": header,
        "exec": {
            "counts": counts,
            "top3": top3,
            # §8.1: counts are grouped by market only when the watchlist spans
            # more than one — a single-market run reads as it always did.
            "by_market": _counts_by_market(enriched),
        },
        "recommendations": enriched,
        "methodology": methodology,
    }


def _counts_by_market(enriched: List[dict]) -> List[dict]:
    """Per-market recommendation counts (§8.1), or [] for a single-market run.

    Returning [] rather than one group is what lets the template stay silent on
    a TA-35-only run: the grouping is information only when there is more than
    one market to tell apart.
    """
    groups: Dict[str, dict] = {}
    for e in enriched:
        name = e.get("market") or "unknown"
        group = groups.setdefault(
            name, {"market": name, "currency": e.get("currency") or "", "counts": {}}
        )
        call = (e.get("final") or {}).get("recommendation") or "unknown"
        group["counts"][call] = group["counts"].get(call, 0) + 1
    if len(groups) < 2:
        return []
    return [groups[name] for name in sorted(groups)]


def _enrich_ticker(con, rec: dict, threshold: float) -> dict:
    """One condensed §6.3 rec + its DuckDB-sourced §8.1 detail blocks."""
    ticker = rec.get("ticker") or "UNKNOWN"

    sent = rec.get("sentiment") or {}
    disagreement = sent.get("disagreement")
    sentiment = {
        "llm_sentiment": sent.get("llm_sentiment"),
        "model_sentiment": sent.get("model_sentiment"),
        "disagreement": disagreement,
        "summary": sent.get("summary") or "",
        "disagree_high": isinstance(disagreement, (int, float))
        and disagreement > threshold,
    }

    earn = rec.get("earnings") or {}
    earnings_panel = {
        # wire shape uses is_earnings_window; the RM panel condenses to is_window.
        "is_window": earn.get("is_window", earn.get("is_earnings_window", False)),
        "materiality": earn.get("materiality"),
        "summary": earn.get("summary") or "",
    }

    tech = rec.get("technical") or {}
    technical_panel = {
        "signal": tech.get("signal"),
        "summary": tech.get("summary") or "",
    }

    # Fetch the cached OHLC once and share it between the indicator snapshot and
    # the chart (each would otherwise re-read / potentially re-ingest).
    prices = _prices(con, ticker)

    market, currency = _market_labels(ticker)

    return {
        "ticker": ticker,
        "market": market,
        "currency": currency,
        "status": rec.get("status") or "ok",
        "draft": rec.get("draft"),
        "critique": rec.get("critique"),
        "final": rec.get("final"),
        "sentiment": sentiment,
        "earnings_panel": earnings_panel,
        "technical_panel": technical_panel,
        "citations": _citations(con, ticker),
        "disclosure": _disclosure(con, ticker),
        "indicators": _indicator_snapshot(prices),
        "chart_uri": _chart_uri(prices, ticker),
    }


def _market_labels(ticker: str) -> tuple[str, str]:
    """The ticker's market and reporting currency for the §8.1 header.

    A mixed watchlist puts ILS and USD figures in one PDF, so each ticker page
    states its unit — an ILS revenue read as USD is a 3x error. Best-effort like
    the other enrichments: an unresolvable market degrades the tag, not the page.
    """
    try:
        name = markets.market(ticker)
        return name, markets.currency(name)
    except Exception:  # noqa: BLE001
        return "", ""


def _citations(con, ticker: str) -> List[dict]:
    """Top scored news articles for the Sentiment panel citation block (§8.1)."""
    try:
        return news_store.top_scored(con, ticker, limit=5)
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        return []


def _disclosure(con, ticker: str) -> Optional[dict]:
    """Latest Maya disclosure + extracted figures for the Earnings panel (§8.1).

    Figures are turned into a display list, each flagged `ambiguous` so the
    template can style unresolved figures distinctly (§8.1: "ambiguous figures
    visually distinct").
    """
    try:
        row = earnings_store.latest_for_symbol(con, ticker)
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None

    extracted = row.get("extracted") or {}
    figures = []
    keys = list(_FIGURE_ORDER) + [k for k in extracted if k not in _FIGURE_ORDER]
    for name in keys:
        fig = extracted.get(name)
        if not isinstance(fig, dict):
            continue
        value = fig.get("value")
        figures.append(
            {
                "name": name,
                "value": value,
                "confidence": fig.get("confidence"),
                "ambiguous": value == "ambiguous",
            }
        )
    row["figures"] = figures
    return row


def _prices(con, ticker: str):
    """Cached OHLC for a ticker (~150d), or None. Never raises (best-effort)."""
    try:
        df, _ = cache.get_cached_ohlc(con, ticker, _CHART_LOOKBACK_DAYS)
        if df is None or df.empty:
            return None
        return df
    except Exception:  # noqa: BLE001
        return None


def _indicator_snapshot(prices) -> Optional[dict]:
    """Recompute the §3.3 indicator snapshot from the cached prices (§8.1)."""
    if prices is None:
        return None
    try:
        return calc.compute_indicators(prices)
    except Exception:  # noqa: BLE001
        return None


def _chart_uri(prices, ticker: str) -> Optional[str]:
    """Price-chart PNG as a base64 data URI (avoids WeasyPrint file:// handling)."""
    try:
        png = charts.price_chart_png(prices, ticker)
        if not png:
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _coerce_tickers(value) -> List[str]:
    """Normalize `runs.tickers` to a list of strings (it may arrive as JSON text)."""
    if isinstance(value, list):
        return [str(t) for t in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except (TypeError, ValueError):
            return [value]
    return []


def _one_line(text: Optional[str], limit: int = 160) -> str:
    if not text:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2", "xml"]),
    )


def _report_paths(now: Optional[datetime] = None) -> tuple[str, str]:
    """Return (absolute_path, repo_relative_posix_path) for this run's PDF.

    Partitioned by Asia/Jerusalem date/time (§8.3, §11.2): the report file is
    named for local wall-clock, while stored timestamps stay UTC elsewhere.
    """
    local = (now or datetime.now(_IL_TZ)).astimezone(_IL_TZ)
    report_dir = _report_dir()
    rel = f"{report_dir}/{local:%Y-%m-%d}/{local:%H%M}/report.pdf"
    base = report_dir if os.path.isabs(report_dir) else os.path.join(_REPO_ROOT, report_dir)
    abs_path = os.path.join(base, f"{local:%Y-%m-%d}", f"{local:%H%M}", "report.pdf")
    return abs_path, rel


def render_pdf(context: dict) -> str:
    """Render the report to a PDF on disk; return its repo-relative posix path."""
    from weasyprint import HTML  # lazy: GTK only needed to actually render

    html = _jinja_env().get_template("report.html.j2").render(**context)
    abs_path, rel_path = _report_paths()
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    HTML(string=html, base_url=_TEMPLATES_DIR).write_pdf(abs_path)
    return rel_path


# --------------------------------------------------------------------------
# Single-ticker development preview (§8.2)
# --------------------------------------------------------------------------


def _sample_recommendation(ticker: str) -> dict:
    """A §6.3-shaped recommendation for the standalone preview (real DB enrichment)."""
    return {
        "ticker": ticker,
        "status": "ok",
        "draft": {
            "recommendation": "long",
            "conviction": "high",
            "rationale": "Preview draft: momentum and sentiment constructive.",
            "earnings_direction": "neutral",
        },
        "critique": {
            "counter_recommendation": "hold",
            "key_objections": [
                "LLM sentiment more bearish than the fine-tuned model",
                "earnings window opens soon",
            ],
            "conviction_challenge": "high -> medium",
        },
        "final": {
            "recommendation": "long",
            "conviction": "medium",
            "rationale": "Preview final: two of three signals constructive; conviction "
            "capped at medium for event risk. Critique objection on the dual-sentiment "
            "split was incorporated.",
        },
        "sentiment": {
            "llm_sentiment": -0.12,
            "model_sentiment": 0.20,
            "disagreement": 0.32,
            "summary": "Mixed tone; LLM more bearish than FinBERT.",
        },
        "earnings": {
            "is_window": False,
            "materiality": "low",
            "summary": "No recent earnings disclosure.",
        },
        "technical": {"signal": "bullish_momentum", "summary": "RSI 62, MACD positive."},
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Render a single-ticker report preview (§8.2)."
    )
    parser.add_argument(
        "--ticker", default="TEVA.TA", help="Ticker to preview (default TEVA.TA)."
    )
    parser.add_argument(
        "--run-id", default="preview", help="Run id label for the header."
    )
    args = parser.parse_args(argv)

    con = cache.connect()
    try:
        recs = [_sample_recommendation(args.ticker)]
        context = build_context(
            con, args.run_id, recs, summary=f"preview for {args.ticker}"
        )
    finally:
        con.close()
    path = render_pdf(context)
    print(f"Rendered preview: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
