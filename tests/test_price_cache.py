"""`prices` cache writes: a re-ingest must be able to retract, not only add (§4.2, §4.3).

`upsert_prices` is insert-or-replace by `(symbol, ts)`. It can correct a row and
add a row but never remove one, so any bar a previous cleaning run produced
survives every later re-ingest. That let the §4.3 synthetic-Sunday bug outlive
its own fix: the grid was corrected, yet the already-cached phantoms kept feeding
`/ohlc` and `/indicators`.

`replace_prices_window` makes a re-ingest authoritative for the span it covers.
These pin the three properties that has to hold: stale rows inside the span go,
history outside the span stays, and a degraded (empty) fetch never empties the
cache.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from data import cache

COLS = cache.PRICE_COLUMNS


@pytest.fixture
def con():
    """In-memory DuckDB with just the §4.2 `prices` table."""
    c = duckdb.connect(":memory:")
    c.execute(
        """
        CREATE TABLE prices (
            symbol TEXT, ts TIMESTAMP,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume BIGINT, source TEXT,
            PRIMARY KEY (symbol, ts)
        )
        """
    )
    yield c
    c.close()


def _rows(symbol: str, dates: list[str], close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "ts": pd.Timestamp(d),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1_000, "source": "yahoo",
            }
            for d in dates
        ]
    )[COLS]


def _dates(con, symbol: str) -> list[str]:
    df = cache.read_prices(con, symbol)
    return [str(ts.date()) for ts in pd.DatetimeIndex(df["ts"])]


def test_upsert_alone_cannot_retract_a_stale_bar(con):
    """Documents *why* replace_prices_window exists — the old write path's limit."""
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-19", "2026-07-20"]))
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-20"]))
    # 07-19 (the phantom) is still there: insert-or-replace never deletes.
    assert "2026-07-19" in _dates(con, "TEVA.TA")


def test_replace_window_retracts_a_bar_the_new_clean_no_longer_produces(con):
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-19", "2026-07-20"]))
    cache.replace_prices_window(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-20"]))
    assert _dates(con, "TEVA.TA") == ["2026-07-16", "2026-07-20"]


def test_replace_window_keeps_history_outside_the_fetched_span(con):
    """A narrow re-ingest must not delete the older history it did not fetch."""
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-01-05", "2026-07-16", "2026-07-20"]))
    cache.replace_prices_window(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-20"]))
    assert "2026-01-05" in _dates(con, "TEVA.TA")


def test_replace_window_with_an_empty_frame_deletes_nothing(con):
    """A degraded fetch returns an empty frame; it must never empty the cache (§9.4)."""
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-20"]))
    assert cache.replace_prices_window(con, pd.DataFrame(columns=COLS)) == 0
    assert _dates(con, "TEVA.TA") == ["2026-07-16", "2026-07-20"]


def test_replace_window_does_not_touch_other_symbols(con):
    cache.upsert_prices(con, _rows("AAPL", ["2026-07-16", "2026-07-17"]))
    cache.replace_prices_window(con, _rows("TEVA.TA", ["2026-07-16", "2026-07-20"]))
    assert _dates(con, "AAPL") == ["2026-07-16", "2026-07-17"]


def test_replace_window_updates_values_in_place(con):
    cache.upsert_prices(con, _rows("TEVA.TA", ["2026-07-16"], close=100.0))
    cache.replace_prices_window(con, _rows("TEVA.TA", ["2026-07-16"], close=250.0))
    df = cache.read_prices(con, "TEVA.TA")
    assert len(df) == 1
    assert df["close"].iloc[0] == 250.0


def test_replace_window_rejects_a_frame_missing_columns(con):
    with pytest.raises(ValueError, match="missing columns"):
        cache.replace_prices_window(con, pd.DataFrame({"symbol": ["X"], "ts": [pd.Timestamp("2026-07-16")]}))
