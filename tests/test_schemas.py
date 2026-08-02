"""Pydantic at every LLM boundary (§9.4).

These schemas are the guardrail that turns a malformed model response into one stricter
retry and then an explicit `degraded` result. If they accept junk, the whole "no silent
bad values" property is gone — so the negative cases matter more than the positive ones.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.earnings import EarningsExtractionSample, ExtractedFigure
from schemas.risk_manager import RiskCritique, RiskDraft, RiskFinal
from schemas.sentiment import ArticleScore, SentimentNarration
from schemas.technical import TechnicalNarration


class TestCanonicalEnums:
    """§3.4 declares the enums; drift here silently breaks the rubric."""

    @pytest.mark.parametrize("rec", ["long", "short", "hold", "avoid"])
    def test_every_recommendation_is_accepted(self, rec):
        assert RiskDraft(
            recommendation=rec, conviction="low", rationale="x", earnings_direction="neutral"
        ).recommendation == rec

    @pytest.mark.parametrize("bad", ["buy", "sell", "LONG", "strong_buy", ""])
    def test_invented_recommendations_are_rejected(self, bad):
        with pytest.raises(ValidationError):
            RiskDraft(
                recommendation=bad, conviction="low", rationale="x", earnings_direction="neutral"
            )

    @pytest.mark.parametrize("bad", ["very_low", "certain", "HIGH", 1])
    def test_invented_convictions_are_rejected(self, bad):
        with pytest.raises(ValidationError):
            RiskDraft(
                recommendation="hold", conviction=bad, rationale="x", earnings_direction="neutral"
            )

    @pytest.mark.parametrize(
        "signal",
        ["bullish_momentum", "bearish_momentum", "overbought", "oversold", "neutral"],
    )
    def test_technical_signals(self, signal):
        assert TechnicalNarration(signal=signal, summary="s").signal == signal

    def test_invented_technical_signal_is_rejected(self):
        with pytest.raises(ValidationError):
            TechnicalNarration(signal="mildly_bullish", summary="s")


class TestSentimentBounds:
    @pytest.mark.parametrize("score", [-1.0, -0.5, 0.0, 0.42, 1.0])
    def test_scores_within_range(self, score):
        narration = SentimentNarration(
            items=[ArticleScore(id="a1", score=score, reasoning="r")], summary="s"
        )
        assert narration.items[0].score == score

    @pytest.mark.parametrize("score", [-1.01, 1.5, 42])
    def test_scores_outside_the_range_are_rejected(self, score):
        """A -1..+1 contract that silently accepted 42 would poison the mean."""
        with pytest.raises(ValidationError):
            ArticleScore(id="a1", score=score, reasoning="r")

    def test_the_model_cannot_smuggle_extra_fields(self):
        """`extra="forbid"` is what stops a model inventing its own model_score."""
        with pytest.raises(ValidationError):
            ArticleScore(id="a1", score=0.5, reasoning="r", model_score=-0.9)


class TestNeverInventNumbers:
    """§3.2's guarantee, at the schema boundary."""

    def test_a_committed_figure_carries_its_vote_count(self):
        fig = ExtractedFigure(value="$4.1B", confidence=3)
        assert fig.value == "$4.1B" and fig.confidence == 3

    def test_ambiguous_is_a_legal_value(self):
        assert ExtractedFigure(value="ambiguous", confidence=1).value == "ambiguous"

    @pytest.mark.parametrize("bad", [0, 4, -1])
    def test_confidence_outside_the_sample_count_is_rejected(self, bad):
        """n=3 sampling can only ever agree 1, 2 or 3 times."""
        with pytest.raises(ValidationError):
            ExtractedFigure(value="$4.1B", confidence=bad)

    def test_extraction_sample_requires_all_three_fields(self):
        """A lazy `{}` must fail rather than count as a 'no figures found' vote."""
        with pytest.raises(ValidationError):
            EarningsExtractionSample(revenue="$4.1B")  # eps/guidance missing

    def test_a_sample_may_report_null_figures(self):
        """Absent-from-source is null here; the vote turns it into `ambiguous`."""
        sample = EarningsExtractionSample(revenue="$4.1B", eps=None, guidance=None)
        assert sample.eps is None

    def test_a_sample_cannot_invent_new_figure_fields(self):
        with pytest.raises(ValidationError):
            EarningsExtractionSample(
                revenue="$4.1B", eps=None, guidance=None, ebitda="$1.2B"
            )


class TestCritiqueAndFinal:
    def test_a_valid_critique(self):
        critique = RiskCritique(
            counter_recommendation="hold",
            key_objections=["sentiment degraded"],
            conviction_challenge="high -> medium",
        )
        assert critique.key_objections == ["sentiment degraded"]

    def test_critique_needs_at_least_one_objection(self):
        """A devil's advocate that objects to nothing has not run (§3.4)."""
        with pytest.raises(ValidationError):
            RiskCritique(
                counter_recommendation="hold", key_objections=[], conviction_challenge="x"
            )

    def test_final_requires_a_rationale(self):
        with pytest.raises(ValidationError):
            RiskFinal(recommendation="hold", conviction="low", rationale="")
