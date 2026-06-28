"""DuckDB cache helpers for the `prices` table (§4.2, §4.3).

Thin persistence layer used by the Yahoo ingestion (§4.1). The schema itself is
created by `store_init.py`; these helpers only read/write the `prices` cache.

The `prices` schema (§4.2) is fixed and must not gain columns:
    prices(symbol TEXT, ts TIMESTAMP, open, high, low, close DOUBLE,
           volume BIGINT, source TEXT, PRIMARY KEY(symbol, ts))
"""

from __future__ import annotations

import os

import duckdb
import pandas as pd

# store.duckdb lives at quant_service/store.duckdb (one level up from data/).
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "store.duckdb")

PRICE_COLUMNS = ["symbol", "ts", "open", "high", "low", "close", "volume", "source"]


def get_db_path(db_path: str | None = None) -> str:
    """Resolve the DuckDB path: explicit arg > DUCKDB_PATH env > default."""
    return db_path or os.environ.get("DUCKDB_PATH") or DEFAULT_DB_PATH


def connect(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection at the resolved path."""
    return duckdb.connect(get_db_path(db_path))


def upsert_prices(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Insert-or-replace cleaned price rows keyed by (symbol, ts).

    `df` must have exactly the §4.2 `prices` columns. Re-ingesting the same
    symbol replaces existing rows rather than duplicating them, so the step is
    idempotent and safe to re-run.
    """
    if df.empty:
        return 0

    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"prices frame missing columns: {missing}")

    ordered = df[PRICE_COLUMNS]
    con.register("incoming_prices", ordered)
    try:
        con.execute(
            """
            INSERT OR REPLACE INTO prices
                (symbol, ts, open, high, low, close, volume, source)
            SELECT symbol, ts, open, high, low, close, volume, source
            FROM incoming_prices
            """
        )
    finally:
        con.unregister("incoming_prices")
    return len(ordered)


def read_prices(con: duckdb.DuckDBPyConnection, symbol: str) -> pd.DataFrame:
    """Return all cached rows for a symbol, oldest first."""
    return con.execute(
        "SELECT symbol, ts, open, high, low, close, volume, source "
        "FROM prices WHERE symbol = ? ORDER BY ts",
        [symbol],
    ).df()


def count_prices(con: duckdb.DuckDBPyConnection, symbol: str | None = None) -> int:
    """Row count for a symbol, or the whole table when symbol is None."""
    if symbol is None:
        return con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM prices WHERE symbol = ?", [symbol]
    ).fetchone()[0]
