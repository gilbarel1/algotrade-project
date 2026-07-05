"""NewsAPI English news fetch (§4.1, §3.1).

Queries NewsAPI `/v2/everything` for a ticker's company terms over a recent
window and returns compact, source-neutral items for `/news/fetch` to clean and
merge with the RSS items. English coverage only — Hebrew comes from the RSS
fallback (§3.1).

The key comes from `NEWSAPI_API_KEY` (§11.1). A rate-limit or HTTP failure
raises `NewsAPIError` so the caller can degrade the affected source rather than
fabricate or silently drop it (§9.4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from data import tls

NEWSAPI_URL = "https://newsapi.org/v2/everything"
# NewsAPI caps the free-tier `from` at ~1 month back; keep the request bounded.
_MAX_WINDOW_MINUTES = 30 * 24 * 60


class NewsAPIError(RuntimeError):
    """Raised on a NewsAPI transport error or a non-``ok`` API status."""


def _query(terms: List[str]) -> str:
    """OR the company terms into a single NewsAPI query, each phrase quoted."""
    return " OR ".join(f'"{t}"' for t in terms if t.strip())


def fetch_newsapi(
    terms: List[str],
    window_minutes: int,
    api_key: str,
    *,
    page_size: int = 50,
    now: Optional[datetime] = None,
) -> List[dict]:
    """Return recent English NewsAPI items matching `terms` within the window.

    Each item: ``{headline, summary, source, url, published_at (UTC ISO),
    language: "en"}``. `published_at` is left as NewsAPI's UTC ISO string. The
    caller applies the §4.3 dedupe / term-in-text cleaning; this function only
    fetches and normalizes shape.
    """
    if not terms:
        return []
    if not api_key:
        raise NewsAPIError("NEWSAPI_API_KEY is not set")

    now = now or datetime.now(timezone.utc)
    window = max(1, min(int(window_minutes), _MAX_WINDOW_MINUTES))
    since = (now - timedelta(minutes=window)).strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "q": _query(terms),
        "from": since,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max(1, min(page_size, 100)),
        "searchIn": "title,description",
        "apiKey": api_key,
    }

    try:
        resp = httpx.get(
            NEWSAPI_URL,
            params=params,
            timeout=20,
            verify=tls.ssl_context(),
            headers={"User-Agent": "ta35-quant-service/1.0"},
        )
    except httpx.HTTPError as exc:  # transport failure -> degrade (§9.4)
        raise NewsAPIError(f"NewsAPI request failed: {exc}") from exc

    if resp.status_code != 200:
        # NewsAPI returns JSON {status:"error", code, message} on 4xx/429.
        try:
            body = resp.json()
            detail = f" ({body.get('code')}: {body.get('message')})"
        except Exception:  # noqa: BLE001 - body may not be JSON
            detail = f" (HTTP {resp.status_code})"
        raise NewsAPIError(f"NewsAPI returned {resp.status_code}{detail}")

    body = resp.json()
    if body.get("status") != "ok":
        raise NewsAPIError(
            f"NewsAPI status={body.get('status')} "
            f"({body.get('code')}: {body.get('message')})"
        )

    items: List[dict] = []
    for art in body.get("articles", []):
        url = (art.get("url") or "").strip()
        headline = (art.get("title") or "").strip()
        if not url or not headline:
            continue
        source = (art.get("source") or {}).get("name") or "NewsAPI"
        items.append(
            {
                "headline": headline,
                "summary": (art.get("description") or "").strip(),
                "source": source,
                "url": url,
                "published_at": art.get("publishedAt"),
                "language": "en",
            }
        )
    return items
