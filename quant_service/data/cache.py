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


# A cache is treated as covering the requested window if its calendar span is
# within this many days of `lookback_days`. The slack absorbs the fact that the
# earliest trading day on/after a calendar cutoff can fall a few days later
# (weekend + TASE holiday clustering); it is far smaller than the gaps we must
# detect (e.g. a 120-day cache asked for 180 days), so a genuine shortfall still
# triggers a re-ingest.
COVERAGE_SLACK_DAYS = 3


def get_cached_ohlc(
    con: duckdb.DuckDBPyConnection, symbol: str, lookback_days: int
) -> tuple[pd.DataFrame, str | None]:
    """Read cached OHLC for a symbol, ingesting when the cache can't cover it (§5, §4.1).

    The `/ohlc` and `/indicators` endpoints are described in §5 as serving cached
    OHLC, but the §6.1 orchestrator flow has no explicit ingest step and the
    Technical agent (§3.3, §6.2) picks `lookback_days` per task — so the cached
    window for a symbol is whatever the *first* request happened to ask for. This
    helper makes the endpoints self-sufficient and order-independent: it
    (re-)ingests via the Step-1 Yahoo ingester (`yahoo.ingest_symbol`) whenever
    the cache is empty **or its span is shorter than the requested window**, then
    re-reads. Requests can therefore widen a previously-narrow cache; a later
    narrower request just slices it down. A genuine fetch failure never fabricates
    bars: it returns whatever is cached (if anything) with a degrade reason, else
    an empty frame plus the reason (§9.4).

    Returns `(df, degrade_reason)`. `df` has the §4.2 `prices` columns, oldest
    first, sliced to the most recent `lookback_days` calendar days. `degrade_reason`
    is None on success, else a short human-readable string (with `df` possibly
    holding stale/partial cached rows, or empty if nothing is cached).
    """
    from data import yahoo  # local import: avoids a yahoo→cache import cycle

    df = read_prices(con, symbol)
    degrade_reason: str | None = None
    if not _covers(df, lookback_days):
        res = yahoo.ingest_symbol(con, symbol, lookback_days)
        if res.status == "ok":
            df = read_prices(con, symbol)
        elif df.empty:
            # nothing cached and the fetch failed — no data at all (§9.4).
            return _empty_ohlc_frame(), res.note or "ingest degraded"
        else:
            # keep the (narrower/stale) cache we already have, but flag it.
            degrade_reason = res.note or "ingest degraded; serving cached span"

    df = _slice_lookback(df, lookback_days)
    return df, degrade_reason


def _covers(df: pd.DataFrame, lookback_days: int) -> bool:
    """True if the cached rows span at least the requested calendar window.

    Measured as the cache's own calendar span (`max_ts - min_ts`) rather than a
    row count, because TASE sessions are sparser than calendar days. A symbol
    genuinely younger than `lookback_days` can never satisfy this and will
    re-ingest each time — harmless for the TA-35 watchlist (all long-listed), and
    it never yields wrong data, only an occasional redundant fetch.
    """
    if df.empty:
        return False
    if lookback_days <= 0:
        return True
    span_days = (df["ts"].max() - df["ts"].min()).days
    return span_days >= lookback_days - COVERAGE_SLACK_DAYS


def _slice_lookback(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """Keep rows within the last `lookback_days` calendar days of the newest bar."""
    if df.empty or lookback_days <= 0:
        return df
    newest = df["ts"].max()
    cutoff = newest - pd.Timedelta(days=lookback_days)
    return df[df["ts"] >= cutoff].reset_index(drop=True)


def _empty_ohlc_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS)
