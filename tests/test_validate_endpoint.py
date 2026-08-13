"""The /validate endpoint over HTTP, and its contract with the n8n workflows (§5, §9.4).

`/validate` is the load-bearing half of the "Pydantic at every LLM boundary"
guardrail: n8n's Pyodide runtime cannot import `schemas/`, so every agent POSTs
its raw LLM JSON here and branches on the `{valid, errors}` it gets back. That
makes two things worth pinning that no other test covers:

1. **The response shape**, asserted through the real ASGI stack rather than by
   calling the handler — an agent's `Valid?` IF node reads `valid`, and its
   stricter-retry prompt interpolates `errors`, so a renamed or restructured
   field silently sends every boundary down the retry path.
2. **The registry ↔ workflow agreement.** The `agent` keys live in two artifacts
   that are edited independently: `SCHEMAS` here and `agent: '<key>'` inside the
   workflow JSON. An unregistered key does not raise — it returns
   `valid: false` forever, so the agent retries once and then degrades *every
   run*, which reads like a flaky model rather than a typo. This extracts the
   keys the shipped workflows actually post, the same way test_rubric_clamp.py
   runs the rubric's own `jsCode`.

Mounting only this router keeps the suite offline and torch-free: `schemas/` is
pure Pydantic, so nothing here pulls transformers, Playwright, or WeasyPrint.
"""
from __future__ import annotations

import json
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.validate import SCHEMAS, router

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(REPO_ROOT, "n8n", "agents")

# The workflows build the request body in a Code node, so the key appears as
# `agent: 'risk_draft'` in embedded JS rather than as JSON.
_AGENT_KEY = re.compile(r"agent:\s*'([a-z_]+)'")


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _post(client, agent, payload):
    return client.post("/validate", json={"agent": agent, "payload": payload}).json()


# A minimal payload that satisfies each schema, so the "accepts good input" leg
# is exercised for every registered boundary rather than one representative.
VALID_PAYLOADS = {
    "technical": {
        "signal": "bullish_momentum",
        "summary": "Momentum has turned up while volatility stays contained.",
    },
    "sentiment": {
        "items": [
            {"id": "a1", "score": 0.6, "reasoning": "Beat on revenue, guidance raised."},
            {"id": "a2", "score": -0.2, "reasoning": "Regulatory query noted in passing."},
        ],
        "summary": "Coverage leans positive on the quarterly beat.",
    },
    "earnings": {
        "kind": "earnings",
        "materiality": "high",
        "summary": "Q3 results released with revenue above guidance.",
        "title_en": "Q3 2026 results",
    },
    # All three keys are required but nullable: null is "not present in the
    # source", which is the only way a sample may decline to give a figure.
    "earnings_extraction": {
        "revenue": "4.1 billion NIS",
        "eps": None,
        "guidance": None,
    },
    "risk_draft": {
        "recommendation": "long",
        "conviction": "medium",
        "rationale": "Three signals line up on the bullish side.",
        "earnings_direction": "bullish",
    },
    "risk_critique": {
        "counter_recommendation": "hold",
        "key_objections": ["The technical read leans on a single indicator."],
        "conviction_challenge": "Medium overstates a two-of-three agreement.",
    },
    "risk_final": {
        "recommendation": "hold",
        "conviction": "low",
        "rationale": "The critique undercuts the draft's strongest leg.",
    },
}


def test_every_registered_agent_has_a_valid_payload_fixture():
    """Guards the two tests below: a new schema must arrive with a fixture."""
    assert set(VALID_PAYLOADS) == set(SCHEMAS)


@pytest.mark.parametrize("agent", sorted(SCHEMAS))
def test_valid_payload_returns_the_success_contract(client, agent):
    body = _post(client, agent, VALID_PAYLOADS[agent])
    assert body == {"agent": agent, "valid": True, "errors": []}


@pytest.mark.parametrize("agent", sorted(SCHEMAS))
def test_empty_payload_is_rejected_with_errors(client, agent):
    """A lazy `{}` must fail closed — and say why, since the retry prompt quotes it."""
    body = _post(client, agent, {})
    assert body["agent"] == agent
    assert body["valid"] is False
    assert body["errors"], "a rejection with no reason gives the retry nothing to correct"
    assert all(isinstance(e, str) for e in body["errors"])


def test_invented_enum_is_rejected_by_name(client):
    """The §3.4 call vocabulary is closed; `errors` must locate the offending field."""
    body = _post(
        client,
        "risk_final",
        {**VALID_PAYLOADS["risk_final"], "recommendation": "moon"},
    )
    assert body["valid"] is False
    assert any("recommendation" in e for e in body["errors"])


def test_unknown_agent_degrades_rather_than_raising(client):
    """An unroutable key returns 200 with the contract, not a 500 the agent can't read."""
    resp = client.post("/validate", json={"agent": "nope", "payload": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    # The message lists what *is* supported — this is the only place a workflow
    # author with a typo'd key finds out what to use instead.
    assert "nope" in body["errors"][0]
    for agent in SCHEMAS:
        assert agent in body["errors"][0]


def test_workflows_only_post_registered_agent_keys():
    """Every `agent` key the shipped workflows post must exist in SCHEMAS (§5)."""
    posted = {}
    for name in sorted(os.listdir(AGENTS_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(AGENTS_DIR, name)
        with open(path, encoding="utf-8") as fh:
            workflow = json.load(fh)
        # Read from the parsed node graph, not the raw file, so a key in a
        # comment or a disabled node's leftover text cannot satisfy this.
        for node in workflow.get("nodes", []):
            code = (node.get("parameters") or {}).get("jsCode") or ""
            for key in _AGENT_KEY.findall(code):
                posted.setdefault(key, set()).add(name)

    assert posted, "found no agent keys — the extraction regex has drifted from the workflows"
    unregistered = {k: sorted(v) for k, v in posted.items() if k not in SCHEMAS}
    assert not unregistered, f"workflows post agent keys /validate cannot route: {unregistered}"
