"""POST /earnings/fetch and POST /earnings/store — server-side disclosure access for the Earnings Agent (§5, §3.2).

Same split as the Step 5 news endpoints: fetching (Playwright-rendered Maya for
TASE names, the SEC EDGAR JSON API for US names), §4.3 cleaning, and
`earnings`-table persistence all stay in the quant service, so n8n moves only
compact disclosure items and the LLM sees only short text (§2 guardrail).

- **/earnings/fetch** routes by the ticker's **market** (§4.4): `tase` →
  `data/maya.py` (EN primary / HE fallback, matched by the ticker's company
  terms from `config/universe.yaml`), `us` → `data/edgar.py` (ticker → CIK →
  recent 8-K/10-Q/10-K). Both return the same compact items — ranked by §3.2
  relevance, the top `earnings_candidates` carrying a bounded `excerpt` (the
  verbatim source for the §3.2 self-consistency number extraction) — together
  with the few-shot classification examples from
  `prompts/earnings_examples.jsonl`. **The response shape does not vary by
  market**, which is what keeps the Earnings Agent sub-workflow market-agnostic.
  A fetch failure degrades the summary (prefix ``degraded:``) rather than
  500-ing; zero items with a healthy fetch is a valid "no recent disclosure"
  (§13, §14).
- **/earnings/store** upserts the classified disclosure into `earnings` (§4.2).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache, earnings_store, edgar, maya
from data.markets import (
    load_config as _load_config,
    market as _market,
    market_config as _market_config,
)

router = APIRouter()

# How each market's disclosure source is named in text the model or the reader
# sees (§3.2). "Maya" is meaningless on a US 8-K and actively misleading in an
# extraction prompt, which is what these labels exist to prevent.
_SOURCE_LABELS = {"maya": "TASE (Maya)", "edgar": "SEC EDGAR"}

# routers/earnings.py -> routers/ -> quant_service/ -> repo root.
# Config itself is read by `data/markets.py` (the single §4.4 reader).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_FEWSHOT_PATH = os.path.join(_REPO_ROOT, "prompts", "earnings_examples.jsonl")


def _load_fewshot() -> List[dict]:
    """Read the few-shot labeled classification examples (§7); empty list if absent."""
    if not os.path.exists(_FEWSHOT_PATH):
        return []
    examples: List[dict] = []
    with open(_FEWSHOT_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                examples.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a malformed example line must not break the fetch
    return examples


class EarningsFetchRequest(BaseModel):
    ticker: str
    window_days: Optional[int] = None
    candidates: Optional[int] = None


@router.post("/earnings/fetch")
def earnings_fetch(req: EarningsFetchRequest):
    config = _load_config()
    window = req.window_days or int(config.get("earnings_window_days", 5))
    # How many ranked disclosures get an excerpt — i.e. how many the agent will
    # classify before selecting the most material (§3.2, §4.4).
    candidates = req.candidates or int(config.get("earnings_candidates", 3))

    # §3.2: only the *fetch source* routes by market — everything downstream
    # (ranking, classification, self-consistency extraction) is identical.
    # The source name comes from the market's `earnings_source` (§4.4) rather
    # than from the market name itself, so adding a market is a config change.
    name = _market(req.ticker, config)

    def _empty(source_name: str, summary: str) -> dict:
        """A no-items response carrying the same envelope as a successful one."""
        return {
            "ticker": req.ticker,
            "market": name,
            "source": source_name,
            "source_label": _SOURCE_LABELS.get(source_name, source_name or "unknown"),
            "items": [],
            "few_shot": _load_fewshot(),
            "summary": summary,
        }

    try:
        source = str(_market_config(name, config).get("earnings_source") or "")
    except ValueError as exc:  # market_overrides names a market not in config
        return _empty("", f"degraded: {exc}")

    if source == "maya":
        terms = (config.get("search_terms") or {}).get(req.ticker, [])
        if not terms:
            # No mapping for this ticker: cannot match Maya rows to a *.TA
            # symbol. (EDGAR needs no terms — the ticker resolves to a CIK.)
            return _empty(
                source,
                f"degraded: no search_terms configured for {req.ticker}; "
                "cannot match Maya disclosures.",
            )
        items, errors = maya.fetch_disclosures(req.ticker, terms, window, candidates)
    elif source == "edgar":
        items, errors = edgar.fetch_disclosures(req.ticker, window, candidates)
    else:
        return _empty(
            source,
            f"degraded: market {name!r} declares no known earnings_source "
            f"(got {source!r}; expected 'maya' or 'edgar').",
        )

    n_en = sum(1 for i in items if i["language"] == "en")
    n_he = sum(1 for i in items if i["language"] == "he")
    n_excerpted = sum(1 for i in items if i.get("excerpt"))
    summary = (
        f"{len(items)} disclosure(s) in window: {n_en} EN, {n_he} HE "
        f"(window {window}d); {n_excerpted} ranked candidate(s) to classify."
    )
    # A candidate that resolved to a lower layer is not a degrade (§3.2), but it
    # is why its figures may come out "ambiguous" — so say so plainly, without
    # the `degraded:` prefix that would mark the whole agent degraded (§9.4).
    fell_back = sorted(
        {
            str(i.get("excerpt_source"))
            for i in items
            if i.get("excerpt") and i.get("excerpt_source") != maya.PRESS_RELEASE
        }
    )
    if fell_back:
        n_fell_back = sum(
            1
            for i in items
            if i.get("excerpt") and i.get("excerpt_source") != maya.PRESS_RELEASE
        )
        summary += (
            f" {n_fell_back} candidate(s) fell back to a lower excerpt layer "
            f"({', '.join(fell_back)}); their figures may be ambiguous."
        )
    # Degrade only when the fetch failed somewhere AND coverage may be
    # incomplete because of it. A healthy fetch with zero matching rows is a
    # genuine "no recent disclosure" (§13/§14), left for the sub-workflow.
    if errors:
        summary = f"degraded: {'; '.join(errors)} — {summary}"

    return {
        "ticker": req.ticker,
        # The market and its human-readable source name (§5). The sub-workflow
        # is market-agnostic in its LOGIC but not in its WORDING: it names the
        # source in the no-disclosure summary and in both LLM prompts. Serving
        # the label here keeps the §4.4 market rule in one place instead of
        # re-deriving ".TA" inside an n8n Code node — which would also miss
        # `market_overrides`.
        "market": name,
        "source": source,
        "source_label": _SOURCE_LABELS.get(source, source),
        "items": items,
        "few_shot": _load_fewshot(),
        "summary": summary,
    }


class EarningsStoreRequest(BaseModel):
    ticker: str
    items: List[Dict[str, Any]]


@router.post("/earnings/store")
def earnings_store_endpoint(req: EarningsStoreRequest):
    con = cache.connect()
    try:
        stored = earnings_store.upsert_earnings(con, req.ticker, req.items)
    finally:
        con.close()
    return {"stored": stored}
