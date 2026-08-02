"""§3.2: an excerpt-layer fallback is a resolution, not a degrade.

The excerpt ladder walks PDF attachment -> cover sheet. A candidate that reached a lower
layer still produced verbatim text, so it must NOT prefix the summary `degraded:` — that
prefix becomes agent `status: "degraded"`, and two degraded agents force `avoid` (§3.4).
Before this rule, a dead attachment on a candidate that lost selection and was never
extracted from decided the run's recommendation.

The scrape itself is monkeypatched: these are the routing/summary semantics, not Maya.
"""
from __future__ import annotations

import pytest

from data import maya
from routers import earnings as earnings_router


def _item(rid, *, layer, excerpt="Revenue was $4.1 billion.", rank=5):
    return {
        "id": rid,
        "symbol": "TEVA.TA",
        "published_at": "2026-07-29T11:20:00+00:00",
        "title": f"disclosure {rid}",
        "url": f"https://maya.tase.co.il/reports/details/{rid}",
        "language": "en",
        "rank_score": rank,
        "excerpt_source": layer,
        "excerpt": excerpt,
    }


@pytest.fixture
def fake_maya(monkeypatch):
    """Install a fake Maya scrape returning (items, errors)."""

    def install(items, errors):
        monkeypatch.setattr(
            maya, "fetch_disclosures", lambda *a, **k: (list(items), list(errors))
        )

    return install


def _fetch(ticker="TEVA.TA", window_days=30):
    return earnings_router.earnings_fetch(
        earnings_router.EarningsFetchRequest(ticker=ticker, window_days=window_days)
    )


class TestLayerFallbackIsNotADegrade:
    def test_cover_sheet_fallback_does_not_degrade(self, fake_maya):
        """The exact live case: one candidate's PDF 404s, its cover sheet succeeds."""
        fake_maya(
            [
                _item("1", layer=maya.PRESS_RELEASE),
                _item("2", layer=maya.PRESS_RELEASE),
                _item("3", layer=maya.COVER_SHEET, excerpt="Report page. Last Rate 10,580."),
            ],
            errors=[],
        )
        out = _fetch()
        assert not out["summary"].startswith("degraded:")

    def test_the_fallback_is_still_reported(self, fake_maya):
        """Not degrading must not mean going silent — thin figures need explaining."""
        fake_maya(
            [
                _item("1", layer=maya.PRESS_RELEASE),
                _item("2", layer=maya.COVER_SHEET, excerpt="Report page."),
            ],
            errors=[],
        )
        summary = _fetch()["summary"]
        assert "fell back to a lower excerpt layer" in summary
        assert maya.COVER_SHEET in summary

    def test_all_press_release_says_nothing_extra(self, fake_maya):
        fake_maya([_item("1", layer=maya.PRESS_RELEASE)], errors=[])
        summary = _fetch()["summary"]
        assert "fell back" not in summary
        assert not summary.startswith("degraded:")


class TestRealFailuresStillDegrade:
    """The guard must not swallow a genuine evidence gap."""

    def test_a_candidate_with_no_excerpt_degrades(self, fake_maya):
        fake_maya(
            [_item("1", layer="", excerpt="")],
            errors=["maya pdf: https://... -> HTTP 404, not a PDF"],
        )
        out = _fetch()
        assert out["summary"].startswith("degraded:")
        assert "404" in out["summary"]

    def test_a_failed_scrape_degrades(self, fake_maya):
        fake_maya([], errors=["maya: Page.goto timeout"])
        assert _fetch()["summary"].startswith("degraded:")

    def test_healthy_scrape_with_no_rows_is_not_a_failure(self, fake_maya):
        """Zero in-window disclosures is a valid answer, not a degrade (§13/§14)."""
        fake_maya([], errors=[])
        out = _fetch()
        assert not out["summary"].startswith("degraded:")
        assert out["items"] == []


class TestMarketRouting:
    def test_tase_routes_to_maya(self, fake_maya):
        fake_maya([_item("1", layer=maya.PRESS_RELEASE)], errors=[])
        out = _fetch("TEVA.TA")
        assert out["market"] == "tase"
        assert out["source"] == "maya"
        assert out["source_label"] == "TASE (Maya)"

    def test_us_routes_to_edgar(self, monkeypatch):
        from data import edgar

        monkeypatch.setattr(edgar, "fetch_disclosures", lambda *a, **k: ([], []))
        out = _fetch("AAPL")
        assert out["market"] == "us"
        assert out["source"] == "edgar"
        assert out["source_label"] == "SEC EDGAR"

    def test_response_shape_is_market_agnostic(self, monkeypatch, fake_maya):
        """The agent sub-workflow is shared, so both sources must answer alike."""
        from data import edgar

        fake_maya([_item("1", layer=maya.PRESS_RELEASE)], errors=[])
        monkeypatch.setattr(edgar, "fetch_disclosures", lambda *a, **k: ([], []))
        assert set(_fetch("TEVA.TA")) == set(_fetch("AAPL"))
