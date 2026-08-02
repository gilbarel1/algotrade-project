"""Shared fixtures. Every test here is offline: no network, no LLM calls, no n8n.

`quant_service/` is the service's import root (it runs with that as cwd), so it is put
on `sys.path` here rather than making the package importable a second way.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_DIR = os.path.join(REPO_ROOT, "quant_service")
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)


@pytest.fixture
def universe():
    """A self-contained §4.4 config, so tests never depend on the live watchlist."""
    return {
        "watchlist": ["TEVA.TA", "AAPL"],
        "earnings_window_days": 5,
        "earnings_candidates": 3,
        "markets": {
            "tase": {
                "closed_weekdays": [4, 5],
                "trading_hours": {
                    "tz": "Asia/Jerusalem",
                    "days": [0, 1, 2, 3, 4],
                    "open": "09:30",
                    "close": "17:25",
                },
                "earnings_source": "maya",
                "rss_feed_groups": ["en_il", "he_il"],
                "currency": "ILS",
            },
            "us": {
                "closed_weekdays": [5, 6],
                "trading_hours": {
                    "tz": "America/New_York",
                    "days": [1, 2, 3, 4, 5],
                    "open": "09:30",
                    "close": "16:00",
                },
                "earnings_source": "edgar",
                "rss_feed_groups": ["en_us"],
                "currency": "USD",
                "search_terms_fallback": "sec_registry",
            },
        },
        "market_overrides": {},
        "search_terms": {"TEVA.TA": ["Teva Pharmaceutical"]},
    }


@pytest.fixture
def clean_env(monkeypatch):
    """Drop env vars that would otherwise leak a developer's machine into a test."""
    for key in (
        "MARKET_GATE_FAKE_NOW",
        "REPORT_DIR",
        "N8N_WF_TECHNICAL",
        "N8N_WF_SENTIMENT",
        "N8N_WF_EARNINGS",
        "N8N_WF_RISK_MANAGER",
        "N8N_WF_CHAT",
    ):
        monkeypatch.delenv(key, raising=False)
