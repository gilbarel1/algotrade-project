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


def top_scored(con: duckdb.DuckDBPyConnection, symbol: str, limit: int = 5) -> List[dict]:
    """Return a symbol's most salient scored articles for the report (§8.1).

    Used by the PDF renderer's Sentiment panel citation block. "Salient" =
    strongest signal first: ordered by the larger absolute of the two stored
    scores (LLM / model), then most recent. Returns compact citation rows
    (headline, url, source, language, both scores) — never article bodies.
    """
    rows = con.execute(
        """
        SELECT headline, url, source, language,
               llm_sentiment, model_sentiment, disagreement, published_at
        FROM news WHERE symbol = ?
        ORDER BY GREATEST(
                     COALESCE(ABS(llm_sentiment), 0),
                     COALESCE(ABS(model_sentiment), 0)
                 ) DESC,
                 published_at DESC NULLS LAST
        LIMIT ?
        """,
        [symbol, limit],
    ).fetchall()
    out: List[dict] = []
    for r in rows:
        published_at = r[7]
        out.append(
            {
                "headline": r[0],
                "url": r[1],
                "source": r[2],
                "language": r[3],
                "llm_score": r[4],
                "model_score": r[5],
                "disagreement": r[6],
                "published_at": published_at.isoformat()
                if published_at is not None
                else None,
            }
        )
    return out
