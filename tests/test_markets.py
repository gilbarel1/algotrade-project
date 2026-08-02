"""Market abstraction and the per-market schedule gate (§4.4, §6.1).

The gate decides which tickers a scheduled run analyzes, so a wrong answer here is a
run that silently covers the wrong market — hence the DST cases, which are the ones a
fixed UTC offset gets wrong for two to three weeks a year.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data import markets


class TestMarketResolution:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("TEVA.TA", "tase"),
            ("LUMI.TA", "tase"),
            ("AAPL", "us"),
            ("NVDA", "us"),
            ("BRK.B", "us"),  # a dot that is NOT the TASE suffix
            ("teva.ta", "tase"),  # case-insensitive suffix
        ],
    )
    def test_suffix_rule(self, symbol, expected, universe):
        assert markets.market(symbol, universe) == expected

    def test_override_beats_the_suffix_rule(self, universe):
        universe["market_overrides"] = {"SOMESYM": "tase"}
        assert markets.market("SOMESYM", universe) == "tase"

    def test_market_properties_are_config_driven(self, universe):
        assert markets.currency("tase", universe) == "ILS"
        assert markets.currency("us", universe) == "USD"
        # pandas numbering (Mon=0): TASE rests Fri/Sat, the US Sat/Sun.
        assert markets.closed_weekdays("tase", universe) == (4, 5)
        assert markets.closed_weekdays("us", universe) == (5, 6)

    def test_unknown_market_raises(self, universe):
        with pytest.raises(ValueError):
            markets.market_config("moon", universe)


class TestSessionGate:
    """`is_market_open` in each market's OWN timezone (§6.1)."""

    def test_tase_open_midweek_morning(self, universe):
        # Thursday 2026-07-30, 11:00 Israel time == 08:00 UTC.
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        assert markets.is_market_open("tase", now, universe) is True
        assert markets.is_market_open("us", now, universe) is False

    def test_us_open_israeli_evening(self, universe):
        # Thursday 2026-07-30, 20:00 Israel == 17:00 UTC == 13:00 New York.
        now = datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)
        assert markets.is_market_open("us", now, universe) is True
        assert markets.is_market_open("tase", now, universe) is False

    def test_friday_is_tase_weekend_but_a_us_session(self, universe):
        # Friday 2026-07-31, 17:00 UTC — TASE closed (Fri), NYSE trading.
        now = datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc)
        assert markets.is_market_open("tase", now, universe) is False
        assert markets.is_market_open("us", now, universe) is True

    def test_saturday_closes_both(self, universe):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        assert markets.is_market_open("tase", now, universe) is False
        assert markets.is_market_open("us", now, universe) is False

    def test_bounds_are_inclusive(self, universe):
        # TASE opens 09:30 Israel == 06:30 UTC in July (UTC+3).
        at_open = datetime(2026, 7, 30, 6, 30, tzinfo=timezone.utc)
        just_before = datetime(2026, 7, 30, 6, 29, tzinfo=timezone.utc)
        assert markets.is_market_open("tase", at_open, universe) is True
        assert markets.is_market_open("tase", just_before, universe) is False

    def test_naive_now_is_rejected(self, universe):
        with pytest.raises(ValueError):
            markets.is_market_open("tase", datetime(2026, 7, 30, 8, 0), universe)


class TestDstSkew:
    """Israel and the US switch DST on different dates.

    For those weeks the Israel/New York gap is 6 hours instead of 7, so any fixed
    offset misjudges one market. These cases exist to catch a regression to one.
    """

    def test_us_session_during_the_skew_window(self, universe):
        # Wed 2026-03-11: US already on EDT, Israel still on IST (gap = 6h).
        # 14:00 UTC == 10:00 New York (open) == 16:00 Israel.
        now = datetime(2026, 3, 11, 14, 0, tzinfo=timezone.utc)
        assert markets.is_market_open("us", now, universe) is True

    def test_us_close_shifts_with_dst(self, universe):
        # January (EST): 21:30 UTC == 16:30 New York — after the 16:00 close.
        winter = datetime(2026, 1, 14, 21, 30, tzinfo=timezone.utc)
        assert markets.is_market_open("us", winter, universe) is False
        # July (EDT): the same 21:30 UTC == 17:30 New York — also closed.
        summer = datetime(2026, 7, 15, 21, 30, tzinfo=timezone.utc)
        assert markets.is_market_open("us", summer, universe) is False
        # But 19:00 UTC is 15:00 New York in July — open.
        assert markets.is_market_open(
            "us", datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc), universe
        ) is True


class TestWatchlistFiltering:
    """`open_markets` is what a scheduled run actually calls (§6.1)."""

    def test_israeli_morning_keeps_only_tase_names(self, universe):
        now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)  # 11:00 Israel
        assert markets.open_markets(["TEVA.TA", "AAPL", "LUMI.TA"], now, universe) == [
            "TEVA.TA",
            "LUMI.TA",
        ]

    def test_israeli_evening_keeps_only_us_names(self, universe):
        now = datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)  # 20:00 Israel
        assert markets.open_markets(["TEVA.TA", "AAPL", "NVDA"], now, universe) == [
            "AAPL",
            "NVDA",
        ]

    def test_order_is_preserved(self, universe):
        now = datetime(2026, 7, 30, 17, 0, tzinfo=timezone.utc)
        assert markets.open_markets(["NVDA", "AAPL"], now, universe) == ["NVDA", "AAPL"]

    def test_closed_everywhere_returns_empty(self, universe):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)  # Saturday
        assert markets.open_markets(["TEVA.TA", "AAPL"], now, universe) == []


class TestFakeNowOverride:
    """`MARKET_GATE_FAKE_NOW` makes the outside-hours branch testable (§11.1)."""

    def test_fake_now_is_honoured(self, monkeypatch, clean_env):
        monkeypatch.setenv(markets.FAKE_NOW_ENV, "2026-07-30T08:00:00+00:00")
        assert markets.now_utc() == datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)

    def test_naive_fake_now_is_rejected(self, monkeypatch, clean_env):
        # Without an offset the gate would silently shift by the tester's own zone.
        monkeypatch.setenv(markets.FAKE_NOW_ENV, "2026-07-30T08:00:00")
        with pytest.raises(ValueError, match="UTC offset"):
            markets.now_utc()

    def test_garbage_fake_now_is_rejected(self, monkeypatch, clean_env):
        monkeypatch.setenv(markets.FAKE_NOW_ENV, "yesterday")
        with pytest.raises(ValueError):
            markets.now_utc()
