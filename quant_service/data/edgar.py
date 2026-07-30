"""SEC EDGAR disclosure fetching — the US earnings source (§3.2, §4.1).

The market twin of `data/maya.py`. Where Maya is a JavaScript SPA behind bot
protection that needs a headless browser, EDGAR publishes a free JSON API, so
this module is plain `httpx`: **no Playwright**.

Three hops, each a documented public endpoint:

1. ``www.sec.gov/files/company_tickers.json`` — ticker → CIK. Rarely changes,
   so it is cached for a day and shared across tickers.
2. ``data.sec.gov/submissions/CIK##########.json`` — the issuer's recent
   filings as parallel arrays (``form``, ``filingDate``,
   ``acceptanceDateTime``, ``accessionNumber``, ``items``, …). Filtered to
   8-K / 10-Q / 10-K inside the disclosure window.
3. ``…/Archives/edgar/data/<cik>/<accn>/<accn>-index.htm`` — the filing index,
   whose table names each document's **exhibit type**. The ``EX-99.*``
   exhibit is the press release carrying revenue, EPS and guidance; its text
   is the bounded ``excerpt`` the §3.2 self-consistency extraction reads.

Hop 3 is the EDGAR analogue of Maya's PDF attachment: the filing's *primary*
document is a cover page ("On May 1 the registrant issued a press release,
attached as Exhibit 99.1"), so an excerpt taken from it would contain no figure
and every field would vote ``ambiguous`` (§3.2). The figures live in the exhibit.

Everything the Earnings Agent sees is identical to the Maya path — same item
shape, same ranking (`maya.rank_items`), same bounded excerpt — so the n8n
sub-workflow is market-agnostic and needed no changes for this source (§5).

SEC policy requires a declared ``User-Agent`` carrying a contact address
(``EDGAR_USER_AGENT``, §11.1) and asks for ≤10 requests/second; both are
enforced below. Nothing here raises: failures come back as
``(partial_items, errors)`` and the caller degrades (§9.4) — never fabricates.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from data import maya
from data.maya import PRESS_RELEASE, PRIMARY_DOCUMENT
from data.textclean import clean_text

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}"
_FILING_INDEX_URL = _ARCHIVE_BASE + "/{accn}-index.htm"

# §4.1: 8-K carries results press releases and material events; 10-Q/10-K are
# the periodic reports. Amendments ("8-K/A") count as their base form — an
# amended results filing is still a results filing.
_FORMS = {"8-K", "10-Q", "10-K"}
# The periodic reports: their primary document *is* the financial statement.
_PERIODIC_FORMS = {"10-Q", "10-K"}

_ET_TZ = ZoneInfo("America/New_York")

_TIMEOUT_S = 30
# SEC asks for <=10 requests/second from a declared client. One ticker costs a
# handful of calls, so a simple global minimum spacing is enough — and it is
# global (not per-call) because the orchestrator fans out over three tickers at
# a time (§6.1) and the limit is per *client*, not per thread.
_MIN_REQUEST_INTERVAL_S = 0.12

_TTL_SECONDS = 600  # per (ticker, window) — same cycle-level reuse as Maya
_TICKER_MAP_TTL_SECONDS = 86_400  # the CIK map moves on the order of weeks

# The exhibit type that holds the press release. EDGAR types are "EX-99",
# "EX-99.1", "EX-99.01"… — the number after 99 is just an ordinal.
_EX99_TYPE_RE = re.compile(r"^ex-?99", re.I)
# Filename fallback for filings whose index table cannot be parsed: exhibits are
# conventionally named ex99*.htm / d123456dex991.htm / a8-kex991.htm.
_EX99_NAME_RE = re.compile(r"ex[-_]?99", re.I)
_EX99_SUFFIXES = (".htm", ".html", ".txt")
# iXBRL documents are linked through a viewer; the raw file is the doc= param.
_IX_PREFIX_RE = re.compile(r"^/ix\?doc=", re.I)

# Excerpt anchoring. Maya anchors on the first PDF *page* carrying >=3 money
# matches; an HTML exhibit has no pages, so the same rule is applied over a
# character span: the excerpt starts at the first figures *cluster*, backing up
# a little so the sentence introducing it survives. A press release with no
# cluster (a governance 8-K) falls back to its opening — the §3.2 vote then
# marks every field "ambiguous", which is the correct answer, not a failure.
_CLUSTER_SPAN = 2_000
_MIN_MONEY_CLUSTER = 3
_ANCHOR_LEAD = 500
# Bound on the HTML pulled before cleaning: a 10-Q/10-K primary document is
# iXBRL running to tens of megabytes, and only its opening can ever reach the
# LLM anyway (EXCERPT_MAX). Generous enough to cover a press release whole and
# to reach a periodic report's condensed financial statements.
_RAW_MAX_BYTES = 4_000_000

# 8-K item codes -> the wording the §3.2 ranking reads. Item 9.01 ("Financial
# Statements and Exhibits") is deliberately DROPPED: it is attached to nearly
# every 8-K, results or not, and its wording would score every 8-K as a results
# filing — flattening the ranking that exists to find the material one.
_ITEM_DESCRIPTIONS: Dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.02": "Departure or Election of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}
_BOILERPLATE_ITEMS = {"9.01"}

_FORM_DESCRIPTIONS = {
    "10-Q": "Quarterly report",
    "10-K": "Annual report",
}

# "ticker|window|candidates" -> (fetched_at_monotonic, items, errors). Failures
# are cached too — an unknown ticker or a 403 will not resolve inside the TTL,
# and retrying per ticker only adds load (same rule as Maya). The one exception
# is an unset User-Agent, checked before the cache in `fetch_disclosures`.
_result_cache: dict[str, Tuple[float, List[dict], List[str]]] = {}
_cache_lock = threading.Lock()

_ticker_map: Optional[Tuple[float, Dict[str, int]]] = None
_ticker_map_lock = threading.Lock()

_rate_lock = threading.Lock()
_last_request_at = 0.0


def fetch_disclosures(
    ticker: str, window_days: int, candidates: int = 3
) -> Tuple[List[dict], List[str]]:
    """Return (items, errors) for a US ticker's recent EDGAR filings.

    Item shape is identical to `maya.fetch_disclosures` (§5 — the response
    contract does not vary by market):
        {id, symbol, published_at (UTC ISO), title, url, language, rank_score,
         excerpt}
    Items are ordered by §3.2 relevance rank, then recency; `excerpt` is
    populated for the top `candidates` and "" for the rest. Never raises.
    """
    if not user_agent():
        # Checked ahead of the cache, unlike every other failure: SEC blocks
        # unidentified clients outright, and guessing a contact address would be
        # both a policy breach and a fabrication (§11.1). It is also the one
        # failure a config edit fixes instantly, so caching it would leave the
        # operator staring at a stale error for the whole TTL after the fix.
        return [], [
            "edgar: EDGAR_USER_AGENT is not set — SEC requires a declared "
            "User-Agent with a contact email (see .env.example)"
        ]

    key = f"{ticker}|{window_days}|{candidates}"
    with _cache_lock:
        hit = _result_cache.get(key)
        if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
            return list(hit[1]), list(hit[2])
    items, errors = _fetch(ticker, window_days, candidates)
    with _cache_lock:
        _result_cache[key] = (time.monotonic(), items, errors)
    return list(items), list(errors)


def clear_caches() -> None:
    """Testing hook: drop both TTL caches."""
    global _ticker_map
    with _cache_lock:
        _result_cache.clear()
    with _ticker_map_lock:
        _ticker_map = None


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------


def user_agent() -> str:
    """The SEC-required declared User-Agent (§11.1); "" when unconfigured."""
    return (os.environ.get("EDGAR_USER_AGENT") or "").strip()


def _client():
    """An httpx client carrying the declared UA and OS-trust TLS."""
    import httpx  # local import: keeps module import cheap

    from data import tls  # OS-trust context: corporate TLS interception

    return httpx.Client(
        timeout=_TIMEOUT_S,
        follow_redirects=True,
        verify=tls.ssl_context(),
        headers={
            "User-Agent": user_agent(),
            "Accept-Encoding": "gzip, deflate",
        },
    )


def _throttle() -> None:
    """Space requests to stay inside SEC's ~10 req/s fair-access policy."""
    global _last_request_at
    with _rate_lock:
        wait = _MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _get(client, url: str):
    """One throttled GET. Raises on transport errors; caller degrades."""
    _throttle()
    return client.get(url)


