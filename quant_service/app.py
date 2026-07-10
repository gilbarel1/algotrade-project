"""Quant service (FastAPI) — §5 of docs/design.md.

Local FastAPI app exposing the four endpoints (/ohlc, /indicators, /sentiment, /report).
In Step 0 every endpoint returns a hardcoded stub matching the §5 contract exactly;
real implementations land in later build steps.

Run: uvicorn app:app --port 8000
"""

from fastapi import FastAPI

from routers import (
    ohlc,
    indicators,
    sentiment,
    news,
    earnings,
    report,
    validate,
    riskmanager,
)

app = FastAPI(
    title="TA-35 Quant Service",
    description="Indicators, sentiment, and PDF rendering for the n8n investment team (§5).",
    version="0.0.0",
)

app.include_router(ohlc.router)
app.include_router(indicators.router)
app.include_router(sentiment.router)
app.include_router(news.router)
app.include_router(earnings.router)
app.include_router(report.router)
app.include_router(validate.router)
app.include_router(riskmanager.router)


@app.get("/health")
def health():
    """Liveness check."""
    return {"status": "ok"}
