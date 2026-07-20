"""Market abstraction: symbol -> market, market properties, session gate (§4.4, §6.1).

A ticker's **market** (`tase` | `us`) is the one new concept the mixed-watchlist
support rests on. It replaces what used to be global TASE assumptions with a
per-market bundle read from `config/universe.yaml`:

  * `closed_weekdays`  -> the OHLC session grid (§4.3, `data/yahoo.py`)
  * `trading_hours`    -> the scheduled-run gate (§6.1, `routers/runs.py`)
  * `rss_feed_groups`  -> which news feeds a ticker pulls (§3.1, `routers/news.py`)
  * `earnings_source`  -> Maya vs. EDGAR (§3.2; EDGAR is wired up in Step 14)
  * `currency`         -> report rendering (§8.1; rendered in Step 14)

Derivation is by Yahoo suffix — `*.TA` is TASE, a bare symbol is US — with an
explicit `market_overrides` map winning when a symbol needs pinning.

**Two weekday conventions meet here, and the conversion happens in exactly one
place** (`is_market_open`), which is why both live behind this module:

  * `closed_weekdays` is **pandas** numbering: Monday=0 … Sunday=6.
  * `trading_hours.days` is the **n8n cron** convention: 0=Sunday. It is written
    that way so it reads the same as `schedule_cron` in the same file.

This module is also the single loader for `config/universe.yaml` (it has to read
the file anyway); `routers/news.py`, `routers/earnings.py` and `routers/runs.py`
import `load_config` from here rather than each keeping a private copy. The file
is read fresh per call — no caching — so a config edit takes effect without a
service restart, exactly as the previous per-router loaders behaved.
"""

from __future__ import annotations

import os
from datetime import datetime, time, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import yaml

# data/markets.py -> data/ -> quant_service/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "universe.yaml")

TASE_SUFFIX = ".TA"
TASE = "tase"
US = "us"

# DEV OVERRIDE (test-only, not part of the design): when set to an ISO timestamp,
# `now_utc()` returns that instant instead of the real clock, so the §6.1 gate is
# deterministically verifiable offline (the n8n-side TASE_GATE_FAKE_NOW it
# replaces did the same job before the gate moved into the service). MUST be
# unset in production.
FAKE_NOW_ENV = "MARKET_GATE_FAKE_NOW"


def load_config() -> dict:
    """Read `config/universe.yaml` (§4.4). Shared by the routers and this module."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _cfg(config: Optional[dict]) -> dict:
    return load_config() if config is None else config


def market(symbol: str, config: Optional[dict] = None) -> str:
    """Return the market of a ticker: `*.TA` -> "tase", bare symbol -> "us".

    An explicit `market_overrides` entry in config wins over the suffix rule.
    """
    sym = (symbol or "").strip()
    overrides = _cfg(config).get("market_overrides") or {}
    if sym in overrides:
        return str(overrides[sym])
    return TASE if sym.upper().endswith(TASE_SUFFIX) else US


def market_config(name: str, config: Optional[dict] = None) -> dict:
    """Return the `markets:` block for one market (§4.4).

    Raises on an unknown/missing market rather than defaulting: a silent fallback
    would quietly reindex a US symbol onto the Israeli trading week, which is the
    exact bug this abstraction exists to prevent (§4.3), and "defaults from
    config" is a standing guardrail.
    """
    markets = _cfg(config).get("markets") or {}
    if name not in markets:
        known = ", ".join(sorted(markets)) or "<none>"
        raise ValueError(
            f"unknown market {name!r}: config/universe.yaml `markets:` defines {known}"
        )
    return markets[name] or {}


def closed_weekdays(name: str, config: Optional[dict] = None) -> tuple[int, ...]:
    """Weekdays the market does not trade, in **pandas** numbering (Mon=0…Sun=6)."""
    return tuple(int(d) for d in market_config(name, config).get("closed_weekdays", ()))


def currency(name: str, config: Optional[dict] = None) -> str:
    """The market's reporting currency (§8.1)."""
    return str(market_config(name, config).get("currency", ""))


def now_utc() -> datetime:
    """Current instant, tz-aware UTC — or `MARKET_GATE_FAKE_NOW` when set (dev only).

    A fake value must carry an explicit offset: assuming UTC for a naive string
    would silently shift the gate by the tester's own timezone, turning a failed
    check into a passing one for the wrong reason.
    """
    fake = (os.environ.get(FAKE_NOW_ENV) or "").strip()
    if not fake:
        return datetime.now(timezone.utc)

    try:
        parsed = datetime.fromisoformat(fake.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{FAKE_NOW_ENV} is set but is not a valid ISO timestamp: {fake!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{FAKE_NOW_ENV} must include a UTC offset (e.g. "
            f"2026-07-21T17:00:00+00:00); got the naive value {fake!r}"
        )
    return parsed.astimezone(timezone.utc)


def _parse_hhmm(value: str, field: str, name: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"markets.{name}.trading_hours.{field} must be 'HH:MM'; got {value!r}"
        ) from exc


def is_market_open(
    name: str, now: Optional[datetime] = None, config: Optional[dict] = None
) -> bool:
    """True if `name` is in continuous trading at `now` (§6.1).

    `now` must be tz-aware (defaults to `now_utc()`), and is converted into the
    market's OWN timezone via zoneinfo — never a fixed offset, so the ~2-3 week
    windows where Israel and the US have already/not yet switched DST are handled
    correctly. Bounds are inclusive of both open and close.
    """
    hours = market_config(name, config).get("trading_hours") or {}
    tz_name = hours.get("tz")
    if not tz_name:
        raise ValueError(f"markets.{name}.trading_hours.tz is missing (§4.4)")

    moment = now or now_utc()
    if moment.tzinfo is None:
        raise ValueError("is_market_open() requires a timezone-aware `now`")
    local = moment.astimezone(ZoneInfo(str(tz_name)))

    # trading_hours.days is n8n-cron numbering (0=Sun); datetime.weekday() is
    # pandas/ISO numbering (Mon=0). This is the ONE place the two convert.
    n8n_dow = (local.weekday() + 1) % 7
    open_days: Iterable[int] = hours.get("days") or ()
    if n8n_dow not in {int(d) for d in open_days}:
        return False

    opens = _parse_hhmm(hours.get("open"), "open", name)
    closes = _parse_hhmm(hours.get("close"), "close", name)
    return opens <= local.time() <= closes


def open_markets(
    symbols: Iterable[str], now: Optional[datetime] = None, config: Optional[dict] = None
) -> list[str]:
    """Filter `symbols` to those whose market is in session at `now` (§6.1 gate).

    Order is preserved. `now` is resolved once for the whole batch so every symbol
    is judged against the same instant.
    """
    cfg = _cfg(config)
    moment = now or now_utc()
    cache: dict[str, bool] = {}
    kept: list[str] = []
    for symbol in symbols:
        name = market(symbol, cfg)
        if name not in cache:
            cache[name] = is_market_open(name, moment, cfg)
        if cache[name]:
            kept.append(symbol)
    return kept
