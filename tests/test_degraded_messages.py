"""Degraded reasons are printed verbatim in the PDF, so their wording is a contract.

The report showed `degraded: degraded: NewsAPI: NewsAPI returned 401 …` — each layer
adding a label the layer below had already added.
"""
from __future__ import annotations

import json
import os

import pytest

from data import newsapi, rss
from routers import news as news_router

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def no_rss(monkeypatch):
    monkeypatch.setattr(rss, "fetch_rss", lambda *a, **k: ([], []))


def _fetch(ticker="TEVA.TA"):
    return news_router.news_fetch(news_router.NewsFetchRequest(ticker=ticker))


class TestSourceIsNamedOnce:
    def test_a_self_describing_error_is_not_prefixed_again(self, monkeypatch, no_rss):
        def boom(*a, **k):
            raise newsapi.NewsAPIError("NewsAPI returned 401 (apiKeyInvalid: bad key)")

        monkeypatch.setattr(newsapi, "fetch_newsapi", boom)
        summary = _fetch()["summary"]
        assert "NewsAPI: NewsAPI" not in summary
        assert summary.count("NewsAPI returned 401") == 1

    def test_an_anonymous_error_still_gets_a_label(self, monkeypatch, no_rss):
        """Without the prefix, "NEWSAPI_API_KEY is not set" loses its source."""
        def boom(*a, **k):
            raise newsapi.NewsAPIError("NEWSAPI_API_KEY is not set")

        monkeypatch.setattr(newsapi, "fetch_newsapi", boom)
        assert "NEWSAPI_API_KEY is not set" in _fetch()["summary"]

    def test_a_healthy_fetch_says_nothing_about_degradation(self, monkeypatch, no_rss):
        monkeypatch.setattr(newsapi, "fetch_newsapi", lambda *a, **k: [])
        assert not _fetch()["summary"].startswith("degraded:")


class TestAgentsDoNotDoublePrefix:
    """The n8n agents re-prefix a fetch summary that already carries `degraded:`."""

    @pytest.mark.parametrize(
        "workflow", ["n8n/agents/sentiment.json", "n8n/agents/earnings.json"]
    )
    def test_fetch_summary_is_stripped_before_re_prefixing(self, workflow):
        wf = json.load(open(os.path.join(REPO_ROOT, workflow), encoding="utf-8"))
        uses = [
            n["name"]
            for n in wf["nodes"]
            if "fetch_summary" in (n.get("parameters") or {}).get("jsCode", "")
        ]
        assert uses, f"{workflow}: no node composes a summary from fetch_summary"
        for node in wf["nodes"]:
            code = (node.get("parameters") or {}).get("jsCode", "")
            for line in code.splitlines():
                if "fetch_summary" not in line or "degraded" not in line:
                    continue
                # Any line that both reads fetch_summary and re-labels it must strip first.
                if "parts.push" in line or "summary:" in line:
                    assert "replace('degraded: ', '')" in line, (
                        f"{workflow} [{node['name']}] re-prefixes without stripping: {line.strip()}"
                    )
