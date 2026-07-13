"""matplotlib price-chart PNG thumbnails for the PDF report (§8.1).

One small function: turn a cleaned `prices` frame (the §4.2 shape, oldest first)
into a compact PNG of the last 90 trading days' close plus 20- and 50-day moving
averages. The frame should carry more history than 90 bars (the renderer pulls
~150 calendar days via `cache.get_cached_ohlc`) so the 50-day MA is already
defined at the left edge of the displayed window.

Rendering never raises: too little data or any plotting error returns ``None``
and the report shows "chart unavailable" for that ticker rather than failing the
whole PDF (§8.1 best-effort enrichment).
"""

from __future__ import annotations

import io
from typing import Optional

import matplotlib

# Headless backend — the service has no display and this runs inside a request.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (import after backend selection)

_DISPLAY_DAYS = 90
_MA_SHORT = 20
_MA_LONG = 50


def price_chart_png(prices, ticker: str) -> Optional[bytes]:
    """Return a PNG thumbnail (bytes) of 90-day close + 20/50-day MAs, or None.

    `prices` is a DataFrame with at least `ts` and `close` columns, oldest
    first (the §4.2 `prices` shape). Returns None when there are too few bars to
    draw a meaningful line (< the short MA window) or on any rendering error.
    """
    try:
        if prices is None or len(prices) < _MA_SHORT:
            return None

        df = prices.sort_values("ts")
        close = df["close"].astype(float)
        ma20 = close.rolling(_MA_SHORT).mean()
        ma50 = close.rolling(_MA_LONG).mean()

        # Show only the last 90 bars, but compute the MAs over full history
        # first so they are already "warm" at the left edge of the window.
        ts = df["ts"].iloc[-_DISPLAY_DAYS:]
        close_w = close.iloc[-_DISPLAY_DAYS:]
        ma20_w = ma20.iloc[-_DISPLAY_DAYS:]
        ma50_w = ma50.iloc[-_DISPLAY_DAYS:]

        fig, ax = plt.subplots(figsize=(4.6, 1.9), dpi=150)
        ax.plot(ts, close_w, color="#1f77b4", linewidth=1.1, label="Close")
        # Only draw an MA line if it has any defined values in the window.
        if ma20_w.notna().any():
            ax.plot(ts, ma20_w, color="#ff7f0e", linewidth=0.9, label=f"MA{_MA_SHORT}")
        if ma50_w.notna().any():
            ax.plot(ts, ma50_w, color="#2ca02c", linewidth=0.9, label=f"MA{_MA_LONG}")

        ax.set_title(f"{ticker} — 90-day close", fontsize=7, loc="left")
        ax.tick_params(axis="both", labelsize=5, length=2)
        ax.legend(fontsize=5, loc="best", frameon=False)
        ax.margins(x=0.01)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        fig.autofmt_xdate(rotation=0, ha="center")
        fig.tight_layout(pad=0.3)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - a chart is decorative; never fail the report
        try:
            plt.close("all")
        except Exception:  # noqa: BLE001
            pass
        return None
