"""POST /indicators — RSI, MACD, Bollinger, ATR from cached OHLC (§5, §3.3).

Reads the DuckDB `prices` cache (lazily ingesting on a miss via
`cache.get_cached_ohlc`) and computes the requested indicators with `pandas-ta`
(`indicators.calc`). Returns the §5 contract shape; an indicator without enough
history yields a `null` value rather than a crash.
"""

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache
from indicators import calc

router = APIRouter()


class IndicatorsRequest(BaseModel):
    symbol: str
    lookback_days: int = 120
    indicators: List[str] = ["rsi", "macd", "bbands", "atr"]


@router.post("/indicators")
def indicators(req: IndicatorsRequest):
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
            "as_of": None,
            "indicators": {},
            "summary": f"degraded: {degrade_reason or 'no cached data'} — no indicators computed.",
        }

    values = calc.compute_indicators(df, req.indicators)
    as_of = df["ts"].iloc[-1].strftime("%Y-%m-%d")

    short = calc.insufficient(len(df), req.indicators)
    if short:
        summary = (
            f"{len(df)} bars; insufficient history for {', '.join(short)} "
            f"(value null)."
        )
    else:
        summary = f"Indicators computed from {len(df)} daily bars."

    return {
        "symbol": req.symbol,
        "as_of": as_of,
        "indicators": values,
        "summary": summary,
    }
