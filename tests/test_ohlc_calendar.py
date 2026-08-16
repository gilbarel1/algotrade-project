"""§4.3 cleaning: the session grid follows the source, not an assumed trading week.

The regression these pin was live and expensive. `clean_ohlc` used to reindex
onto `closed_weekdays` from config — Sun–Thu for `tase`. Yahoo actually returns
`.TA` daily bars on a **Mon–Fri** index (verified on TEVA.TA, ICL.TA and
POLI.TA), so every real Friday bar was reindexed away and a synthetic Sunday was
forward-filled from the preceding Thursday. About 19% of a TASE series became
duplicate rows: zero close-to-close return and a near-zero true range, which
deflates ATR, flattens RSI, and poisons the MAD outlier detector — all from the
data layer, silently, on the project's primary market.

The invariants below are the ones that make that class of bug impossible:
no source row may be dropped by the reindex, and no bar may be invented on a
weekday the source never delivers. They are asserted on synthetic frames, so
they stay offline and cannot drift with live data.
"""
from __future__ import annotations

import pandas as pd
import pytest

from data import yahoo

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def _frame(dates: list[str], *, close_from: float = 100.0) -> pd.DataFrame:
    """A raw-Yahoo-shaped frame: DatetimeIndex named ts, distinct closes."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="ts")
    n = len(dates)
    return pd.DataFrame(
        {
            "open": [close_from + i for i in range(n)],
            "high": [close_from + i + 1 for i in range(n)],
            "low": [close_from + i - 1 for i in range(n)],
            "close": [close_from + i for i in range(n)],
            "volume": [1_000 + i for i in range(n)],
        },
        index=idx,
    )


# Three full Mon-Fri weeks, exactly as Yahoo delivers a .TA symbol.
MON_FRI_3W = [
    "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24",
]


def test_observed_weekdays_reads_the_sources_own_week():
    idx = pd.DatetimeIndex(pd.to_datetime(MON_FRI_3W))
    assert yahoo._observed_weekdays(idx) == (MON, TUE, WED, THU, FRI)


def test_tase_symbol_on_a_mon_fri_feed_keeps_every_friday():
    """The core regression: Fridays are real bars and must survive cleaning."""
    cleaned, res = yahoo.clean_ohlc(_frame(MON_FRI_3W), "TEVA.TA")
    weekdays = set(pd.DatetimeIndex(cleaned["ts"]).weekday)
    assert FRI in weekdays
    assert len(cleaned) == len(MON_FRI_3W)
    assert res.sessions_filled == 0
    assert res.sessions_dropped == 0


def test_tase_symbol_on_a_mon_fri_feed_invents_no_sunday():
    """The other half: a weekday the source never sends must never be created."""
    cleaned, _ = yahoo.clean_ohlc(_frame(MON_FRI_3W), "TEVA.TA")
    weekdays = set(pd.DatetimeIndex(cleaned["ts"]).weekday)
    assert SUN not in weekdays
    assert SAT not in weekdays


def test_no_bar_is_a_duplicate_of_the_one_before_it():
    """A forward-filled phantom shows up as an exact repeat; there should be none."""
    cleaned, _ = yahoo.clean_ohlc(_frame(MON_FRI_3W), "TEVA.TA")
    cols = cleaned[["open", "high", "low", "close", "volume"]]
    assert not (cols.shift() == cols).all(axis=1).any()


@pytest.mark.parametrize("symbol", ["TEVA.TA", "AAPL"])
def test_reindex_never_drops_a_source_row(symbol):
    """Whatever the configured market, every fetched session reaches the cache."""
    raw = _frame(MON_FRI_3W)
    cleaned, _ = yahoo.clean_ohlc(raw, symbol)
    kept = set(pd.DatetimeIndex(cleaned["ts"]))
    assert set(raw.index) <= kept


def test_isolated_midweek_holiday_is_still_bridged():
    """§4.3's one-day fill still applies — a single missing Wednesday is filled."""
    dates = [d for d in MON_FRI_3W if d != "2026-07-15"]  # a Wednesday
    cleaned, res = yahoo.clean_ohlc(_frame(dates), "TEVA.TA")
    assert res.sessions_filled == 1
    assert pd.Timestamp("2026-07-15") in set(pd.DatetimeIndex(cleaned["ts"]))


def test_multi_day_closure_is_left_as_a_gap():
    """Two or more consecutive missing sessions are a real closure, not a gap to fill."""
    dates = [d for d in MON_FRI_3W if d not in ("2026-07-14", "2026-07-15")]
    cleaned, res = yahoo.clean_ohlc(_frame(dates), "TEVA.TA")
    assert res.sessions_dropped == 2
    assert res.sessions_filled == 0
    present = set(pd.DatetimeIndex(cleaned["ts"]))
    assert pd.Timestamp("2026-07-14") not in present
    assert pd.Timestamp("2026-07-15") not in present


def test_calendar_mismatch_is_reported_for_a_tase_feed_on_mon_fri():
    """Following the source is right; doing it silently is not (§4.4)."""
    _, res = yahoo.clean_ohlc(_frame(MON_FRI_3W), "TEVA.TA")
    assert res.calendar_mismatch
    assert "Fri" in res.calendar_mismatch and "Sun" in res.calendar_mismatch


def test_no_mismatch_reported_when_source_and_config_agree():
    """A US symbol on a Mon-Fri feed matches config, so the line stays quiet."""
    _, res = yahoo.clean_ohlc(_frame(MON_FRI_3W), "AAPL")
    assert res.calendar_mismatch is None


def test_a_genuine_sun_thu_feed_is_handled_on_its_own_terms():
    """The fix is not 'assume Mon-Fri' — a Sun-Thu source keeps Sundays and no Fridays."""
    sun_thu = [
        "2026-07-05", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09",
        "2026-07-12", "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
    ]
    cleaned, res = yahoo.clean_ohlc(_frame(sun_thu), "TEVA.TA")
    weekdays = set(pd.DatetimeIndex(cleaned["ts"]).weekday)
    assert SUN in weekdays and FRI not in weekdays
    assert len(cleaned) == len(sun_thu)
    assert res.calendar_mismatch is None  # matches the configured tase week
