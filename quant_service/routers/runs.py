"""POST /runs/start, /runs/finish, /recommendations/store — orchestration writes (§5, §6.1).

n8n cannot open DuckDB, so the orchestrator's three table writes are served over HTTP,
the same precedent as `/news/store` and `/earnings/store`.

`/runs/start` also hands the orchestrator its run parameters (watchlist, windows) from
`config/universe.yaml`, so the workflow never hardcodes a ticker list or a lookback —
the §4.4 "defaults from config" guardrail holds even inside n8n.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from data import cache, run_store

router = APIRouter()

# routers/runs.py -> routers/ -> quant_service/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "universe.yaml")


def load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class RunStartRequest(BaseModel):
    mode: str = "manual"  # {manual, scheduled, chat} (§6.1, §6.5)
    # §6.5: an ad-hoc chat request analyses one name instead of the whole
    # watchlist. Non-empty ⇒ overrides config for this run only; omitted or
    # empty ⇒ the full config watchlist, exactly as manual/scheduled behave.
    tickers: Optional[List[str]] = None


@router.post("/runs/start")
def runs_start(req: RunStartRequest):
    config = load_config()
    override = [t.strip() for t in (req.tickers or []) if t and t.strip()]
    watchlist: List[str] = override or list(config.get("watchlist") or [])
    started_at = run_store.utc_now()

    con = cache.connect()
    try:
        run_id = run_store.mint_run_id(con, started_at)
        run_store.start_run(con, run_id, req.mode, watchlist, started_at)
    finally:
        con.close()

    return {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "mode": req.mode,
        "watchlist": watchlist,
        "window_minutes": int(config.get("news_window_minutes", 120)),
        "window_days": int(config.get("earnings_window_days", 5)),
        "lookback_days": int(config.get("ohlc_lookback_days", 180)),
        "summary": f"run {run_id} started ({req.mode}): {len(watchlist)} ticker(s).",
    }


class RunFinishRequest(BaseModel):
    run_id: str
    status: str = "ok"  # {ok, degraded, error}
    report_path: Optional[str] = None


@router.post("/runs/finish")
def runs_finish(req: RunFinishRequest):
    finished_at = run_store.utc_now()
    con = cache.connect()
    try:
        updated = run_store.finish_run(
            con, req.run_id, req.status, req.report_path, finished_at
        )
    finally:
        con.close()

    if not updated:
        # An unknown run_id is a workflow wiring bug, not a data problem: say so
        # plainly instead of silently reporting success (§9.4 — no silent fallbacks).
        return {
            "run_id": req.run_id,
            "finished_at": finished_at.isoformat(),
            "status": req.status,
            "summary": f"degraded: no runs row for {req.run_id} — nothing to finish.",
        }
    return {
        "run_id": req.run_id,
        "finished_at": finished_at.isoformat(),
        "status": req.status,
        "summary": f"run {req.run_id} finished ({req.status}).",
    }


class RecommendationStoreRequest(BaseModel):
    run_id: str
    ticker: str
    draft: Optional[Dict[str, Any]] = None
    critique: Optional[Dict[str, Any]] = None
    final: Optional[Dict[str, Any]] = None
    sentiment: Optional[Dict[str, Any]] = None
    earnings: Optional[Dict[str, Any]] = None
    technical: Optional[Dict[str, Any]] = None
    agent_status: Optional[Dict[str, Any]] = None


@router.post("/recommendations/store")
def recommendations_store(req: RecommendationStoreRequest):
    con = cache.connect()
    try:
        stored = run_store.upsert_recommendation(
            con,
            req.run_id,
            req.ticker,
            req.draft,
            req.critique,
            req.final,
            req.sentiment,
            req.earnings,
            req.technical,
            req.agent_status,
        )
    finally:
        con.close()
    return {"stored": stored}
