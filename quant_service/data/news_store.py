"""DuckDB writer for the `news` table (§4.2, §3.1).

`/news/store` calls this to persist the Sentiment Agent's per-article dual
scores. n8n cannot open DuckDB, so the write is served over HTTP. Follows the
`cache.py` connect / `INSERT OR REPLACE` idiom; keyed by the article `id`
(sha1 of the url) so re-running a ticker replaces rather than duplicates.

The §4.2 `news` schema is fixed:
    news(id TEXT PK, symbol TEXT, published_at TIMESTAMP, headline TEXT,
         url TEXT, source TEXT, language TEXT, llm_sentiment DOUBLE,
         model_sentiment DOUBLE, disagreement DOUBLE, raw JSON)

The per-article LLM and model scores land in `llm_sentiment` / `model_sentiment`
(the columns are per-row here; the sub-workflow separately reports the aggregate
in its §3.1 output).
"""

from __future__ import annotations

import json
from typing import List

import duckdb

_INSERT = """
INSERT OR REPLACE INTO news
    (id, symbol, published_at, headline, url, source, language,
     llm_sentiment, model_sentiment, disagreement, raw)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _num(value):
    """Coerce a score to float, or None when absent (e.g. degraded LLM side)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_news(con: duckdb.DuckDBPyConnection, symbol: str, rows: List[dict]) -> int:
    """Insert-or-replace scored news rows for a symbol. Returns rows written.

    Each row carries `id`, `headline`, `url`, `source`, `language`,
    `published_at` (ISO string or None), `llm_score`, `model_score`,
    `disagreement`, and the original `raw` item. Missing scores persist as SQL
    NULL rather than a fabricated 0.
    """
    written = 0
    for row in rows:
        rid = row.get("id")
        url = row.get("url")
        if not rid or not url:
            continue  # id/url are the identity; skip malformed rows
        con.execute(
            _INSERT,
            [
                rid,
                symbol,
                row.get("published_at"),  # ISO string -> TIMESTAMP cast, or NULL
                row.get("headline"),
                url,
                row.get("source"),
                row.get("language"),
                _num(row.get("llm_score")),
                _num(row.get("model_score")),
                _num(row.get("disagreement")),
                json.dumps(row.get("raw", row), ensure_ascii=False),
            ],
        )
        written += 1
    return written
