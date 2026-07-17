"""Pydantic schemas for the Earnings Agent (§3.2, §9.4).

Three models, mirroring the `schemas/sentiment.py` convention. The Earnings
Agent has **two** LLM boundaries, each validated via ``POST /validate``:

- ``EarningsClassification`` (agent ``"earnings"``) — the single-shot
  (temperature 0) classify/translate pass over the latest disclosure. Returns
  the §3.4 canonical ``kind`` and ``materiality`` enums plus a short summary;
  ``title_en`` is the flagged translation for Hebrew disclosures (§8.1 — the
  original ``title``/``language`` are kept alongside so a reviewer can trace
  back to the Maya source).

- ``EarningsExtractionSample`` (agent ``"earnings_extraction"``) — one of the
  three self-consistency samples (temperature 0.3). Each field is either a
  string copied **verbatim** from the disclosure text or null when the figure
  is not present. The majority vote and the ``{value, confidence}`` /
  ``"ambiguous"`` marking happen deterministically in the sub-workflow — the
  LLM never emits a confidence, so it cannot vouch for its own numbers (§3.2).

- ``EarningsAgentOutput`` — the full §3.2 sub-workflow return (plus the §3.4
  canonical ``status``). Used by the Step 11 evaluation harness and by callers
  validating the assembled payload. Classified fields are optional so a
  ``degraded`` result (LLM boundary failed twice) still validates without
  fabricated values. ``selected_disclosure`` is the most material of the ranked
  candidates rather than the newest filing, and ``considered`` records the
  classified-but-rejected ones (§3.2).
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# §3.4 canonical enums, shared shape with the other agents.
Status = Literal["ok", "degraded"]
Kind = Literal["earnings", "guidance", "material_event", "other"]
Materiality = Literal["low", "medium", "high"]


class EarningsClassification(BaseModel):
    """What Haiku must return from the classify/translate pass. Extra keys are
    rejected so the model cannot smuggle fields (e.g. its own extracted
    numbers) past the boundary — numbers only enter via self-consistency."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    materiality: Materiality
    summary: str = Field(min_length=1, max_length=500)
    # English translation of a Hebrew title; null for EN disclosures. Kept as a
    # separate field so the translation stays flagged as such (§8.1).
    title_en: Optional[str] = Field(default=None, max_length=300)


class EarningsExtractionSample(BaseModel):
    """One self-consistency sample: each figure verbatim-from-source or null.
    ``extra="forbid"`` keeps a sample from inventing new figure fields; all
    three keys are **required** (values may be null) so a truncated or lazy
    response — e.g. ``{}`` — fails validation instead of silently counting as
    a "no figures found" vote."""

    model_config = ConfigDict(extra="forbid")

    revenue: Optional[str] = Field(max_length=100)
    eps: Optional[str] = Field(max_length=100)
    guidance: Optional[str] = Field(max_length=200)


class ExtractedFigure(BaseModel):
    """A voted figure in the §3.2 output: the committed verbatim string with
    ``confidence`` = number of agreeing samples (2 or 3), or the literal
    ``"ambiguous"`` with confidence 1 — never a silently filled value."""

    value: str
    confidence: int = Field(ge=1, le=3)


class SelectedDisclosure(BaseModel):
    """The §3.2 ``selected_disclosure`` block — the disclosure the agent
    classified *and* extracted from: the most material of the ranked
    candidates, which is usually **not** the newest filing."""

    date: Optional[str] = None
    type: Optional[Kind] = None  # null when classification degraded
    language: str
    title: str
    url: str
    title_en: Optional[str] = None  # flagged translation for HE titles (§8.1)
    summary: Optional[str] = None
    extracted: Optional[Dict[str, ExtractedFigure]] = None


class ConsideredDisclosure(BaseModel):
    """A candidate that was classified but not selected (§3.2).

    The audit trail for the choice: it shows what the agent looked at and how
    the model rated it, so a reviewer can see why the winner won. Carries no
    ``extracted`` — self-consistency runs on the selected disclosure only.
    """

    date: Optional[str] = None
    title: str
    url: str
    kind: Optional[Kind] = None  # null when that candidate's classification degraded
    materiality: Optional[Materiality] = None


class EarningsAgentOutput(BaseModel):
    """Full §3.2 output shape returned by the Earnings sub-workflow."""

    ticker: str
    selected_disclosure: Optional[SelectedDisclosure] = None
    considered: List[ConsideredDisclosure] = []
    is_earnings_window: bool
    materiality: Optional[Materiality] = None
    summary: str
    status: Status
