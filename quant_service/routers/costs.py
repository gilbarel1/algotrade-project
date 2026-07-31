"""POST /costs/harvest — log a run's LLM costs into `costs` (§5, §9.4).

Token usage is not reachable from inside an n8n workflow (the LLM chain node emits
only its text, and a Code node cannot read the Chat Model sub-node's run data), so the
agents cannot log their own costs. n8n *does* record usage in the execution, so the
orchestrator calls this once after the fan-out — every agent sub-execution has finished
and been persisted by then — and the quant service reads the real numbers back out:
per LLM call, the model, `tokenUsage` (prompt/completion) and `executionTime`.

Attribution is exact, not time-based: every agent sub-workflow takes a `run_id` (§6.2),
so an execution belongs to this run only if its own input carries this `run_id`.

Which n8n workflow *is* which agent is resolved per the §4.4 ladder — `N8N_WF_<AGENT>`
from the environment, then a lookup by workflow name through the n8n API, then
`n8n_workflow_ids` in `config/universe.yaml`. Ids are minted by the import and differ
per machine, so tracking them in a shared file breaks attribution for every other
developer: their ids 404, the harvest degrades, and `costs` stays empty while the run
still reports green. The name lookup makes a fresh import work with no configuration.

Degrades, never 500s (§9.4): an unreachable n8n API or a missing key returns a
`degraded:` summary with zero rows, so a cost-logging failure cannot fail a run.
"""

from __future__ import annotations

import json
import logging
import os
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


# Agent -> the workflow JSON this repo ships for it. The `name` inside each file is
# the key the §4.4 name lookup matches on, so renaming a workflow in the repo keeps
# the lookup correct with no second list to maintain.
_AGENT_WORKFLOW_FILES = {
    "technical": os.path.join("n8n", "agents", "technical.json"),
    "sentiment": os.path.join("n8n", "agents", "sentiment.json"),
    "earnings": os.path.join("n8n", "agents", "earnings.json"),
    "risk_manager": os.path.join("n8n", "agents", "risk_manager.json"),
    "chat": os.path.join("n8n", "chat_assistant.workflow.json"),
}

# routers/costs.py -> routers/ -> quant_service/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


class CostsHarvestRequest(BaseModel):
    run_id: str


def _shipped_name(agent: str) -> Optional[str]:
    """The workflow name this repo ships for `agent`, or None if unreadable."""
    rel = _AGENT_WORKFLOW_FILES.get(agent)
    if not rel:
        return None
    try:
        with open(os.path.join(_REPO_ROOT, rel), "r", encoding="utf-8") as fh:
            name = json.load(fh).get("name")
    except (OSError, json.JSONDecodeError):
        return None
    return name if isinstance(name, str) and name else None


def _match_by_name(name: str, live: List[dict]) -> Optional[str]:
    """Id of the one live workflow whose name matches, or None.

    Exact match first; failing that, the name before " (" — so the section-number
    suffix ("Technical Agent (§3.3)") can drift without breaking the lookup. An
    ambiguous prefix (two workflows match) resolves to neither: guessing which of
    two copies is live would mis-attribute costs silently.
    """
    wanted = name.strip().casefold()
    exact = [w for w in live if str(w.get("name", "")).strip().casefold() == wanted]
    if len(exact) == 1:
        return str(exact[0].get("id"))

    stem = wanted.split(" (", 1)[0].strip()
    if not stem:
        return None
    loose = [
        w
        for w in live
        if str(w.get("name", "")).strip().casefold().split(" (", 1)[0].strip() == stem
    ]
    return str(loose[0].get("id")) if len(loose) == 1 else None


def _resolve_workflow_ids(config: dict) -> tuple[Dict[str, str], List[str]]:
    """Agent -> n8n workflow id, per the §4.4 ladder: env, then name, then config.

    Ids are minted per import, so a tracked config file is the wrong home for them:
    one developer's ids 404 for everyone else and the harvest degrades while the run
    stays green. The name lookup makes a fresh import work with no configuration.
    """
    configured: Dict[str, str] = dict(config.get("n8n_workflow_ids") or {})
    agents = list(dict.fromkeys([*_AGENT_WORKFLOW_FILES, *configured]))

    live: List[dict] = []
    notes: List[str] = []
    if any(not os.environ.get(f"N8N_WF_{a.upper()}") for a in agents):
        try:
            live = n8n_api.list_workflows()
        except n8n_api.N8nApiError as exc:
            notes.append(f"name lookup unavailable ({exc})")

    resolved: Dict[str, str] = {}
    by_source: Dict[str, List[str]] = {"env": [], "name": [], "config": []}
    for agent in agents:
        from_env = os.environ.get(f"N8N_WF_{agent.upper()}", "").strip()
        if from_env:
            resolved[agent] = from_env
            by_source["env"].append(agent)
            continue
        shipped = _shipped_name(agent)
        found = _match_by_name(shipped, live) if (shipped and live) else None
        if found:
            resolved[agent] = found
            by_source["name"].append(agent)
            continue
        if configured.get(agent):
            resolved[agent] = str(configured[agent])
            by_source["config"].append(agent)

    for source, names in by_source.items():
        if names:
            notes.append(f"{source}: {', '.join(sorted(names))}")
    unresolved = [a for a in agents if a not in resolved]
    if unresolved:
        notes.append(f"unresolved: {', '.join(sorted(unresolved))}")
    return resolved, notes


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
    workflow_ids, resolution_notes = _resolve_workflow_ids(config)

    if not workflow_ids:
        return {
            "run_id": req.run_id,
            "rows": 0,
            "calls": 0,
            "usd_cost": 0.0,
            "by_agent": [],
            "summary": (
                "degraded: could not resolve any agent's n8n workflow id "
                f"({'; '.join(resolution_notes) or 'no candidates'}) — cannot "
                "attribute LLM calls to agents (§4.4)."
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
    # Say how each agent's workflow id was resolved (§4.4): silent mis-resolution is
    # exactly the failure this ladder exists to prevent, so it is never invisible.
    if resolution_notes:
        summary += f" Workflow ids — {'; '.join(resolution_notes)}."

    return {
        "run_id": req.run_id,
        "rows": written,
        "calls": len(calls),
        "usd_cost": round(total_usd, 6),
        "by_agent": rows,
        "summary": summary,
    }
