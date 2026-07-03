"""Pydantic schemas for the Technical Agent LLM boundary (§3.3, §9.4).

Two models:

- ``TechnicalNarration`` — the LLM boundary. Gemini Flash-Lite is only asked to
  narrate: it returns ``{signal, summary}``. The indicator numbers come from
  ``/indicators`` deterministically and never round-trip through the model, so
  the LLM cannot alter or fabricate them. ``POST /validate`` (agent
  ``"technical"``) validates against this model; a malformed response triggers
  the sub-workflow's single stricter retry, then a ``degraded`` result.

- ``TechnicalAgentOutput`` — the full §3.3 sub-workflow return (plus the §3.4
  canonical ``status`` field every sub-workflow carries). Used by the Step 11
  evaluation harness and available for callers that want to check the whole
  assembled payload.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# §3.4 canonical enums.
Signal = Literal[
    "bullish_momentum", "bearish_momentum", "overbought", "oversold", "neutral"
]
Status = Literal["ok", "degraded"]


class TechnicalNarration(BaseModel):
    """What Flash-Lite must return: a signal from the §3.4 enum and a short
    plain-English reading. Extra keys are rejected so the model cannot smuggle
    fields (e.g. its own indicator values) past the boundary."""

    model_config = ConfigDict(extra="forbid")

    signal: Signal
    summary: str = Field(min_length=1, max_length=500)


class MacdBlock(BaseModel):
    macd: Optional[float] = None
    signal: Optional[float] = None
    hist: Optional[float] = None


class BbandsBlock(BaseModel):
    upper: Optional[float] = None
    mid: Optional[float] = None
    lower: Optional[float] = None
    pct_b: Optional[float] = None


class IndicatorBlock(BaseModel):
    """§3.3 / §5 indicator values; null when history is insufficient."""

    rsi_14: Optional[float] = None
    macd: MacdBlock = MacdBlock()
    bbands: BbandsBlock = BbandsBlock()
    atr_14: Optional[float] = None


class TechnicalAgentOutput(BaseModel):
    """Full §3.3 output shape returned by the Technical sub-workflow."""

    ticker: str
    as_of: Optional[str] = None  # YYYY-MM-DD; null when the cache is empty
    indicators: IndicatorBlock
    signal: Signal
    summary: str
    status: Status
