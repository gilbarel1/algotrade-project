"""Technical-indicator computation for the /indicators endpoint (§5, §3.3).

Pure functions over a cleaned OHLC DataFrame (the §4.2 `prices` shape): no DB,
no HTTP. Kept out of the router so it is unit-testable and so the numpy shim
below runs at one well-defined import point.

Indicators and parameters follow §3.3 / §5:
  * RSI length 14           → `rsi_14`
  * MACD fast/slow/signal 12/26/9 → `macd:{macd, signal, hist}`
  * Bollinger length 20, 2 std    → `bbands:{upper, mid, lower, pct_b}`
  * ATR length 14           → `atr_14`

`pct_b` is computed directly as (close - lower) / (upper - lower) with a guard
for zero-width bands, rather than trusting a library %B column across versions.
"""

from __future__ import annotations

import math

import numpy as np

# --- numpy 2.x shim (must run before importing pandas_ta) --------------------
# The PyPI `pandas-ta` (0.3.14b) does `from numpy import NaN`, an alias removed
# in numpy 2.0, so importing it crashes on the numpy 2.x in this venv. Restoring
# the alias is non-destructive and lets the library import unchanged; we do NOT
# downgrade numpy (that would risk the rest of the stack).
if not hasattr(np, "NaN"):
    np.NaN = np.nan  # type: ignore[attr-defined]

import pandas_ta as ta  # noqa: E402  (import intentionally after the shim)

# §3.3 / §5 indicator parameters.
RSI_LENGTH = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BBANDS_LENGTH, BBANDS_STD = 20, 2.0
ATR_LENGTH = 14

# Minimum bars each indicator needs before it yields a non-NaN latest value.
MIN_BARS = {
    "rsi": RSI_LENGTH + 1,
    "macd": MACD_SLOW + MACD_SIGNAL,
    "bbands": BBANDS_LENGTH,
    "atr": ATR_LENGTH + 1,
}

ALL_INDICATORS = ("rsi", "macd", "bbands", "atr")


def _last(series) -> float | None:
    """Last finite value of a Series, or None if empty/NaN."""
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(float(val), 4)


def _pick(df, prefix: str):
    """Return the first column of `df` whose name starts with `prefix`."""
    if df is None:
        return None
    for col in df.columns:
        if col.startswith(prefix):
            return df[col]
    return None


def compute_indicators(prices, which=ALL_INDICATORS) -> dict:
    """Compute the requested indicators from a cleaned OHLC frame.

    `prices` must have `high`, `low`, `close` columns, oldest first. Returns the
    §5 `indicators` object containing only the requested keys. An indicator with
    insufficient history yields a `null` value (never a crash).
    """
    close = prices["close"].astype(float)
    high = prices["high"].astype(float)
    low = prices["low"].astype(float)
    requested = [w for w in ALL_INDICATORS if w in set(which)]

    out: dict = {}
    for name in requested:
        if name == "rsi":
            out["rsi_14"] = _last(ta.rsi(close, length=RSI_LENGTH))
        elif name == "macd":
            macd_df = ta.macd(
                close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL
            )
            out["macd"] = {
                "macd": _last(_pick(macd_df, "MACD_")),
                "signal": _last(_pick(macd_df, "MACDs_")),
                "hist": _last(_pick(macd_df, "MACDh_")),
            }
        elif name == "bbands":
            bb = ta.bbands(close, length=BBANDS_LENGTH, std=BBANDS_STD)
            upper = _last(_pick(bb, "BBU_"))
            mid = _last(_pick(bb, "BBM_"))
            lower = _last(_pick(bb, "BBL_"))
            out["bbands"] = {
                "upper": upper,
                "mid": mid,
                "lower": lower,
                "pct_b": _pct_b(_last(close), upper, lower),
            }
        elif name == "atr":
            out["atr_14"] = _last(ta.atr(high, low, close, length=ATR_LENGTH))
    return out


def _pct_b(last_close, upper, lower) -> float | None:
    """%B = (close - lower) / (upper - lower); None on missing or zero-width band."""
    if last_close is None or upper is None or lower is None:
        return None
    width = upper - lower
    if width == 0:
        return None
    return round((last_close - lower) / width, 4)


def insufficient(n_bars: int, which=ALL_INDICATORS) -> list[str]:
    """Requested indicators that don't have enough bars to produce a value."""
    return [w for w in which if w in MIN_BARS and n_bars < MIN_BARS[w]]
