"""POST /earnings/fetch and POST /earnings/store — server-side Maya access for the Earnings Agent (§5, §3.2).

Same split as the Step 5 news endpoints: scraping (Playwright-rendered Maya,
EN primary / HE fallback — see `data/maya.py`), §4.3 cleaning, and
`earnings`-table persistence all stay in the quant service, so n8n moves only
compact disclosure items and the LLM sees only short text (§2 guardrail).

- **/earnings/fetch** resolves the ticker's company terms from
  `config/universe.yaml`, scrapes the Maya reports list, filters to the ticker
  and the disclosure window, and returns compact items (the newest carries a
  bounded `excerpt` of the disclosure text — the verbatim source for the §3.2
  self-consistency number extraction) together with the few-shot classification
  examples from `prompts/earnings_examples.jsonl`. A scrape failure degrades
  the summary (prefix ``degraded:``) rather than 500-ing; zero items with a
  healthy scrape is a valid "no recent disclosure" (§13, §14).
- **/earnings/store** upserts the classified disclosure into `earnings` (§4.2).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from data import cache, earnings_store, maya

router = APIRouter()

# routers/earnings.py -> routers/ -> quant_service/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "universe.yaml")
_FEWSHOT_PATH = os.path.join(_REPO_ROOT, "prompts", "earnings_examples.jsonl")


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
    terms = (config.get("search_terms") or {}).get(req.ticker, [])

    if not terms:
        # No mapping for this ticker: cannot match Maya rows to a *.TA symbol.
        return {
            "ticker": req.ticker,
            "items": [],
            "few_shot": _load_fewshot(),
            "summary": (
                f"degraded: no search_terms configured for {req.ticker}; "
                "cannot match Maya disclosures."
            ),
        }

    items, errors = maya.fetch_disclosures(req.ticker, terms, window, candidates)

    n_en = sum(1 for i in items if i["language"] == "en")
    n_he = sum(1 for i in items if i["language"] == "he")
    n_excerpted = sum(1 for i in items if i.get("excerpt"))
    summary = (
        f"{len(items)} disclosure(s) in window: {n_en} EN, {n_he} HE "
        f"(window {window}d); {n_excerpted} ranked candidate(s) to classify."
    )
    # Degrade only when the scrape failed somewhere AND coverage may be
    # incomplete because of it. A healthy scrape with zero matching rows is a
    # genuine "no recent disclosure" (§13/§14), left for the sub-workflow.
    if errors:
        summary = f"degraded: {'; '.join(errors)} — {summary}"

    return {
        "ticker": req.ticker,
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
