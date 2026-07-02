"""OHLC ingestion CLI for the watchlist (Step 1 — Milestone A part 1).

Reads the watchlist and lookback from `config/universe.yaml` (§4.4), pulls
adjusted daily OHLC from Yahoo Finance (§4.1), applies the §4.3 cleaning rules,
and writes the cleaned rows into the DuckDB `prices` cache (§4.2). Idempotent:
re-running replaces rows by (symbol, ts).

Usage (from quant_service/, with the venv active):
    python -m data.ingest                      # full watchlist, lookback from config
    python -m data.ingest --symbols TEVA.TA    # one or more explicit symbols
    python -m data.ingest --lookback-days 90   # override the lookback
"""

from __future__ import annotations

import argparse
import os
import time

import yaml

from . import cache, yahoo

# data/ingest.py → up two levels (data/, quant_service/) reaches the repo root.
CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config",
    "universe.yaml",
)


def load_universe() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> int:
    universe = load_universe()
    parser = argparse.ArgumentParser(description="Ingest watchlist OHLC into DuckDB.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=universe["watchlist"],
        help="Symbols to ingest (default: config watchlist).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=universe.get("ohlc_lookback_days", 180),
        help="Lookback window in days (default: config ohlc_lookback_days).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.0,
        help="Seconds to pause between symbols (politeness vs. rate limits).",
    )
    args = parser.parse_args()

    db_path = cache.get_db_path()
    print(f"DuckDB: {db_path}")
    print(f"Lookback: {args.lookback_days}d   Symbols: {', '.join(args.symbols)}\n")

    con = cache.connect()
    degraded = 0
    try:
        header = f"{'symbol':<10}{'fetched':>8}{'written':>8}{'filled':>7}{'dropped':>8}  range"
        print(header)
        print("-" * len(header))
        for i, symbol in enumerate(args.symbols):
            if i:
                time.sleep(args.sleep)
            res = yahoo.ingest_symbol(con, symbol, args.lookback_days)
            if res.status != "ok":
                degraded += 1
                print(f"{symbol:<10}  DEGRADED — {res.note}")
                continue
            rng = f"{res.start} … {res.end}" if res.start else "-"
            print(
                f"{res.symbol:<10}{res.rows_fetched:>8}{res.rows_written:>8}"
                f"{res.sessions_filled:>7}{res.sessions_dropped:>8}  {rng}"
            )
            if res.outliers:
                print(f"           outliers (>8x MAD return): {', '.join(res.outliers)}")
    finally:
        con.close()

    print(f"\nTotal in prices: {count_all()} rows.")
    if degraded:
        print(f"{degraded} symbol(s) degraded (no data fabricated).")
    return 1 if degraded == len(args.symbols) else 0


def count_all() -> int:
    con = cache.connect()
    try:
        return cache.count_prices(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
