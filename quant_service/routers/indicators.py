"""POST /indicators — RSI, MACD, Bollinger, ATR from cached OHLC (§5).

Step 0: returns the hardcoded §5 stub. Real pandas-ta implementation lands in Step 2.
"""

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class IndicatorsRequest(BaseModel):
    symbol: str
    lookback_days: int = 120
    indicators: List[str] = ["rsi", "macd", "bbands", "atr"]


@router.post("/indicators")
def indicators(req: IndicatorsRequest):
    return {
        "symbol": "TEVA.TA",
        "as_of": "2026-06-22",
        "indicators": {
            "rsi_14": 62.1,
            "macd": {"macd": 1.2, "signal": 0.9, "hist": 0.3},
            "bbands": {"upper": 34.1, "mid": 31.0, "lower": 27.9, "pct_b": 0.71},
            "atr_14": 0.85,
        },
        "summary": "Momentum building; near upper Bollinger.",
    }
