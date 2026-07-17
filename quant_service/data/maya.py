"""Maya (maya.tase.co.il) disclosure scraping, EN primary / HE fallback (§3.2, §4.1).

The Maya reports page is a JavaScript SPA behind Imperva bot protection: its
HTML is an empty shell and direct HTTP calls to the JSON API are blocked. The
page is therefore loaded in a headless Playwright Chromium — which satisfies
the bot protection — and disclosures are then fetched **through the page
context** with the SPA's own list API (verified live):

    POST /api/v1/reports/companies
    {"pageNumber":1, "limit":<=30, "offset":0, "freeText":"<term>",
     "fromDate":"YYYY-MM-DD"}

`freeText` is a server-side search over company names and report contents, so
each watchlist ticker is queried directly by its `search_terms` (EN + HE both
work). Because the search also matches document contents, rows are re-checked
locally against the ticker's terms before being kept.

Resilience ladder (all best-effort per §13/§14 — never raises, never
fabricates; failures surface as `(partial_items, errors)` and the caller
degrades):

1. **Per-term API queries** through the page context (primary).
2. **Passive XHR harvest** — the JSON the SPA itself loaded while rendering,
   term-filtered (used if the query API changes shape).
3. **DOM fallback** — rendered report-link rows, dates regex-parsed.
4. **Hebrew page** — same ladder, if the English page itself fails to load.

Results are TTL-cached per (ticker, window) so re-runs inside an orchestrator
cycle don't relaunch browsers. §4.3: dedupe by (symbol, url) →
id = sha1("symbol|url"). The newest matching disclosure also gets a bounded
plain-text `excerpt` — the verbatim source for the §3.2 self-consistency
extraction.

**The excerpt comes from the disclosure's PDF attachment, not its report page.**
Maya publishes a disclosure across three layers and only the last has figures
(§3.2): the report page is an SPA shell whose visible text is navigation, the
report-list sidebar and a live stock quote; its iframe holds a ~1 KB MAGNA cover
sheet naming an attachment; the attachment itself — `mayafiles.tase.co.il/rpdf/
<bucket>/P<id>-00.pdf` — is the press release carrying revenue, EPS and
guidance. Reading only the page (as this module originally did) yields an
excerpt in which no figure is ever present, so every field votes "ambiguous" and
§3.2's self-consistency never commits — intact, but never exercised. Worse, that
page text *does* contain decoy numbers (the quote's "Last Rate"), so the vote is
actively rejecting plausible wrong answers. See `_pdf_excerpt`.
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from data.textclean import clean_text, mentions_term

LIST_URLS = {
    "en": "https://maya.tase.co.il/en/reports/companies",
    "he": "https://maya.tase.co.il/reports/companies",
}
_DETAILS_PREFIX = {
    "en": "https://maya.tase.co.il/en/reports/details/",
    "he": "https://maya.tase.co.il/reports/details/",
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# The disclosure's PDF attachment — the only layer carrying financial figures
# (§3.2). Served by a plain file host (no Imperva), so httpx suffices; the
# report id's bucket is its enclosing 1000 (1737984 -> "1737001-1738000").
_PDF_URL = "https://mayafiles.tase.co.il/rpdf/{bucket}/P{rid}-00.pdf"
_REPORT_ID_RE = re.compile(r"/reports/(?:details/)?(\d+)")
_PDF_TIMEOUT_S = 60

# A currency amount with a magnitude word, or an explicit per-share figure.
# Deliberately strict: it selects *where in the PDF the excerpt starts*, so a
# loose pattern would anchor on legal boilerplate ("Form S-8", "Rule 13a-16")
# instead of the results summary (verified against NICE's Q1 6-K).
# Hebrew is matched too (§3.2 HE fallback): there the magnitude word *follows*
# the number ("1.5 מיליארד ₪"), and the majority vote already normalizes those
# units — an EN-only pattern would leave a HE disclosure's figures unanchored
# and every field "ambiguous" despite being present.
_MONEY_RE = re.compile(
    r"[$€₪]\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|bn\b)"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:מיליון|מיליארד)"
    r"|(?:EPS|earnings per share|רווח למניה)[^\n]{0,40}?[$€₪]?\s?\d+\.\d+",
    re.I,
)
_PDF_MIN_MONEY_HITS = 3  # per page, to clear boilerplate false positives
_PDF_PAGE_SPAN = 3  # pages kept from the anchor (highlights + outlook + tables)

_PAGE_TIMEOUT_MS = 45_000
_SETTLE_MS = 3_000  # extra wait after load for late XHRs / list hydration
# Bound on the disclosure text that reaches the LLM (§2). Sized so a results
# press release's highlights *and* its outlook/guidance block both fit — see
# _pdf_excerpt; ~1.5k tokens, still far below any page's full text (30-90k chars).
_EXCERPT_MAX = 6_000
_TTL_SECONDS = 600
_API_LIMIT = 30  # server-enforced maximum page size (verified live)
_MAX_PAGES = 2  # per term; a single company rarely files >60 reports per window

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# "ticker|window_days" -> (fetched_at_monotonic, items, errors). Failures are
# cached too: a bot-block won't clear inside the TTL, and retrying per ticker
# would hammer the site and stall the run.
_result_cache: dict[str, Tuple[float, List[dict], List[str]]] = {}
_cache_lock = threading.Lock()


def fetch_disclosures(
    ticker: str, terms: List[str], window_days: int
) -> Tuple[List[dict], List[str]]:
    """Return (items, errors) for a ticker's recent Maya disclosures.

    Item shape (compact — §2 heavy-data guardrail):
        {id, symbol, published_at (UTC ISO), title, url, language, excerpt}
    `excerpt` is populated only for the newest item (the one the Earnings
    Agent classifies and extracts numbers from); older items carry "".
    Errors are human-readable degrade reasons; items may be partial.
    """
    key = f"{ticker}|{window_days}"
    with _cache_lock:
        hit = _result_cache.get(key)
        if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
            return list(hit[1]), list(hit[2])
        items, errors = _scrape_ticker(ticker, terms, window_days)
        _result_cache[key] = (time.monotonic(), items, errors)
        return list(items), list(errors)


def clear_caches() -> None:
    """Testing hook: drop the TTL cache."""
    with _cache_lock:
        _result_cache.clear()


# --------------------------------------------------------------------------
# One browser session per (ticker, window): list queries + excerpt
# --------------------------------------------------------------------------


def _scrape_ticker(
    ticker: str, terms: List[str], window_days: int
) -> Tuple[List[dict], List[str]]:
    """Drive one headless-browser session for a ticker. Never raises."""
    errors: List[str] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], ["maya: playwright not installed (pip install playwright)"]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale="en-US", user_agent=_USER_AGENT)
                page = context.new_page()
                passive: List = []  # JSON bodies the SPA loads by itself

                def _on_response(resp):
                    try:
                        ctype = resp.headers.get("content-type", "")
                        if "json" in ctype and "/api/" in resp.url:
                            body = resp.json()
                            if isinstance(body, (dict, list)):
                                passive.append(body)
                    except Exception:  # noqa: BLE001 - harvest is opportunistic
                        pass

                page.on("response", _on_response)

                language = "en"
                try:
                    _load_list_page(page, LIST_URLS["en"])
                except Exception as exc:  # noqa: BLE001 - §3.2 HE fallback
                    errors.append(f"maya (en): {_reason(exc)}")
                    language = "he"
                    _load_list_page(page, LIST_URLS["he"])

                # 1. Primary: per-term server-side search via the SPA's API.
                rows = _query_reports(page, terms, window_days, language, errors)

                # 2. Fallback: whatever report lists the SPA loaded passively.
                if not rows:
                    rows = [
                        r
                        for r in _rows_from_entries(_entry_lists(passive), language)
                        if _matches_ticker(r, terms)
                    ]

                # 3. Fallback: the rendered DOM.
                if not rows:
                    rows = [
                        r
                        for r in _rows_from_dom(page, language)
                        if _matches_ticker(r, terms)
                    ]

                items = _rows_to_items(rows, ticker, window_days)

                if items:
                    excerpt, exc_errors = _fetch_excerpt(page, items[0]["url"])
                    items[0]["excerpt"] = excerpt
                    errors.extend(exc_errors)
                return items, errors
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - any Playwright failure degrades
        errors.append(f"maya: {_reason(exc)}")
        return [], errors


def _reason(exc: Exception) -> str:
    text = str(exc)
    if "Executable doesn't exist" in text:
        return "chromium missing; run: python -m playwright install chromium"
    return text.splitlines()[0][:200]


def _load_list_page(page, url: str) -> None:
    page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - SPAs may never go network-idle
        pass
    page.wait_for_timeout(_SETTLE_MS)


# The in-page fetch: runs with the SPA's origin + cookies, so the bot
# protection that blocks direct HTTP is already satisfied.
_QUERY_JS = """async (body) => {
  const resp = await fetch('/api/v1/reports/companies', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  let entries = null;
  try { entries = await resp.json(); } catch (e) { entries = null; }
  return { status: resp.status, entries: Array.isArray(entries) ? entries : null };
}"""


def _query_reports(
    page, terms: List[str], window_days: int, language: str, errors: List[str]
) -> List[dict]:
    """Union of per-term freeText queries; each result locally re-verified.

    fromDate bounds the search to the disclosure window server-side; the API
    rejects future toDate values, so it is omitted (defaults to "now").
    """
    from_date = (datetime.now(_IL_TZ) - timedelta(days=window_days)).strftime(
        "%Y-%m-%d"
    )
    rows: List[dict] = []
    seen_ids: set = set()
    query_failed = False
    for term in terms:
        term = (term or "").strip()
        if not term:
            continue
        for page_number in range(1, _MAX_PAGES + 1):
            body = {
                "pageNumber": page_number,
                "limit": _API_LIMIT,
                "offset": (page_number - 1) * _API_LIMIT,
                "freeText": term,
                "fromDate": from_date,
            }
            try:
                result = page.evaluate(_QUERY_JS, body)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"maya query '{term}': {_reason(exc)}")
                query_failed = True
                break
            entries = result.get("entries")
            if entries is None:
                # 400 = request shape drifted; 403 = bot-block. Either way the
                # passive/DOM fallbacks still get their chance.
                errors.append(f"maya query '{term}': HTTP {result.get('status')}")
                query_failed = True
                break
            for entry in entries:
                rid = entry.get("id")
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                rows.append(_entry_to_row(entry, language))
            if len(entries) < _API_LIMIT:
                break  # no further pages
    matched = [r for r in rows if _matches_ticker(r, terms)]
    if rows and not matched and not query_failed:
        # freeText hit document contents only — treat as no coverage, not error.
        return []
    return matched


# --------------------------------------------------------------------------
# Entry/row mapping and ticker matching
# --------------------------------------------------------------------------

_HEBREW = re.compile(r"[֐-׿]")

# Generic tokens that must not, by themselves, tie a row to a company.
_STOP_TOKENS = {"bank", "ltd", "inc", "corp", "group", "systems", "the", "בע\"מ"}


def _entry_to_row(entry: dict, language: str) -> dict:
    """Map one /api/v1/reports/companies entry to an internal row.

    Verified entry shape: {id, title, publishDate (Asia/Jerusalem wall clock),
    companies: [{name, ...}], ...}.
    """
    title = clean_text(str(entry.get("title") or ""))
    companies = entry.get("companies") or []
    names = [
        clean_text(str(c.get("name") or ""))
        for c in companies
        if isinstance(c, dict)
    ]
    rid = entry.get("id")
    lang = "he" if _HEBREW.search(title) else language
    return {
        "title": title,
        "company": " / ".join(n for n in names if n),
        "published_at": _parse_date(str(entry.get("publishDate") or "")),
        "url": f"{_DETAILS_PREFIX[language]}{rid}" if rid else "",
        "language": lang,
    }


def _entry_lists(payloads: List) -> List[dict]:
    """Flatten passively harvested JSON bodies into report-entry dicts."""
    entries: List[dict] = []
    for body in payloads:
        candidates = body if isinstance(body, list) else []
        for entry in candidates:
            if (
                isinstance(entry, dict)
                and entry.get("id") is not None
                and entry.get("publishDate")
                and (entry.get("title") or entry.get("companies"))
            ):
                entries.append(entry)
    return entries


def _rows_from_entries(entries: List[dict], language: str) -> List[dict]:
    return [r for e in entries if (r := _entry_to_row(e, language))["url"]]


def _matches_ticker(row: dict, terms: List[str]) -> bool:
    """True when the row's company/title text plausibly names the ticker.

    Two tests, either passes:
    - §4.3-style term match over company + title (word-bounded Latin,
      substring Hebrew — see textclean.term_matches).
    - Token match between the terms and the company name: exact token
      equality, or containment when the shorter token has ≥5 chars — so
      config names tolerate Maya's registrar spellings and Hebrew affixes
      ("Hapoalim" ↔ "POALIM BANK") without short-token false positives
      ("NICE" must not match inside "VENICE"). Generic tokens (bank/ltd/…)
      never match alone.
    """
    company = (row.get("company") or "").lower()
    title = (row.get("title") or "").lower()
    if mentions_term(company, title, terms=terms):
        return True
    company_tokens = [
        t for t in re.split(r"[^\w֐-׿]+", company)
        if len(t) >= 3 and t not in _STOP_TOKENS
    ]
    for term in terms:
        for token in re.split(r"[^\w֐-׿]+", (term or "").lower()):
            if len(token) < 3 or token in _STOP_TOKENS:
                continue
            for ctok in company_tokens:
                if token == ctok:
                    return True
                if min(len(token), len(ctok)) >= 5 and (
                    token in ctok or ctok in token
                ):
                    return True
    return False


def _rows_to_items(rows: List[dict], ticker: str, window_days: int) -> List[dict]:
    """§4.3 cleaning: window filter, dedupe by (symbol, url), newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    seen_urls: set = set()
    items: List[dict] = []
    for row in rows:
        url = row.get("url") or ""
        if not url or url in seen_urls:
            continue
        published = row.get("published_at")
        # An unparseable date stays None: the row is kept (never silently drop
        # a real disclosure) and sorts to the end rather than being invented.
        if published is not None and published < cutoff:
            continue
        seen_urls.add(url)
        items.append(
            {
                "id": hashlib.sha1(f"{ticker}|{url}".encode("utf-8")).hexdigest(),
                "symbol": ticker,
                "published_at": published.isoformat() if published else None,
                "title": row.get("title") or row.get("company") or "",
                "url": url,
                "language": row.get("language") or "en",
                "excerpt": "",
            }
        )
    items.sort(key=lambda i: i["published_at"] or "0000", reverse=True)
    return items


def _rows_from_dom(page, language: str) -> List[dict]:
    """Last-resort fallback: rendered report links + their row text."""
    try:
        raw = page.evaluate(
            """() => {
              const seen = new Set();
              const out = [];
              for (const a of document.querySelectorAll('a[href*="/reports/"]')) {
                const href = a.href;
                if (!href || seen.has(href)) continue;
                if (!/reports\\/(details|company)|\\/reports\\/[0-9]/i.test(href)) continue;
                seen.add(href);
                const row = a.closest('tr, li, article, [class*="row"], [class*="card"], [class*="item"]');
                out.push({
                  href,
                  text: (a.innerText || '').trim(),
                  rowText: row ? (row.innerText || '').trim().slice(0, 500) : '',
                });
              }
              return out.slice(0, 300);
            }"""
        )
    except Exception:  # noqa: BLE001 - DOM shape changed; degrade upstream
        return []

    rows: List[dict] = []
    for link in raw or []:
        row_text = clean_text(link.get("rowText") or "")
        title = clean_text(link.get("text") or "") or row_text[:120]
        if not title:
            continue
        rows.append(
            {
                "title": title,
                # Company isn't separable in the DOM path; term matching runs
                # over the whole row text instead.
                "company": row_text,
                "published_at": _parse_date(row_text),
                "url": link["href"],
                "language": "he" if _HEBREW.search(title) else language,
            }
        )
    return rows


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")
_IL_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\D{0,3}(\d{1,2}):(\d{2}))?")


