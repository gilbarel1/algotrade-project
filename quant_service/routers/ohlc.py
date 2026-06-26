"""POST /ohlc — cached daily/intraday OHLC for a symbol (§5).

Step 0: returns the hardcoded §5 stub. Real Yahoo Finance ingestion lands in Steps 1-2.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class OHLCRequest(BaseModel):
    symbol: str
    lookback_days: int = 180
    interval: str = "1d"


@router.post("/ohlc")
def ohlc(req: OHLCRequest):
    return {
        "symbol": "TEVA.TA",
        "interval": "1d",
        "as_of": "2026-06-22",
        "candles": [
            {"ts": "2026-06-20", "o": 31.1, "h": 31.6, "l": 30.9, "c": 31.4, "v": 1234567}
        ],
        "summary": "180 daily bars cached.",
    }
