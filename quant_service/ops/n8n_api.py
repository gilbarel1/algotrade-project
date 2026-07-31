"""Read-only client for n8n's REST API — the only place LLM token usage exists (§9.4).

n8n does not expose token usage *to* a workflow: the LLM chain node emits only its
text, and a Code node cannot read the Chat Model sub-node's run data. It does record
it in the execution, where each Chat Model sub-node run carries:

    runData[<model node>][i].data.ai_languageModel[0][0].json.tokenUsage
        -> {promptTokens, completionTokens, totalTokens}
    runData[<model node>][i].executionTime                    -> latency_ms
    runData[<model node>][i].inputOverride
        .ai_languageModel[0][0].json.options.model            -> the OpenRouter model

Each agent sub-workflow call is its own execution (`mode: "integrated"`), persisted
as soon as it finishes — so the orchestrator can harvest a run's costs after the
fan-out completes (§5 `/costs/harvest`).

Read-only by construction: this module only ever GETs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://localhost:5678"
_TIMEOUT = 30.0
# One page is plenty: a run makes one sub-execution per ticker per agent.
_PAGE_LIMIT = 250


class N8nApiError(RuntimeError):
    """The n8n API was unreachable or refused the request (harvest degrades, §9.4)."""


def _base_url() -> str:
    return (os.environ.get("N8N_API_URL") or DEFAULT_API_URL).rstrip("/")


def _headers() -> Dict[str, str]:
    key = os.environ.get("N8N_API_KEY", "")
    if not key:
        raise N8nApiError(
            "N8N_API_KEY is not set — cannot read token usage from n8n (§11.1)"
        )
    return {"X-N8N-API-KEY": key, "Accept": "application/json"}


def _get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{_base_url()}/api/v1{path}"
    try:
        resp = httpx.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise N8nApiError(f"n8n API unreachable at {url}: {exc}") from exc
    if resp.status_code == 401:
        raise N8nApiError("n8n API rejected the key (401) — check N8N_API_KEY")
    if resp.status_code >= 400:
        raise N8nApiError(f"n8n API {resp.status_code} for {path}: {resp.text[:200]}")
    return resp.json()


def list_workflows() -> List[dict]:
    """Every workflow in this n8n, as `{id, name, ...}` (§4.4 name lookup).

    Workflow ids are minted by the import and so differ per machine; the names in
    this repo's `n8n/*.json` do not. `/costs/harvest` maps agent -> id through
    this rather than through a tracked config file.
    """
    body = _get("/workflows", {"limit": _PAGE_LIMIT})
    return body.get("data") or []


def list_executions(workflow_id: str) -> List[dict]:
    """Execution metadata for one workflow, newest first (no run data)."""
    body = _get("/executions", {"workflowId": workflow_id, "limit": _PAGE_LIMIT})
    return body.get("data") or []


def get_execution(execution_id: str) -> dict:
    """One execution *with* its run data (where the token usage lives)."""
    return _get(f"/executions/{execution_id}", {"includeData": "true"})


def _run_data(execution: dict) -> Dict[str, Any]:
    return (
        ((execution.get("data") or {}).get("resultData") or {}).get("runData") or {}
    )


def find_run_id(execution: dict) -> Optional[str]:
    """The `run_id` this execution was invoked with (§6.2 — every agent takes one).

    Scans the executed nodes' output items rather than assuming a trigger node
    name, so it keeps working if an agent's trigger is ever renamed.

    Every output *type* is scanned, not just `main`: the §6.5 chat assistant reaches
    the orchestrator through a Call n8n Workflow Tool, so its run_id comes back on
    the `ai_tool` connection. Scanning only `main` left the chat workflow with no
    attributable run_id, and `/costs/harvest` silently dropped its tokens.
    """
    for runs in _run_data(execution).values():
        for run in runs or []:
            for branches in ((run.get("data") or {}).values()):
                for branch in branches or []:
                    for item in branch or []:
                        if not isinstance(item, dict):
                            continue
                        run_id = (item.get("json") or {}).get("run_id")
                        if isinstance(run_id, str) and run_id:
                            return run_id
    return None


def _model_of(run: dict) -> str:
    """The OpenRouter model of one Chat Model sub-node run."""
    try:
        override = (run.get("inputOverride") or {})["ai_languageModel"][0][0]
        model = ((override.get("json") or {}).get("options") or {}).get("model")
        if isinstance(model, str) and model:
            return model
    except (KeyError, IndexError, TypeError):
        pass
    return "unknown"


def extract_llm_calls(execution: dict) -> List[dict]:
    """Every LLM call in one execution: model, real token usage, and latency.

    One entry per Chat Model sub-node *run*, which is exactly one LLM call — so an
    agent's retry pass, the Earnings agent's three self-consistency samples, and the
    Risk Manager's three stages each show up as their own call.
    """
    calls: List[dict] = []
    for runs in _run_data(execution).values():
        for run in runs or []:
            branches = ((run.get("data") or {}).get("ai_languageModel") or [])
            input_tokens = 0
            output_tokens = 0
            found = False
            for branch in branches:
                for item in branch or []:
                    usage = (item.get("json") or {}).get("tokenUsage") or {}
                    if not usage:
                        continue
                    found = True
                    input_tokens += int(usage.get("promptTokens") or 0)
                    output_tokens += int(usage.get("completionTokens") or 0)
            if not found:
                continue
            calls.append(
                {
                    "model": _model_of(run),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": int(run.get("executionTime") or 0),
                }
            )
    return calls
