"""POST /riskmanager/context — prompts, rubric, and deterministic rubric facts
for the Risk Manager three-stage critique loop (§3.4, §6.2, §6.3).

The Risk Manager is otherwise LLM-only (§6.2), but two CLAUDE.md guardrails
require server-side delivery: prompts live in ``prompts/`` (never hardcoded in a
workflow) and rubric thresholds come from ``config/rubric.yaml`` (never
hardcoded). This endpoint serves both — mirroring how ``/news/fetch`` and
``/earnings/fetch`` load their few-shot files server-side — plus the
*mechanism* half of the "deterministic + LLM" rubric: the directional mapping,
strong-signal flags, agreement counts, and applicable conviction caps computed
in pure Python from the rubric values. The n8n sub-workflow injects these facts
into the draft/final prompts and re-applies the clamp after the final pass, so
rubric compliance is guaranteed by construction rather than by instruction.

Endpoint is read-only and never 500s on a degraded/partial agent input: it
computes what is computable and leaves the rest neutral/None so the loop still
runs (§3.4 "runs three passes, always"; §9.4 degraded mode).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# routers/riskmanager.py -> routers/ -> quant_service/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_RUBRIC_PATH = os.path.join(_REPO_ROOT, "config", "rubric.yaml")
_PROMPTS_DIR = os.path.join(_REPO_ROOT, "prompts")
_PROMPT_FILES = {
    "draft": "risk_manager_draft.md",
    "critique": "risk_manager_critique.md",
    "final": "risk_manager_final.md",
}


def _load_rubric() -> dict:
    with open(_RUBRIC_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_prompts() -> Dict[str, str]:
    """Read the three pass templates; a missing file yields an empty string so
    the caller degrades rather than crashing."""
    prompts: Dict[str, str] = {}
    for key, fname in _PROMPT_FILES.items():
        path = os.path.join(_PROMPTS_DIR, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                prompts[key] = fh.read()
        else:
            prompts[key] = ""
    return prompts


# --- Deterministic rubric mechanism (§3.4). Pure functions on the agent inputs. ---


def sentiment_direction(sentiment: dict, rubric: dict) -> str:
    """§3.4 directional mapping for sentiment: bullish if max(llm, model) >=
    bullish_threshold, bearish if min(llm, model) <= bearish_threshold, else
    neutral. Missing scores (degraded sentiment) map to neutral."""
    thresholds = ((rubric.get("directional") or {}).get("sentiment") or {})
    bull = float(thresholds.get("bullish_threshold", 0.2))
    bear = float(thresholds.get("bearish_threshold", -0.2))
    llm = sentiment.get("llm_sentiment")
    model = sentiment.get("model_sentiment")
    scores = [s for s in (llm, model) if isinstance(s, (int, float))]
    if not scores:
        return "neutral"
    if max(scores) >= bull:
        return "bullish"
    if min(scores) <= bear:
        return "bearish"
    return "neutral"


def technical_direction(technical: dict, rubric: dict) -> str:
    """§3.4 directional mapping for technical: from the signal lists in the
    rubric. An unknown/missing signal maps to neutral."""
    tech = ((rubric.get("directional") or {}).get("technical") or {})
    signal = technical.get("signal")
    if signal in (tech.get("bullish_signals") or []):
        return "bullish"
    if signal in (tech.get("bearish_signals") or []):
        return "bearish"
    return "neutral"


def _earnings_is_window(earnings: dict) -> bool:
    """Earnings agents emit ``is_earnings_window`` (§3.2); the §6.3 condensed
    panel uses ``is_window``. Accept either."""
    if "is_earnings_window" in earnings:
        return bool(earnings.get("is_earnings_window"))
    return bool(earnings.get("is_window"))


def strong_signals(sentiment: dict, technical: dict, earnings: dict, rubric: dict) -> dict:
    """§3.4 strong-signal flags used by the draft pass to pick a side at all,
    and required (bearish) for a `short` call."""
    strong = rubric.get("strong_signal") or {}
    abs_thresh = float(strong.get("sentiment_abs_threshold", 0.4))
    tech_strong = strong.get("technical_strong_signals") or []

    llm = sentiment.get("llm_sentiment")
    model = sentiment.get("model_sentiment")
    sent_strong = any(
        isinstance(s, (int, float)) and abs(s) >= abs_thresh for s in (llm, model)
    )
    tech_signal = technical.get("signal")
    tech_is_strong = tech_signal in tech_strong
    earn_strong = bool(
        strong.get("earnings_high_materiality_within_window", True)
        and earnings.get("materiality") == "high"
        and _earnings_is_window(earnings)
    )
    return {
        "sentiment": sent_strong,
        "technical": tech_is_strong,
        "earnings": earn_strong,
        "any": bool(sent_strong or tech_is_strong or earn_strong),
    }


def _degraded_agents(sentiment: dict, earnings: dict, technical: dict) -> List[str]:
    agents = {"sentiment": sentiment, "earnings": earnings, "technical": technical}
    return [name for name, out in agents.items() if out.get("status") == "degraded"]


def compute_facts(
    sentiment: dict, earnings: dict, technical: dict, rubric: dict
) -> dict:
    """The deterministic §3.4 facts. Earnings direction is LLM-classified in the
    draft pass, so agreement counts are given for each possible earnings
    direction — the sub-workflow resolves the count once the draft supplies it."""
    sent_dir = sentiment_direction(sentiment, rubric)
    tech_dir = technical_direction(technical, rubric)

    # Fixed contributions from the two deterministically-mapped agents.
    fixed_bullish = int(sent_dir == "bullish") + int(tech_dir == "bullish")
    fixed_bearish = int(sent_dir == "bearish") + int(tech_dir == "bearish")

    def counts_for(earn_dir: str) -> dict:
        return {
            "bullish": fixed_bullish + int(earn_dir == "bullish"),
            "bearish": fixed_bearish + int(earn_dir == "bearish"),
        }

    degraded = _degraded_agents(sentiment, earnings, technical)
    caps_cfg = rubric.get("conviction_caps") or {}
    dual_thresh = float(
        ((caps_cfg.get("dual_sentiment") or {}).get("disagreement_threshold", 0.3))
    )
    disagreement = sentiment.get("disagreement")
    dual_cap = isinstance(disagreement, (int, float)) and disagreement > dual_thresh

    earnings_cap = bool(
        ((caps_cfg.get("earnings_event") or {}).get("enabled", True))
        and earnings.get("materiality") == "high"
        and _earnings_is_window(earnings)
    )

    avoid_min = int(rubric.get("avoid_min_degraded_agents", 2))
    force_avoid = len(degraded) >= avoid_min

    strong = strong_signals(sentiment, technical, earnings, rubric)

    caps = {
        "earnings_event": earnings_cap,
        "dual_sentiment": dual_cap,
        "degraded_agents": degraded,
        "degraded_count": len(degraded),
        "force_avoid": force_avoid,
        # Any of these caps conviction at medium (before the agreement count).
        "any_cap_medium": bool(earnings_cap or dual_cap or len(degraded) >= 1),
    }

    return {
        "sentiment_direction": sent_dir,
        "technical_direction": tech_dir,
        "strong_signals": strong,
        # `short` (§3.4) needs at least one strong bearish signal. This is the
        # part knowable at context time — sentiment/technical that are BOTH
        # strong AND bearish. The earnings contribution is direction-dependent
        # (a high-materiality earnings can be a bullish beat), and earnings
        # direction is LLM-classified in the draft, so the sub-workflow's clamp
        # adds `strong_signals.earnings && earnings_direction == "bearish"` on
        # top of this when it enforces the `short` rule.
        "short_requires_strong_bearish": True,
        "has_strong_bearish": bool(
            (strong["sentiment"] and sent_dir == "bearish")
            or (strong["technical"] and tech_dir == "bearish")
        ),
        "agreement_counts": {
            "fixed_bullish": fixed_bullish,
            "fixed_bearish": fixed_bearish,
            "by_earnings_direction": {
                "bullish": counts_for("bullish"),
                "bearish": counts_for("bearish"),
                "neutral": counts_for("neutral"),
            },
        },
        "caps": caps,
    }


class RiskContextRequest(BaseModel):
    ticker: str
    sentiment: Optional[Dict[str, Any]] = None
    earnings: Optional[Dict[str, Any]] = None
    technical: Optional[Dict[str, Any]] = None


@router.post("/riskmanager/context")
def riskmanager_context(req: RiskContextRequest):
    rubric = _load_rubric()
    prompts = _load_prompts()
    sentiment = req.sentiment or {}
    earnings = req.earnings or {}
    technical = req.technical or {}

    facts = compute_facts(sentiment, earnings, technical, rubric)

    missing = [
        name
        for name, out in (
            ("sentiment", req.sentiment),
            ("earnings", req.earnings),
            ("technical", req.technical),
        )
        if not out
    ]
    summary = f"context ready for {req.ticker}."
    if missing:
        summary = f"degraded: missing agent input(s): {', '.join(missing)} — {summary}"

    return {
        "ticker": req.ticker,
        "prompts": prompts,
        "rubric": rubric,
        "facts": facts,
        "summary": summary,
    }
