"""DuckDB writer for the `earnings` table (§4.2, §3.2).

`/earnings/store` calls this to persist the Earnings Agent's classified
disclosure with its self-consistency extraction. n8n cannot open DuckDB, so the
write is served over HTTP — same idiom as `news_store.py`. Keyed by the item
`id` (sha1 of "symbol|url", the §4.3 dedupe key), so re-running a ticker
replaces rather than duplicates.

The §4.2 `earnings` schema is fixed:
    earnings(id TEXT PK, symbol TEXT, published_at TIMESTAMP, language TEXT,
             title TEXT, url TEXT, kind TEXT, materiality TEXT, summary TEXT,
             extracted JSON)
"""

from __future__ import annotations

import json
from typing import List

import duckdb

_INSERT = """
INSERT OR REPLACE INTO earnings
    (id, symbol, published_at, language, title, url, kind,
     materiality, summary, extracted)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def upsert_earnings(
    con: duckdb.DuckDBPyConnection, symbol: str, rows: List[dict]
) -> int:
    """Insert-or-replace classified disclosure rows for a symbol. Returns rows written.

    Each row carries `id`, `published_at` (ISO string or None), `language`,
    `title`, `url`, `kind`, `materiality`, `summary`, and `extracted` (the
    per-figure `{value, confidence}` dict). A degraded agent may legitimately
    send None for the classified fields — stored as SQL NULL, never guessed.
    """
    written = 0
    for row in rows:
        rid = row.get("id")
        url = row.get("url")
        if not rid or not url:
            continue  # id/url are the identity; skip malformed rows
        extracted = row.get("extracted")
        con.execute(
            _INSERT,
            [
                rid,
                symbol,
                row.get("published_at"),  # ISO string -> TIMESTAMP cast, or NULL
                row.get("language"),
                row.get("title"),
                url,
                row.get("kind"),
                row.get("materiality"),
                row.get("summary"),
                json.dumps(extracted, ensure_ascii=False)
                if extracted is not None
                else None,
            ],
        )
        written += 1
    return written
