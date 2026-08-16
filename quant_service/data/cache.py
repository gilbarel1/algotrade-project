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


def replace_prices_window(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Make `df` the authoritative content of the span it covers, then upsert.

    `upsert_prices` alone is insert-or-replace by `(symbol, ts)`: it can correct a
    row and add a row, but it can never *retract* one. So a bar that a previous
    cleaning run wrote and the current one no longer produces survives every
    re-ingest, and the cache keeps serving it.

    That is not hypothetical. The §4.3 session grid used to be the configured
    trading week, which forward-filled a synthetic Sunday into every TASE series
    (see `yahoo.clean_ohlc`). Fixing the grid stopped *new* phantoms, but the old
    ones stayed cached and kept feeding `/ohlc` and `/indicators` — the bug
    outlived its own fix. Deleting the covered span first makes a re-ingest
    self-healing, which is also what "idempotent" should have meant all along.

    Only the `[min(ts), max(ts)]` span of `df` is cleared, so a narrow re-ingest
    cannot drop history outside the window it fetched. An empty frame deletes
    nothing: a degraded fetch must never empty the cache (§9.4).
    """
    if df.empty:
        return 0

    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"prices frame missing columns: {missing}")

    symbols = df["symbol"].unique()
    for symbol in symbols:
        span = df.loc[df["symbol"] == symbol, "ts"]
        con.execute(
            "DELETE FROM prices WHERE symbol = ? AND ts BETWEEN ? AND ?",
            [symbol, span.min(), span.max()],
        )
    return upsert_prices(con, df)


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


def _coverage_slack_days(symbol: str) -> int:
    """Days of slack allowed when judging whether a cache covers the window.

    The slack absorbs the fact that the earliest trading day on/after a calendar
    cutoff can fall a few days later (the market's weekend, plus holiday
    clustering around it), so it is derived from the symbol's own market: one day
    per closed weekday plus one for a holiday abutting the weekend. Both markets
    currently have two-day weekends, so this is 3 either way — the point is that
    a market with a different weekend gets the right number rather than the
    Israeli one. It stays far smaller than the gaps we must detect (e.g. a
    120-day cache asked for 180 days), so a genuine shortfall still re-ingests.
    """
    from data import markets  # local import: mirrors the yahoo import below

    return len(markets.closed_weekdays(markets.market(symbol))) + 1


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
    if not _covers(df, lookback_days, _coverage_slack_days(symbol)):
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


def _covers(df: pd.DataFrame, lookback_days: int, slack_days: int) -> bool:
    """True if the cached rows span at least the requested calendar window.

    Measured as the cache's own calendar span (`max_ts - min_ts`) rather than a
    row count, because trading sessions are sparser than calendar days in every
    market. A symbol genuinely younger than `lookback_days` can never satisfy
    this and will re-ingest each time — harmless for a watchlist of long-listed
    names, and it never yields wrong data, only an occasional redundant fetch.
    """
    if df.empty:
        return False
    if lookback_days <= 0:
        return True
    span_days = (df["ts"].max() - df["ts"].min()).days
    return span_days >= lookback_days - slack_days


def _slice_lookback(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """Keep rows within the last `lookback_days` calendar days of the newest bar."""
    if df.empty or lookback_days <= 0:
        return df
    newest = df["ts"].max()
    cutoff = newest - pd.Timedelta(days=lookback_days)
    return df[df["ts"] >= cutoff].reset_index(drop=True)


def _empty_ohlc_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS)
