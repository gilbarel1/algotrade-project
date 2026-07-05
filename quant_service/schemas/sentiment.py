"""Pydantic schemas for the Sentiment Agent (§3.1, §9.4).

Two models, mirroring the `schemas/technical.py` convention:

- ``SentimentNarration`` — the LLM boundary. Haiku 4.5 reads the fetched
  headlines and returns one ``{id, score, reasoning}`` per article plus a short
  ``summary``. ``POST /validate`` (agent ``"sentiment"``) checks the raw model
  output against this; a malformed response triggers the sub-workflow's single
  stricter retry, then a ``degraded`` result. The model only *scores* — the
  aggregate ``llm_sentiment``/``model_sentiment``/``disagreement`` are computed
  deterministically in the sub-workflow, and the fine-tuned model scores come
  from ``/sentiment`` — so the LLM cannot alter either the model score or the
  disagreement metric.

- ``SentimentAgentOutput`` — the full §3.1 sub-workflow return (plus the §3.4
  canonical ``status`` field). Used by the Step 11 evaluation harness and by
  callers that want to validate the whole assembled payload. Aggregate scores are
  optional so a partial ``degraded`` result (LLM side failed, model side present)
  still validates.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# §3.4 canonical status enum, shared shape with the other agents.
Status = Literal["ok", "degraded"]


class ArticleScore(BaseModel):
    """One per-article LLM score. Extra keys rejected so the model cannot smuggle
    fabricated fields (e.g. its own 'model_score') past the boundary."""

    model_config = ConfigDict(extra="forbid")

    id: str
    score: float = Field(ge=-1.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=300)


class SentimentNarration(BaseModel):
    """What Haiku must return: a bounded score + reasoning per article, and a
    short overall summary. This is the validated LLM boundary (§9.4)."""

    model_config = ConfigDict(extra="forbid")

    items: List[ArticleScore]
    summary: str = Field(min_length=1, max_length=500)


class TopItem(BaseModel):
    """A cited article in the §3.1 output: both scores side by side."""

    headline: str
    source: str
    url: str
    language: str
    llm_score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    model_score: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


class SentimentAgentOutput(BaseModel):
    """Full §3.1 output shape returned by the Sentiment sub-workflow."""

    ticker: str
    window: str
    llm_sentiment: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    model_sentiment: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    disagreement: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    n_articles: int = Field(ge=0)
    top_items: List[TopItem]
    summary: str
    status: Status
