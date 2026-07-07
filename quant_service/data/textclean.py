"""Shared text cleaning and company-term matching (§4.3).

Used by both the news pipeline (`routers/news.py`, Step 5) and the Maya
earnings scraper (`data/maya.py`, Step 6): the same §4.3 rules — strip markup,
bound length, and match a ticker's `search_terms` against visible text — apply
to news items and disclosure rows alike. Lives in `data/` so scrapers never
import from `routers/`.
"""

from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup


def clean_text(value: str, limit: Optional[int] = None) -> str:
    """Strip HTML tags, decode entities, and collapse whitespace; optionally cap length."""
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


_LATIN = re.compile(r"[A-Za-z]")


def term_matches(term: str, haystack: str) -> bool:
    """True if `term` occurs in `haystack` (already lowercased) as a whole word.

    Latin terms use word boundaries so short names don't match inside longer
    words (e.g. "Leumi" must not match "Leumit"). Hebrew has no case and attaches
    prefixes (ב/ה/ו/כ/ל/מ/ש) directly to nouns, so a Latin-style boundary would
    wrongly reject legitimate prefixed forms *and* a bare substring wrongly
    accepts collisions (the classic "טבע" inside "מטבע"/currency). We therefore
    require Hebrew terms to be distinctive multi-token names (see universe.yaml)
    and match them as substrings.
    """
    term = term.strip().lower()
    if not term:
        return False
    if _LATIN.search(term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None
    return term in haystack


def mentions_term(*text_parts: str, terms: List[str]) -> bool:
    """§4.3: True when any term appears in the joined (cleaned) text parts."""
    haystack = " ".join(text_parts).lower()
    return any(term_matches(t, haystack) for t in terms)
