"""Pydantic schemas for the Risk Manager three-stage critique loop (§3.4, §6.3, §9.4).

The Risk Manager runs three sequential LLM passes, each a validated boundary
(``POST /validate``): draft → devil's-advocate critique → final. Mirroring the
`schemas/technical.py` convention, every LLM-boundary model sets
``extra="forbid"`` so the model cannot smuggle fabricated fields past the
boundary, uses the §3.4 canonical ``Literal`` enums, and bounds free text.

Four models:

- ``RiskDraft`` (agent ``"risk_draft"``) — the first pass. Returns the initial
  ``{recommendation, conviction, rationale}`` plus ``earnings_direction``. The
  extra ``earnings_direction`` field exists because §3.4's earnings directional
  mapping is *LLM-classified* ("positive surprise / raised guidance" → bullish):
  sentiment and technical directions are computed deterministically from the
  rubric, but earnings direction requires the model to read the disclosure. Put
  in the draft output, it stays auditable and lets the sub-workflow recompute
  the agreement count mechanically for the final-pass clamp.

- ``RiskCritique`` (agent ``"risk_critique"``) — the devil's-advocate pass.
  Argues the opposite case: a counter-recommendation, concrete objections, and a
  challenge to the draft's conviction.

- ``RiskFinal`` (agent ``"risk_final"``) — the final pass. Returns the committed
  ``{recommendation, conviction, rationale}``; the rationale must state how each
  critique objection was incorporated or dismissed and reference any applied cap.

- ``RiskManagerOutput`` — the full §6.3 sub-workflow return (plus the §3.4
  canonical ``status`` every sub-workflow carries). Used by the Step 11
  evaluation harness and by callers validating the whole assembled payload; the
  three passes are optional so a ``degraded`` result (a pass failed validation
  twice, or the context fetch failed) still validates without fabricated passes.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# §3.4 canonical enums, shared shape with the other agents.
Recommendation = Literal["long", "short", "hold", "avoid"]
Conviction = Literal["low", "medium", "high"]
Direction = Literal["bullish", "bearish", "neutral"]
Status = Literal["ok", "degraded"]


class RiskDraft(BaseModel):
    """Draft pass (§3.4 step 1). Extra keys rejected so the model cannot smuggle
    fields past the boundary. ``earnings_direction`` is the LLM's classification
    of the earnings signal (the one directional input §3.4 leaves to the model),
    used by the sub-workflow to recompute the agreement count deterministically."""

    model_config = ConfigDict(extra="forbid")

    recommendation: Recommendation
    conviction: Conviction
    rationale: str = Field(min_length=1, max_length=1500)
    earnings_direction: Direction


class RiskCritique(BaseModel):
    """Devil's-advocate pass (§3.4 step 2). Argues the opposite case. At least
    one objection is required so an empty critique cannot pass as "no objections";
    ``extra="forbid"`` keeps the pass to its structured shape."""

    model_config = ConfigDict(extra="forbid")

    counter_recommendation: Recommendation
    key_objections: List[str] = Field(min_length=1, max_length=10)
    conviction_challenge: str = Field(min_length=1, max_length=500)


class RiskFinal(BaseModel):
    """Final pass (§3.4 step 3). The committed recommendation; the rationale must
    explain how each critique objection was handled and reference any cap."""

    model_config = ConfigDict(extra="forbid")

    recommendation: Recommendation
    conviction: Conviction
    rationale: str = Field(min_length=1, max_length=2000)


# --- Condensed agent panels carried into the §6.3 output (not LLM boundaries) ---


class SentimentPanel(BaseModel):
    llm_sentiment: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    model_sentiment: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    disagreement: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    summary: str


class EarningsPanel(BaseModel):
    is_window: bool
    materiality: Optional[Literal["low", "medium", "high"]] = None
    summary: str


class TechnicalPanel(BaseModel):
    signal: Optional[str] = None
    summary: str


class RiskManagerOutput(BaseModel):
    """Full §6.3 output shape returned by the Risk Manager sub-workflow. The
    three passes are optional so a ``degraded`` result still validates."""

    ticker: str
    draft: Optional[RiskDraft] = None
    critique: Optional[RiskCritique] = None
    final: Optional[RiskFinal] = None
    sentiment: SentimentPanel
    earnings: EarningsPanel
    technical: TechnicalPanel
    status: Status