def _get_bounded(client, url: str) -> Tuple[int, str]:
    """Throttled GET of a document, reading at most `_RAW_MAX_BYTES`.

    A 10-Q's primary document is iXBRL HTML that routinely runs to tens of
    megabytes, of which only the opening (cover page + condensed financial
    statements) can ever reach the excerpt. Streaming with a cap keeps a large
    filing from dominating the run's time and memory.
    """
    _throttle()
    chunks: List[bytes] = []
    size = 0
    with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            return resp.status_code, ""
        for chunk in resp.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= _RAW_MAX_BYTES:
                break
        return resp.status_code, b"".join(chunks).decode("utf-8", errors="replace")


def _reason(exc: Exception) -> str:
    return str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__


# --------------------------------------------------------------------------
# ticker -> CIK
# --------------------------------------------------------------------------


def _load_ticker_map(client) -> Dict[str, Tuple[int, str]]:
    """Ticker -> (CIK, registrant name) from company_tickers.json, cached a day.

    The registered name is kept alongside the CIK because it is the only
    company-name source the system has for a US ticker, and `/news/fetch` uses
    it to search for coverage of a name absent from `search_terms` (§3.1).
    """
    global _ticker_map
    with _ticker_map_lock:
        if _ticker_map and time.monotonic() - _ticker_map[0] < _TICKER_MAP_TTL_SECONDS:
            return _ticker_map[1]

    resp = _get(client, _TICKERS_URL)
    if resp.status_code != 200:
        raise RuntimeError(f"{_TICKERS_URL} -> HTTP {resp.status_code}")
    payload = resp.json()
    # Shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, …}
    mapping: Dict[str, Tuple[int, str]] = {}
    for entry in (payload or {}).values():
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("ticker") or "").strip().upper()
        cik = entry.get("cik_str")
        if symbol and cik is not None:
            mapping[symbol] = (int(cik), str(entry.get("title") or "").strip())
    if not mapping:
        raise RuntimeError(f"{_TICKERS_URL} -> no ticker/CIK pairs in payload")

    with _ticker_map_lock:
        _ticker_map = (time.monotonic(), mapping)
    return mapping


