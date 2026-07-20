"""Yahoo Finance OHLC ingestion and cleaning (§4.1, §4.3).

Pulls daily OHLC for watchlist symbols via `yfinance`, applies the §4.3 cleaning
rules, and writes the result into the DuckDB `prices` cache (§4.2):

  * **Adjusted close** — `auto_adjust=True` so OHLC are split/dividend adjusted
    and `close` is the adjusted close (§4.3).
  * **Market-calendar reindex** — reindex onto the session grid of the symbol's
    own market (§4.4): Sunday–Thursday for `tase` (the Israeli trading week —
    Friday/Saturday are the weekend, not Sat/Sun), Monday–Friday for `us`.
    Running a US symbol through the TASE grid would silently drop every Friday
    session and manufacture a forward-filled Sunday one, so the grid is resolved
    per symbol rather than assumed.
  * **One-day-gap forward-fill** — isolated single missing sessions are bridged
    by forward fill; runs of two or more missing sessions (genuine multi-day
    closures) are left as gaps and dropped.
  * **Outlier flag** — close-to-close returns beyond 8× MAD are flagged and
    reported. The §4.2 `prices` schema is fixed, so flags are surfaced in the
    ingest report rather than persisted as a new column.

The `/ohlc` and `/indicators` endpoints (Step 2) read from this cache; this
module is the writer.
"""

from __future__ import annotations

import os
import ssl
import tempfile
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data import markets

SOURCE = "yahoo"

# §11.2: external calls are rate-limit-aware. This is a defensive backoff for a
# genuine HTTP 429 from Yahoo; in this environment the far more common failure is
# a TLS-trust problem that yfinance *mislabels* as "rate limited" — that one is
# handled up front by `configure_tls()`, not here.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 15.0

# §4.3: flag close-to-close returns beyond this many MADs from the median return.
OUTLIER_MAD_MULTIPLE = 8.0


@dataclass
class IngestResult:
    """Per-symbol summary returned by `ingest_symbol` (for the ingest report)."""

    symbol: str
    rows_fetched: int = 0
    rows_written: int = 0
    sessions_filled: int = 0
    sessions_dropped: int = 0
    outliers: list[str] = field(default_factory=list)
    start: str | None = None
    end: str | None = None
    status: str = "ok"
    note: str | None = None


def configure_tls() -> None:
    """Make yfinance's curl_cffi backend trust the OS/corporate root CAs.

    yfinance fetches over `curl_cffi`, whose native libcurl reads its trust
    anchors from a CA bundle file — it ignores Python's `ssl`/`truststore`. On a
    Windows machine behind a TLS-inspecting proxy/AV, the corporate root CA is in
    the Windows store but NOT in certifi's bundle, so libcurl rejects every
    Yahoo connection with a certificate error (which yfinance can surface as a
    misleading "rate limited"). We build a bundle = certifi + the Windows
    ROOT/CA stores and point libcurl at it via `CURL_CA_BUNDLE`. Verification
    stays ON — no insecure `verify=False`. No-op (harmless) on platforms without
    a Windows certificate store, and we respect a pre-set `CURL_CA_BUNDLE`.
    """
    if os.environ.get("CURL_CA_BUNDLE"):  # respect an explicit override
        return

    import certifi

    parts = [certifi.contents()]
    enum_certificates = getattr(ssl, "enum_certificates", None)
    if enum_certificates is not None:  # Windows-only API
        for store in ("ROOT", "CA"):
            try:
                for cert, encoding, _ in enum_certificates(store):
                    if encoding == "x509_asn":
                        parts.append(ssl.DER_cert_to_PEM_cert(cert))
            except Exception:  # noqa: BLE001 - a missing store must not break ingest
                continue

    bundle_path = os.path.join(tempfile.gettempdir(), "yahoo_ca_bundle.pem")
    try:
        with open(bundle_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts))
    except OSError:
        return  # fall back to certifi defaults rather than crash

    # curl_cffi/libcurl and requests both honour these env vars.
    os.environ["CURL_CA_BUNDLE"] = bundle_path
    os.environ["SSL_CERT_FILE"] = bundle_path
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_path


