"""POST /ohlc — cached daily OHLC for a symbol (§5).

Reads the DuckDB `prices` cache (§4.2) written by Step-1 ingestion, lazily
ingesting on a cache miss (see `cache.get_cached_ohlc`). Serves the last
`lookback_days` daily bars in the §5 contract shape. Intraday intervals are out
of scope (Step 1 caches daily only); a non-`1d` interval returns an empty result
rather than crashing.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache

router = APIRouter()


class OHLCRequest(BaseModel):
    symbol: str
    lookback_days: int = 180
    interval: str = "1d"


@router.post("/ohlc")
def ohlc(req: OHLCRequest):
    if req.interval != "1d":
        return {
            "symbol": req.symbol,
            "interval": req.interval,
            "as_of": None,
            "candles": [],
            "summary": f"interval '{req.interval}' not supported; only daily (1d) is cached.",
        }

    con = cache.connect()
    try:
        df, degrade_reason = cache.get_cached_ohlc(
            con, req.symbol, req.lookback_days
        )
    finally:
        con.close()

    if df.empty:
        return {
            "symbol": req.symbol,
            "interval": "1d",
            "as_of": None,
            "candles": [],
            "summary": f"degraded: {degrade_reason or 'no cached data'} — no bars available.",
        }

    candles = [
        {
            "ts": ts.strftime("%Y-%m-%d"),
            "o": round(float(o), 4),
            "h": round(float(h), 4),
            "l": round(float(low), 4),
            "c": round(float(c), 4),
            "v": int(v),
        }
        for ts, o, h, low, c, v in zip(
            df["ts"], df["open"], df["high"], df["low"], df["close"], df["volume"],
            strict=True,  # columns of one frame: unequal lengths would be a real bug
        )
    ]
    as_of = candles[-1]["ts"]
    return {
        "symbol": req.symbol,
        "interval": "1d",
        "as_of": as_of,
        "candles": candles,
        "summary": f"{len(candles)} daily bars cached.",
    }