def company_search_terms(ticker: str) -> Tuple[List[str], List[str]]:
    """Return (terms, errors): news search terms derived from the SEC registrant name.

    The fallback behind `/news/fetch` for a US ticker with no hand-tuned
    `search_terms` entry (§3.1, §4.4). Without it a name the config has never
    seen — which is most of the S&P 500, reachable ad-hoc through the chat
    assistant (§6.5) — gets no news at all, so its Sentiment agent degrades and
    §3.4 caps conviction at `medium` for a company that is in fact well covered.

    A derived term is deliberately **weaker** than a hand-tuned one and never
    overrides it: `search_terms` is checked first. SEC titles are registry
    strings, not brands ("NVIDIA CORP", "KEYCORP /NEW/", "JPMORGAN CHASE & CO"),
    so the corporate suffix is stripped to leave the searchable name. That is
    lossy for names whose stripped form is an ordinary English word — "GAP INC"
    becomes "Gap" — and those keep the collision problem that hand-tuning exists
    to solve (§13). Thin or noisy coverage still degrades honestly; it is never
    fabricated, so the fallback can only improve on returning nothing.

    Never raises: any failure yields ([], [reason]) and the caller degrades.
    """
    if not user_agent():
        return [], [
            "edgar: EDGAR_USER_AGENT is not set — cannot resolve a company name "
            f"for {ticker} (see .env.example)"
        ]
    try:
        with _client() as client:
            entry = _load_ticker_map(client).get(ticker.strip().upper())
    except Exception as exc:  # noqa: BLE001 - registry failure degrades
        return [], [f"edgar tickers: {_reason(exc)}"]

    if not entry or not entry[1]:
        return [], [f"edgar: no SEC registrant name for {ticker}"]

    term = _clean_registrant_name(entry[1])
    if not term:
        return [], [f"edgar: registrant name for {ticker} is empty after cleaning"]
    return [term], []


