"""POST /costs/harvest — log a run's LLM costs into `costs` (§5, §9.4).

Token usage is not reachable from inside an n8n workflow (the LLM chain node emits
only its text, and a Code node cannot read the Chat Model sub-node's run data), so the
agents cannot log their own costs. n8n *does* record usage in the execution, so the
orchestrator calls this once after the fan-out — every agent sub-execution has finished
and been persisted by then — and the quant service reads the real numbers back out:
per LLM call, the model, `tokenUsage` (prompt/completion) and `executionTime`.

Attribution is exact, not time-based: every agent sub-workflow takes a `run_id` (§6.2),
so an execution belongs to this run only if its own input carries this `run_id`. The
agent name comes from `n8n_workflow_ids` in `config/universe.yaml` (§4.4).

Degrades, never 500s (§9.4): an unreachable n8n API or a missing key returns a
`degraded:` summary with zero rows, so a cost-logging failure cannot fail a run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache, run_store
from ops import cost_log, n8n_api
from routers.runs import load_config

logger = logging.getLogger(__name__)

router = APIRouter()

# An agent sub-execution of this run cannot have started before the run did. The slack
# absorbs clock skew between the n8n host and the service (same box locally, but the two
# timestamps come from different clocks/timezone conversions).
_START_SLACK = timedelta(minutes=5)


class CostsHarvestRequest(BaseModel):
    run_id: str


def _started_at(run_id: str) -> Optional[datetime]:
    con = cache.connect()
    try:
        run = run_store.get_run(con, run_id)
    finally:
        con.close()
    if not run or not run.get("started_at"):
        return None
    started = run["started_at"]
    if started.tzinfo is None:  # stored UTC (§11.2), read back naive
        started = started.replace(tzinfo=timezone.utc)
    return started - _START_SLACK


def _too_old(meta: dict, cutoff: Optional[datetime]) -> bool:
    """True if this execution started before the run did — it cannot belong to it.

    A cheap metadata check that avoids pulling the full run data (the expensive call) for
    every historical execution of an agent. Anything unparseable is *not* skipped: the
    run_id check below is the authoritative filter, this is only a shortcut.
    """
    if cutoff is None:
        return False
    started = meta.get("startedAt")
    if not isinstance(started, str):
        return False
    try:
        return datetime.fromisoformat(started.replace("Z", "+00:00")) < cutoff
    except ValueError:
        return False


def _collect_calls(
    run_id: str, workflow_ids: Dict[str, str], cutoff: Optional[datetime]
) -> tuple[List[dict], List[str]]:
    """Every LLM call this run made, tagged with the agent that made it."""
    calls: List[dict] = []
    errors: List[str] = []

    for agent, workflow_id in workflow_ids.items():
        if not workflow_id:
            errors.append(f"{agent}: no n8n workflow id configured")
            continue
        try:
            executions = n8n_api.list_executions(workflow_id)
        except n8n_api.N8nApiError as exc:
            errors.append(f"{agent}: {exc}")
            continue

        for meta in executions:
            if _too_old(meta, cutoff):
                continue
            execution_id = str(meta.get("id"))
            try:
                execution = n8n_api.get_execution(execution_id)
            except n8n_api.N8nApiError as exc:
                errors.append(f"{agent} execution {execution_id}: {exc}")
                continue
            # Authoritative attribution: the agent was invoked with this run_id (§6.2).
            if n8n_api.find_run_id(execution) != run_id:
                continue  # a different run, or a standalone test run of the agent
            for call in n8n_api.extract_llm_calls(execution):
                call["agent"] = agent
                calls.append(call)

    return calls, errors


@router.post("/costs/harvest")
def costs_harvest(req: CostsHarvestRequest):
    config = load_config()
    workflow_ids: Dict[str, str] = dict(config.get("n8n_workflow_ids") or {})

    if not workflow_ids:
        return {
            "run_id": req.run_id,
            "rows": 0,
            "calls": 0,
            "usd_cost": 0.0,
            "by_agent": [],
            "summary": (
                "degraded: no n8n_workflow_ids in config/universe.yaml — "
                "cannot attribute LLM calls to agents (§4.4)."
            ),
        }

    calls, errors = _collect_calls(req.run_id, workflow_ids, _started_at(req.run_id))
    rows = cost_log.aggregate_calls(calls)

    if rows:
        con = cache.connect()
        try:
            written = cost_log.write_costs(con, req.run_id, rows)
        finally:
            con.close()
    else:
        written = 0

    total_usd = sum(row["usd_cost"] for row in rows)
    n_agents = len({row["agent"] for row in rows})
    summary = (
        f"harvested {len(calls)} LLM call(s) across {n_agents} agent(s): "
        f"${total_usd:.4f}."
    )
    if errors:
        summary = f"degraded: {'; '.join(errors)} — {summary}"
    elif not calls:
        # No error, no calls: the run made no LLM calls (every agent degraded before
        # its first call), which is a real state — not a harvest failure.
        summary = f"no LLM calls found for run {req.run_id}."

    return {
        "run_id": req.run_id,
        "rows": written,
        "calls": len(calls),
        "usd_cost": round(total_usd, 6),
        "by_agent": rows,
        "summary": summary,
    }