def _parse_date(raw: str) -> Optional[datetime]:
    """Parse an ISO or Israeli dd/mm/yyyy[ hh:mm] stamp to UTC; None if absent.

    Maya's API and pages carry wall-clock Asia/Jerusalem times (verified
    live); storage is UTC (§11.2).
    """
    if not raw:
        return None
    m = _ISO_RE.search(raw)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(0).replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IL_TZ)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    m = _IL_RE.search(raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        try:
            return datetime(
                year, month, day, hour, minute, tzinfo=_IL_TZ
            ).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Disclosure detail excerpt
# --------------------------------------------------------------------------


def _report_id(url: str) -> Optional[str]:
    """The numeric report id embedded in a Maya detail URL, if present."""
    match = _REPORT_ID_RE.search(url or "")
    return match.group(1) if match else None


def _pdf_bucket(report_id: str) -> str:
    """The mayafiles directory for a report id: its enclosing 1000."""
    low = ((int(report_id) - 1) // 1000) * 1000 + 1
    return f"{low}-{low + 999}"


def _pdf_excerpt(report_id: str) -> Tuple[str, List[str]]:
    """Text of the disclosure's PDF attachment, windowed onto its figures (§3.2).

    The report page itself carries no figures — they live in the attached press
    release / financial statement (see §3.2 for the three layers). This fetches
    that PDF and returns a bounded excerpt.

    A results PDF opens with several pages of SEC/MAGNA cover boilerplate, so
    the first _EXCERPT_MAX chars of the document would contain no figures at
    all. The excerpt is therefore anchored on the first page carrying at least
    _PDF_MIN_MONEY_HITS strict money matches — in practice the press release's
    highlights page — and spans _PDF_PAGE_SPAN pages so the outlook/guidance
    block that follows the quarter's figures is included too. A disclosure with
    no such page (a meeting notice, a rating affirmation) has no headline
    figures to find: it falls back to the document's opening pages, and the
    §3.2 vote then marks every field "ambiguous" — which is the correct answer,
    not a failure.

    Never raises. Any failure returns ("", [reason]) and the caller falls back
    to the rendered page; figures then come out "ambiguous", never invented.
    """
    try:
        import httpx  # local import: keeps module import cheap and optional
        import pypdf
    except ImportError as exc:  # noqa: BLE001
        return "", [f"maya pdf: dependency missing ({exc})"]

    url = _PDF_URL.format(bucket=_pdf_bucket(report_id), rid=report_id)
    try:
        from data import tls  # OS-trust context: corporate TLS interception

        resp = httpx.get(
            url,
            timeout=_PDF_TIMEOUT_S,
            follow_redirects=True,
            verify=tls.ssl_context(),
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF-"):
            return "", [f"maya pdf: {url} -> HTTP {resp.status_code}, not a PDF"]

        reader = pypdf.PdfReader(io.BytesIO(resp.content))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any fetch/parse failure degrades
        return "", [f"maya pdf: {_reason(exc)}"]

    if not any(p.strip() for p in pages):
        # A scanned disclosure has no text layer; we do not OCR (§13).
        return "", [f"maya pdf: no extractable text ({len(pages)} pages)"]

    anchor = next(
        (
            n
            for n, text in enumerate(pages)
            if len(_MONEY_RE.findall(text)) >= _PDF_MIN_MONEY_HITS
        ),
        0,
    )
    window = "\n".join(pages[anchor : anchor + _PDF_PAGE_SPAN])
    return clean_text(window, _EXCERPT_MAX), []


def _fetch_excerpt(page, url: str) -> Tuple[str, List[str]]:
    """Return the bounded verbatim disclosure text for the §3.2 extraction.

    Prefers the PDF attachment (the only layer with financial figures — §3.2);
    falls back to the rendered detail page, which yields the MAGNA cover text
    at best. Capped at _EXCERPT_MAX so only short text reaches the LLM (§2).
    Failure degrades to "" — extraction then sees just the title and figures
    come out "ambiguous", never invented.
    """
    errors: List[str] = []
    report_id = _report_id(url)
    if report_id:
        text, pdf_errors = _pdf_excerpt(report_id)
        if text:
            return text, pdf_errors
        errors.extend(pdf_errors)

    try:
        page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(_SETTLE_MS)
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        return clean_text(text or "", _EXCERPT_MAX), errors
    except Exception as exc:  # noqa: BLE001
        return "", errors + [f"maya excerpt: {_reason(exc)}"]