# EDGAR annotations on a registrant name: "KEYCORP /NEW/", "XYZ CORP /DE/".
# The closing slash is optional — "COSTCO WHOLESALE CORP /NEW" occurs too.
_REGISTRY_ANNOTATION_RE = re.compile(r"/[A-Z]{2,}/?")
# Corporate-form suffixes, stripped from the END only — repeatedly, since titles
# stack them ("META PLATFORMS, INC.", "ELI LILLY & Co").
#
# The `\b` on BOTH sides is load-bearing, not decoration: without the leading one
# the alternation matches mid-word and eats part of the name — "KEYCORP" became
# "KEY" and "VISA" became "VI" (both measured). Without the trailing one, `co`
# matches the start of "CORP". It sits AFTER the optional `&` because `&` is not
# a word character, so a `\b` before it never matches and "JPMORGAN CHASE & CO"
# kept a dangling ampersand.
_CORPORATE_SUFFIX_RE = re.compile(
    r"[\s,\.]*(?:&\s*)?\b(?:inc|incorporated|corp|corporation|co|company|companies"
    r"|ltd|limited|llc|l\.?l\.?c|lp|l\.?p|plc|nv|n\.?v|sa|s\.?a|ag|holdings?"
    r"|group|the)\b[\s,\.]*$",
    re.I,
)


def _clean_registrant_name(title: str) -> str:
    """"NVIDIA CORP" -> "NVIDIA"; "META PLATFORMS, INC." -> "META PLATFORMS"."""
    name = _REGISTRY_ANNOTATION_RE.sub(" ", title)
    name = re.sub(r"\s+", " ", name).strip(" ,.")
    # Strip stacked suffixes, but never strip the name away entirely: a company
    # actually called "The Gap" or "Group 1" must keep something to search for.
    while True:
        stripped = _CORPORATE_SUFFIX_RE.sub("", name).strip(" ,.")
        if not stripped or stripped == name:
            break
        name = stripped
    return name


# --------------------------------------------------------------------------
# Filings
# --------------------------------------------------------------------------


def _fetch(
    ticker: str, window_days: int, candidates: int
) -> Tuple[List[dict], List[str]]:
    """Resolve, list, rank and excerpt a ticker's filings. Never raises."""
    errors: List[str] = []

    try:
        import httpx  # noqa: F401  - fail here rather than deep in the call tree
    except ImportError as exc:
        return [], [f"edgar: dependency missing ({exc})"]

    try:
        with _client() as client:
            try:
                entry = _load_ticker_map(client).get(ticker.strip().upper())
            except Exception as exc:  # noqa: BLE001 - lookup failure degrades
                return [], [f"edgar tickers: {_reason(exc)}"]
            cik = entry[0] if entry else None
            if cik is None:
                return [], [
                    f"edgar: no CIK for {ticker} in company_tickers.json "
                    "(not an SEC registrant?)"
                ]

            try:
                resp = _get(client, _SUBMISSIONS_URL.format(cik=cik))
                if resp.status_code != 200:
                    return [], [
                        f"edgar submissions: CIK{cik:010d} -> HTTP {resp.status_code}"
                    ]
                submissions = resp.json()
            except Exception as exc:  # noqa: BLE001
                return [], [f"edgar submissions: {_reason(exc)}"]

            items = _recent_items(submissions, ticker, cik, window_days)
            items = maya.rank_items(items)

            # §3.2 step 1: spend an exhibit fetch only on the candidates that
            # will actually be classified.
            for item in items[:candidates]:
                excerpt, layer, exc_errors = _filing_excerpt(
                    client, item["url"], item["_primary_url"]
                )
                item["excerpt"] = excerpt
                item["excerpt_source"] = layer if excerpt else ""
                # Only an excerpt-less candidate degrades the fetch (§3.2) — the
                # EX-99 → primary-document fallback still yields verbatim text,
                # and `excerpt_source` already says which layer it came from.
                if not excerpt:
                    errors.extend(exc_errors)

            for item in items:
                item.pop("_primary_url", None)
            return items, errors
    except Exception as exc:  # noqa: BLE001 - any client failure degrades
        return [], errors + [f"edgar: {_reason(exc)}"]


