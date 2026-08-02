"""RSS news fetch (Globes EN, Ynet + Globes HE) (§4.1, §3.1).

RSS feeds have no search, so each configured feed is pulled whole and its recent
items are returned to `/news/fetch`, which applies the §4.3 term-in-text and
dedupe cleaning uniformly across RSS and NewsAPI. English feeds are primary;
Hebrew feeds are the §3.1 fallback (the LLM translates inline downstream).

`language` is carried per feed group (from `config/universe.yaml.rss_feeds`)
rather than detected, since a feed's language is known up front. A feed that
fails to fetch is recorded as an error and skipped; a partially-reachable set of
feeds still returns the items it got (the caller degrades only if *nothing* is
reachable) (§9.4).
"""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import feedparser
import httpx

from data import tls


def _entry_published_utc(entry) -> Optional[datetime]:
    """Best-effort UTC publish time from a feedparser entry, else None.

    feedparser normalizes `pubDate`/`updated` into a UTC `struct_time`; we treat
    a missing/unparseable date as unknown (the item is dropped when a finite
    window is requested, so stale-dated feeds cannot leak in).
    """
    for attr in ("published_parsed", "updated_parsed"):
        st = getattr(entry, attr, None)
        if st:
            return datetime.fromtimestamp(timegm(st), tz=timezone.utc)
    return None


def _fetch_one(url: str, language: str, cutoff: Optional[datetime]) -> List[dict]:
    """Fetch and normalize one feed's recent items (raises on transport error)."""
    resp = httpx.get(
        url,
        timeout=20,
        follow_redirects=True,
        verify=tls.ssl_context(),
        headers={"User-Agent": "Mozilla/5.0 (ta35-quant-service)"},
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    feed_source = (parsed.feed.get("title") or url).strip()

    items: List[dict] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        headline = (entry.get("title") or "").strip()
        if not link or not headline:
            continue
        published = _entry_published_utc(entry)
        if cutoff is not None:
            if published is None or published < cutoff:
                continue
        items.append(
            {
                "headline": headline,
                "summary": (entry.get("summary") or "").strip(),
                "source": feed_source,
                "url": link,
                "published_at": published.strftime("%Y-%m-%dT%H:%M:%SZ")
                if published
                else None,
                "language": language,
            }
        )
    return items


def fetch_rss(
    feed_urls: List[str],
    language: str,
    window_minutes: int,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], List[str]]:
    """Fetch recent items from a group of same-language feeds.

    Returns ``(items, errors)``: `items` are normalized (same shape as
    `newsapi.fetch_newsapi`) and within the window; `errors` lists per-feed
    failure strings so the caller can report degradation without discarding the
    feeds that did succeed.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = None
    if window_minutes and window_minutes > 0:
        cutoff = datetime.fromtimestamp(
            now.timestamp() - window_minutes * 60, tz=timezone.utc
        )

    items: List[dict] = []
    errors: List[str] = []
    for url in feed_urls:
        try:
            items.extend(_fetch_one(url, language, cutoff))
        except Exception as exc:  # noqa: BLE001 - skip a bad feed, keep the rest
            errors.append(f"{url}: {exc}")
    return items, errors
