"""POST /news/fetch and POST /news/store — server-side news for the Sentiment Agent (§5, §3.1).

Keeping NewsAPI/RSS access, §4.3 cleaning, and `news`-table persistence in the
quant service means n8n moves only compact, pre-cleaned items — no raw feeds, no
DuckDB driver in n8n (§2 heavy-data guardrail).

- **/news/fetch** resolves the ticker's company terms and feeds from
  `config/universe.yaml`, pulls NewsAPI (EN, searched) plus the RSS groups of the
  ticker's **market** (§4.4 — `en_il`+`he_il` for TASE, `en_us` for US; pulled
  whole), applies §4.3 cleaning (dedupe by url; keep only items whose
  headline/summary text contains a term), and returns the items together with the
  few-shot examples the LLM scorer needs. A source that fails degrades the summary
  (prefix ``degraded:``) rather than 500-ing; zero clean items with all sources
  healthy is a valid "no coverage" result (§13), left for the sub-workflow to
  distinguish.
- **/news/store** upserts the per-article dual scores into `news` (§4.2).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache, edgar, markets, news_store, newsapi, rss
from data.markets import load_config as _load_config
from data.textclean import clean_text, mentions_term
from nlp import language_detect

router = APIRouter()

# routers/news.py -> routers/ -> quant_service/ -> repo root.
# Config itself is read by `data/markets.py` (the single §4.4 reader).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_FEWSHOT_PATH = os.path.join(_REPO_ROOT, "prompts", "sentiment_examples.jsonl")


def _load_fewshot() -> List[dict]:
    """Read the few-shot labeled examples (§3.1); empty list if not present."""
    if not os.path.exists(_FEWSHOT_PATH):
        return []
    examples: List[dict] = []
    with open(_FEWSHOT_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                examples.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a malformed example line must not break the fetch
    return examples


def _item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# Some feeds (notably Ynet) put a block of HTML — <div><a><img> plus a caption —
# in the RSS <description>, hundreds of characters of markup. That must not reach
# the LLM prompt or /sentiment (§2: only short text crosses the boundary) or be
# stored as-is, so summaries are stripped to plain text and bounded.
# Cleaning/matching helpers are shared with the Maya scraper via data/textclean.
_SUMMARY_MAX = 300


def _clean(raw_items: List[dict], terms: List[str]) -> List[dict]:
    """Apply §4.3 cleaning: strip markup, term-in-text filter, dedupe by url, assign ids.

    HTML/entities are stripped and whitespace collapsed *before* term matching, so
    a term appearing only inside a tag/URL (metadata, not visible text) does not
    keep the item. Language is trusted from the source when set (RSS carries a
    known feed language), else detected from the cleaned text.
    """
    seen: set[str] = set()
    cleaned: List[dict] = []
    for item in raw_items:
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        headline = clean_text(item.get("headline", ""))
        summary = clean_text(item.get("summary", ""), _SUMMARY_MAX)
        if not mentions_term(headline, summary, terms=terms):
            continue
        seen.add(url)
        language = item.get("language") or language_detect.detect(
            f"{headline} {summary}"
        )
        cleaned.append(
            {
                "id": _item_id(url),
                "headline": headline,
                "summary": summary,
                "source": item.get("source", ""),
                "url": url,
                "published_at": item.get("published_at"),
                "language": language,
            }
        )
    return cleaned


class NewsFetchRequest(BaseModel):
    ticker: str
    window_minutes: Optional[int] = None


@router.post("/news/fetch")
def news_fetch(req: NewsFetchRequest):
    config = _load_config()
    window = req.window_minutes or int(config.get("news_window_minutes", 120))
    terms = (config.get("search_terms") or {}).get(req.ticker, [])
    feeds = config.get("rss_feeds") or {}
    try:
        market_cfg = markets.market_config(markets.market(req.ticker, config), config)
    except ValueError as exc:  # `market_overrides` names a market not in config
        return {
            "ticker": req.ticker,
            "items": [],
            "few_shot": _load_fewshot(),
            "summary": f"degraded: {exc}; cannot query news.",
        }

    derived_errors: List[str] = []
    derived = False
    if not terms:
        # Hand-tuned terms always win; this only runs when the config has never
        # seen the ticker. The chat assistant (§6.5) accepts any S&P 500 name
        # ad-hoc, so most US tickers reaching here are unconfigured by design —
        # deriving a term from the SEC registrant name is what keeps their
        # Sentiment agent from degrading (§3.1, §4.4 `search_terms_fallback`).
        if market_cfg.get("search_terms_fallback") == "sec_registry":
            terms, derived_errors = edgar.company_search_terms(req.ticker)
            derived = bool(terms)

    if not terms:
        # Still nothing: a TASE symbol absent from config (whose Hebrew terms
        # cannot be derived), or the registry lookup itself failed.
        reason = "; ".join(derived_errors) if derived_errors else (
            f"no search_terms configured for {req.ticker}"
        )
        return {
            "ticker": req.ticker,
            "items": [],
            "few_shot": _load_fewshot(),
            "summary": f"degraded: {reason}; cannot query news.",
        }

    raw_items: List[dict] = []
    errors: List[str] = []

    # English via NewsAPI (searched server-side by the company terms).
    try:
        raw_items.extend(
            newsapi.fetch_newsapi(
                terms,
                window,
                os.environ.get("NEWSAPI_API_KEY", ""),
                # The market's publisher allowlist (§4.4). Absent for `tase`, so
                # Israeli names keep their unrestricted Step-5 behavior.
                domains=market_cfg.get("newsapi_domains"),
            )
        )
    except newsapi.NewsAPIError as exc:
        # Most of these messages already name the source ("NewsAPI returned 401 …"),
        # so prefixing unconditionally produced "NewsAPI: NewsAPI returned 401" in a
        # summary the report prints verbatim. Only label the ones that don't.
        reason = str(exc)
        errors.append(reason if reason.lower().startswith("newsapi") else f"NewsAPI: {reason}")

    # RSS groups of THIS ticker's market (§4.4): a TASE name pulls en_il+he_il
    # (EN primary, HE fallback), a US name pulls en_us — never the other market's
    # local press. The group name is `<lang>_<region>`, so the feed's language is
    # its prefix; rss.fetch_rss carries that through per group rather than
    # detecting it, and it routes FinBERT vs DictaBERT downstream (§3.1).
    for group in market_cfg.get("rss_feed_groups") or []:
        urls = feeds.get(group) or []
        if not urls:
            continue
        lang = str(group).split("_", 1)[0]
        items, feed_errors = rss.fetch_rss(urls, lang, window)
        raw_items.extend(items)
        errors.extend(feed_errors)

    cleaned = _clean(raw_items, terms)
    n_en = sum(1 for i in cleaned if i["language"] == "en")
    n_he = sum(1 for i in cleaned if i["language"] == "he")

    summary = (
        f"{len(cleaned)} items after cleaning: {n_en} EN, {n_he} HE "
        f"(window {window}m)."
    )
    if derived:
        # Say so: a derived term is weaker than a hand-tuned one, so thin or
        # off-target coverage here is explainable rather than mysterious (§4.4).
        summary += f" Search term derived from the SEC registrant name: {terms[0]!r}."
    # Degrade only when a source failed AND coverage may be incomplete because of
    # it. All sources healthy with zero items is genuine "no coverage" (§13).
    if errors:
        summary = f"degraded: {'; '.join(errors)} — {summary}"

    return {
        "ticker": req.ticker,
        "items": cleaned,
        "few_shot": _load_fewshot(),
        "summary": summary,
    }


class NewsStoreRequest(BaseModel):
    ticker: str
    items: List[Dict[str, Any]]


@router.post("/news/store")
def news_store_endpoint(req: NewsStoreRequest):
    con = cache.connect()
    try:
        stored = news_store.upsert_news(con, req.ticker, req.items)
    finally:
        con.close()
    return {"stored": stored}