def _recent_items(
    submissions: dict, ticker: str, cik: int, window_days: int
) -> List[dict]:
    """Map `filings.recent` parallel arrays to §5 items inside the window."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    filing_dates = recent.get("filingDate") or []
    acceptances = recent.get("acceptanceDateTime") or []
    report_dates = recent.get("reportDate") or []
    items_col = recent.get("items") or []
    primary_docs = recent.get("primaryDocument") or []

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    out: List[dict] = []
    seen_urls: set = set()

    for i, form in enumerate(forms):
        form = str(form or "").strip().upper()
        if form.split("/")[0] not in _FORMS:  # "8-K/A" counts as "8-K"
            continue
        accn = str(accessions[i] if i < len(accessions) else "").strip()
        if not accn:
            continue

        published = _published_at(
            acceptances[i] if i < len(acceptances) else "",
            filing_dates[i] if i < len(filing_dates) else "",
        )
        # An unparseable date is kept (never silently drop a real filing) and
        # sorts last, exactly as on the Maya path.
        if published is not None and published < cutoff:
            continue

        accn_nodash = accn.replace("-", "")
        index_url = _FILING_INDEX_URL.format(
            cik=cik, accn_nodash=accn_nodash, accn=accn
        )
        if index_url in seen_urls:
            continue
        seen_urls.add(index_url)

        # Only a periodic report's primary document is a figures source (see
        # `_filing_excerpt`); an 8-K's is the cover note, so it stays unset and
        # a press-release-less 8-K correctly yields no excerpt.
        primary = str(primary_docs[i] if i < len(primary_docs) else "").strip()
        primary_url = (
            _ARCHIVE_BASE.format(cik=cik, accn_nodash=accn_nodash) + "/" + primary
            if primary and form.split("/")[0] in _PERIODIC_FORMS
            else None
        )

        out.append(
            {
                "id": hashlib.sha1(f"{ticker}|{index_url}".encode("utf-8")).hexdigest(),
                "symbol": ticker,
                "published_at": published.isoformat() if published else None,
                "title": _title(
                    form,
                    items_col[i] if i < len(items_col) else "",
                    report_dates[i] if i < len(report_dates) else "",
                ),
                "url": index_url,
                "language": "en",  # EDGAR filings are English by statute
                "excerpt": "",
                "excerpt_source": "",
                # Internal (stripped before returning): the excerpt's second
                # layer, used when the filing attaches no EX-99 exhibit.
                "_primary_url": primary_url,
            }
        )

    out.sort(key=lambda i: i["published_at"] or "0000", reverse=True)
    return out


def _published_at(acceptance: str, filing_date: str) -> Optional[datetime]:
    """UTC timestamp of a filing (§11.2 — store UTC).

    `acceptanceDateTime` is an explicit UTC instant and is preferred. A bare
    `filingDate` has no time, so it is read as the start of that day in
    America/New_York — EDGAR's own filing-day boundary — rather than assumed UTC.
    """
    raw = (acceptance or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    raw = (filing_date or "").strip()
    if raw:
        try:
            day = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None
        return day.replace(tzinfo=_ET_TZ).astimezone(timezone.utc)
    return None


def _title(form: str, items: str, report_date: str) -> str:
    """A human-readable filing title — the text the §3.2 ranking scores.

    Built from the form plus its 8-K item descriptions, because the submissions
    feed carries no title of its own and a bare "8-K" says nothing about whether
    the filing is a results release or a director departure.
    """
    parts = [form]
    described = _FORM_DESCRIPTIONS.get(form.split("/")[0])
    if described:
        parts.append(described)

    codes = [c.strip() for c in str(items or "").split(",") if c.strip()]
    labels = [
        _ITEM_DESCRIPTIONS.get(code, f"Item {code}")
        for code in codes
        if code not in _BOILERPLATE_ITEMS
    ]
    if labels:
        parts.append("; ".join(labels))

    title = " — ".join(parts)
    period = str(report_date or "").strip()
    return f"{title} (period {period})" if period else title


# --------------------------------------------------------------------------
# EX-99.* press-release excerpt
# --------------------------------------------------------------------------


def _filing_excerpt(
    client, index_url: str, primary_url: Optional[str]
) -> Tuple[str, str, List[str]]:
    """Bounded verbatim text of a filing, for the §3.2 extraction. Never raises.

    Two layers, in order (§4.1 — the EDGAR analogue of Maya's PDF ladder):

    1. the **EX-99.\\* exhibit**, i.e. the press release an 8-K attaches. This is
       where a results filing's revenue, EPS and guidance actually appear: the
       8-K's own document is a cover note saying "a press release is attached
       as Exhibit 99.1" and carries no figure at all.
    2. the **primary document**, but only for a **periodic report** (10-Q/10-K),
       which *is* the financial statement. An 8-K's primary document is never a
       second chance at the figures — it is the cover note above, and when the
       8-K attaches no press release there is simply nothing to quote. Reading
       it anyway returns the iXBRL cover page, whose hidden tags flatten into
       runs of "true true NASDAQ 0000320193" — noise that costs LLM budget and
       teaches the model nothing (measured on Apple's director-election 8-K).

    Returns (excerpt, excerpt_source, errors) where `excerpt_source` names the
    layer the text came from (§5) — the §3.2 selection prefers a press release
    over a periodic report's statements, so it has to know which it got.

    Any failure returns ("", "", [reason]): extraction then sees only the title
    and every figure votes "ambiguous" — never invented (§9.4, §13).
    """
    try:
        resp = _get(client, index_url)
        if resp.status_code != 200:
            return "", "", [f"edgar index: {index_url} -> HTTP {resp.status_code}"]
        exhibit_url = _find_ex99(resp.text, index_url)
        source_url = exhibit_url or primary_url
        layer = PRESS_RELEASE if exhibit_url else PRIMARY_DOCUMENT
    except Exception as exc:  # noqa: BLE001
        return "", "", [f"edgar index: {index_url} -> {_reason(exc)}"]

    if not source_url:
        # Not an error: a filing with neither an exhibit nor a primary document
        # is simply one with no text to quote. Reporting it would mark an
        # otherwise-healthy agent "degraded" (§9.4); the empty excerpt already
        # makes the §3.2 vote answer "ambiguous", and `/earnings/fetch`'s
        # summary reports how many candidates carry text.
        return "", "", []

    try:
        status, raw = _get_bounded(client, source_url)
        if status != 200:
            return "", "", [f"edgar document: {source_url} -> HTTP {status}"]
    except Exception as exc:  # noqa: BLE001
        return "", "", [f"edgar document: {source_url} -> {_reason(exc)}"]

    text = clean_text(raw)
    if not text:
        return "", "", [f"edgar document: {source_url} -> no extractable text"]

    start = _anchor(text)
    excerpt = clean_text(text[start : start + maya.EXCERPT_MAX], maya.EXCERPT_MAX)
    return excerpt, layer, []


def _find_ex99(html: str, index_url: str) -> Optional[str]:
    """Absolute URL of the EX-99.* document listed on a filing index page.

    The index table's "Type" column is authoritative, so it is read first; the
    filename convention is a fallback for a table whose shape has drifted.
    """
    base = index_url.rsplit("/", 1)[0]
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        if not any(_EX99_TYPE_RE.match(c.get_text(" ", strip=True)) for c in cells):
            continue
        for anchor in row.find_all("a", href=True):
            href = _absolute(anchor["href"], base)
            if href and href.lower().endswith(_EX99_SUFFIXES):
                return href

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        name = href.rsplit("/", 1)[-1]
        if _EX99_NAME_RE.search(name) and name.lower().endswith(_EX99_SUFFIXES):
            return _absolute(href, base)
    return None


def _absolute(href: str, base: str) -> Optional[str]:
    """Resolve an index-page href, unwrapping the iXBRL viewer link."""
    href = (href or "").strip()
    if not href:
        return None
    href = _IX_PREFIX_RE.sub("", href)
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.sec.gov" + href
    return f"{base}/{href}"


def _anchor(text: str) -> int:
    """Offset where the excerpt should start: where the figures actually are.

    A filing opens with cover-page and boilerplate — pages of it in a periodic
    report — so the first `EXCERPT_MAX` characters need not hold a single
    figure. The excerpt therefore starts at the first **cluster of money
    amounts**: the same rule Maya applies per PDF page (>= 3 matches), expressed
    over a character span since HTML has no pages.

    An income-statement *heading* anchor was tried and removed. It read well in
    theory and measured worse: in a press release the statements are appended
    after the narrative, so anchoring on them skipped the labelled prose figures
    ("Revenue was $82.9 billion") that are the best extraction source in the
    document, and in a 10-Q the first heading match is the table of contents.

    No cluster means there are no headline figures to find (a governance 8-K):
    the excerpt falls back to the document's opening and the §3.2 vote marks
    every field "ambiguous", which is the correct answer, not a failure.
    """
    positions = [m.start() for m in maya.MONEY_RE.finditer(text)]
    if not positions:
        return 0
    for n, pos in enumerate(positions):
        cluster = sum(1 for p in positions[n:] if p - pos <= _CLUSTER_SPAN)
        if cluster >= _MIN_MONEY_CLUSTER:
            return max(0, pos - _ANCHOR_LEAD)
    return max(0, positions[0] - _ANCHOR_LEAD)
