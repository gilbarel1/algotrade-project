"""Language detection to route EN->FinBERT, HE->DictaBERT (§5 /sentiment).

Dependency-free heuristic: any Hebrew-block codepoint (U+0590-U+05FF) marks the
text as Hebrew, otherwise English. An explicit per-item `language` field ("en" or
"he") always wins; anything else falls back to detection.
"""

from typing import Optional

_HEBREW_START = 0x0590
_HEBREW_END = 0x05FF


def detect(text: str) -> str:
    """Return "he" if the text contains any Hebrew-block character, else "en"."""
    for ch in text:
        if _HEBREW_START <= ord(ch) <= _HEBREW_END:
            return "he"
    return "en"


def route(language: Optional[str], text: str) -> str:
    """Resolve the scoring language: honor an explicit "en"/"he", else detect."""
    if language in ("en", "he"):
        return language
    return detect(text)