def fetch_ohlc(symbol: str, lookback_days: int, interval: str = "1d") -> pd.DataFrame:
    """Fetch raw adjusted OHLC from Yahoo Finance.

    Returns a DataFrame indexed by trading date with columns
    open/high/low/close/volume. Raises on network/parse failure so the caller
    can mark the symbol `degraded` (§9.4) rather than fabricate data.
    """
    configure_tls()  # must run before the curl_cffi session is used

    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError

    # Default curl_cffi backend: it impersonates a browser TLS fingerprint, which
    # is what Yahoo's cookie/crumb flow expects. A plain `requests` session fails
    # that handshake (401 → mislabelled "rate limited"), so we do not pass one.
    ticker = yf.Ticker(symbol)

    raw = None
    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        try:
            raw = ticker.history(
                period=f"{lookback_days}d",
                interval=interval,
                auto_adjust=True,  # §4.3: adjusted close (and adjusted OHLC)
            )
            break
        except YFRateLimitError:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)

    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no rows for {symbol}")

    raw = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )[["open", "high", "low", "close", "volume"]]

    # Daily bars: index is tz-aware at the exchange tz; collapse to the session
    # date (naive). Intraday handling is out of scope for Step 1.
    idx = pd.DatetimeIndex(raw.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    raw.index = idx.normalize()
    raw.index.name = "ts"
    return raw


def _sessions(
    start: pd.Timestamp, end: pd.Timestamp, closed: tuple[int, ...]
) -> pd.DatetimeIndex:
    """Trading-day calendar dates spanning [start, end] inclusive.

    `closed` is the market's non-trading weekdays in pandas numbering
    (Mon=0…Sun=6), from `markets.closed_weekdays` (§4.4).
    """
    days = pd.date_range(start=start, end=end, freq="D")
    return days[~days.weekday.isin(closed)]


def clean_ohlc(raw: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, IngestResult]:
    """Apply §4.3 cleaning and return (prices-shaped frame, ingest report).

    The returned frame has exactly the §4.2 `prices` columns and is ready for
    `cache.upsert_prices`.
    """
    result = IngestResult(symbol=symbol, rows_fetched=len(raw))
    if raw.empty:
        result.status = "degraded"
        result.note = "no rows fetched"
        return _empty_prices_frame(), result

    df = raw.sort_index()

    # 1) Reindex onto the session grid of the symbol's own market (§4.3, §4.4):
    #    Sun–Thu for tase, Mon–Fri for us.
    closed = markets.closed_weekdays(markets.market(symbol))
    sessions = _sessions(df.index.min(), df.index.max(), closed)
    reindexed = df.reindex(sessions)

    # 2) Classify missing sessions into single-day gaps vs multi-day closures.
    missing = reindexed["close"].isna()
    run_id = (missing != missing.shift()).cumsum()
    run_len = missing.groupby(run_id).transform("size")
    single_gap = missing & (run_len == 1)
    multi_gap = missing & (run_len > 1)

    # 3) Forward-fill, then drop the multi-day gaps (kept as genuine gaps) and
    #    any leading rows that had no prior value to fill from.
    filled = reindexed.ffill()
    filled = filled[~multi_gap]
    filled = filled.dropna(subset=["close"])  # drops leading NaNs

    # A single-day gap counts as "filled" only if it survived (a leading single
    # gap has no prior value and is dropped by the dropna above).
    surviving = set(filled.index)
    result.sessions_filled = int(
        sum(1 for ts in single_gap.index[single_gap] if ts in surviving)
    )
    result.sessions_dropped = int(multi_gap.sum())

    # 4) Outlier flag: close-to-close returns beyond 8× MAD (§4.3).
    result.outliers = _flag_return_outliers(filled["close"])

    out = _to_prices_frame(filled, symbol)
    result.rows_written = len(out)
    if not out.empty:
        result.start = str(out["ts"].iloc[0].date())
        result.end = str(out["ts"].iloc[-1].date())
    return out, result


def _flag_return_outliers(close: pd.Series) -> list[str]:
    """Return the dates whose close-to-close return is beyond 8× MAD (§4.3)."""
    returns = close.pct_change().dropna()
    if returns.empty:
        return []
    median = returns.median()
    mad = (returns - median).abs().median()
    if mad == 0 or np.isnan(mad):
        return []
    flagged = returns[(returns - median).abs() > OUTLIER_MAD_MULTIPLE * mad]
    return [str(ts.date()) for ts in flagged.index]


def _to_prices_frame(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Shape a cleaned OHLC frame into the §4.2 `prices` columns."""
    if df.empty:
        return _empty_prices_frame()
    out = df.reset_index().rename(columns={"index": "ts"})
    if "ts" not in out.columns:
        out = out.rename(columns={out.columns[0]: "ts"})
    out["symbol"] = symbol
    out["source"] = SOURCE
    out["volume"] = out["volume"].round().astype("int64")
    out["ts"] = pd.to_datetime(out["ts"])
    return out[["symbol", "ts", "open", "high", "low", "close", "volume", "source"]]


def _empty_prices_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["symbol", "ts", "open", "high", "low", "close", "volume", "source"]
    )


def ingest_symbol(
    con, symbol: str, lookback_days: int, interval: str = "1d"
) -> IngestResult:
    """Fetch → clean → upsert one symbol. Returns the ingest report.

    On a fetch failure the symbol is reported `degraded` (§9.4) with the reason;
    no rows are fabricated.
    """
    from data import cache

    try:
        raw = fetch_ohlc(symbol, lookback_days, interval)
    except Exception as exc:  # noqa: BLE001 - degrade, never fabricate (§9.4)
        return IngestResult(symbol=symbol, status="degraded", note=str(exc))

    cleaned, result = clean_ohlc(raw, symbol)
    result.rows_written = cache.upsert_prices(con, cleaned)
    return result
