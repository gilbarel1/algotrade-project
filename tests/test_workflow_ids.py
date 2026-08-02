"""The §4.4 workflow-id ladder: env var, then lookup by name, then config.

Ids are minted per n8n import, so a tracked id 404s on every other machine and cost
attribution stops silently. These tests pin the resolution order and the ambiguity guard.
`n8n_api.list_workflows` is monkeypatched, so nothing here touches a live n8n.
"""
from __future__ import annotations

import pytest

from ops import n8n_api
from routers import costs

LIVE = [
    {"id": "wf-tech", "name": "Technical Agent (§3.3)"},
    {"id": "wf-sent", "name": "Sentiment Agent (§3.1)"},
    {"id": "wf-earn", "name": "Earnings Agent (§3.2)"},
    {"id": "wf-risk", "name": "Risk Manager (§3.4 three-stage critique loop)"},
    {"id": "wf-chat", "name": "Chat Assistant (§6.5)"},
]


@pytest.fixture
def live_n8n(monkeypatch):
    monkeypatch.setattr(n8n_api, "list_workflows", lambda: list(LIVE))


@pytest.fixture
def dead_n8n(monkeypatch):
    def boom():
        raise n8n_api.N8nApiError("n8n API unreachable at http://127.0.0.1:5678")

    monkeypatch.setattr(n8n_api, "list_workflows", boom)


class TestNameLookup:
    def test_empty_config_resolves_every_agent(self, live_n8n, clean_env):
        resolved, notes = costs._resolve_workflow_ids({})
        assert resolved == {
            "technical": "wf-tech",
            "sentiment": "wf-sent",
            "earnings": "wf-earn",
            "risk_manager": "wf-risk",
            "chat": "wf-chat",
        }
        assert any(n.startswith("name:") for n in notes)

    def test_section_suffix_may_drift(self, monkeypatch, clean_env):
        """Matching falls back to the stem before " (", so §-numbers can change."""
        monkeypatch.setattr(
            n8n_api,
            "list_workflows",
            lambda: [{"id": "wf-tech", "name": "Technical Agent (renamed v2)"}],
        )
        resolved, _ = costs._resolve_workflow_ids({})
        assert resolved["technical"] == "wf-tech"

    def test_ambiguous_stem_resolves_to_neither(self, monkeypatch, clean_env):
        """Two copies of an agent: guessing would mis-attribute costs silently."""
        monkeypatch.setattr(
            n8n_api,
            "list_workflows",
            lambda: [
                {"id": "a", "name": "Technical Agent (§9.9)"},
                {"id": "b", "name": "Technical Agent (copy)"},
            ],
        )
        resolved, notes = costs._resolve_workflow_ids({})
        assert "technical" not in resolved
        assert any("unresolved" in n for n in notes)

    def test_exact_match_wins_over_an_ambiguous_stem(self, monkeypatch, clean_env):
        monkeypatch.setattr(
            n8n_api,
            "list_workflows",
            lambda: [
                {"id": "a", "name": "Technical Agent (§3.3)"},
                {"id": "b", "name": "Technical Agent (copy)"},
            ],
        )
        resolved, _ = costs._resolve_workflow_ids({})
        assert resolved["technical"] == "a"


class TestPrecedence:
    def test_env_beats_the_name_lookup(self, live_n8n, clean_env, monkeypatch):
        monkeypatch.setenv("N8N_WF_TECHNICAL", "pinned-by-env")
        resolved, notes = costs._resolve_workflow_ids({})
        assert resolved["technical"] == "pinned-by-env"
        assert resolved["sentiment"] == "wf-sent"  # others still resolve by name
        assert any(n.startswith("env:") for n in notes)

    def test_config_is_the_last_resort(self, live_n8n, clean_env):
        """An agent n8n has no workflow for still resolves from config."""
        resolved, notes = costs._resolve_workflow_ids({"n8n_workflow_ids": {"ghost": "cfg-id"}})
        assert resolved["ghost"] == "cfg-id"
        assert any(n.startswith("config:") for n in notes)

    def test_name_lookup_beats_a_stale_config_id(self, live_n8n, clean_env):
        """The bug this ladder exists for: another machine's id committed to config."""
        resolved, _ = costs._resolve_workflow_ids(
            {"n8n_workflow_ids": {"technical": "someone-elses-id"}}
        )
        assert resolved["technical"] == "wf-tech"


class TestDegradation:
    def test_unreachable_n8n_degrades_with_a_reason(self, dead_n8n, clean_env):
        resolved, notes = costs._resolve_workflow_ids({})
        assert resolved == {}
        assert any("name lookup unavailable" in n for n in notes)

    def test_unreachable_n8n_still_honours_env_and_config(self, dead_n8n, clean_env, monkeypatch):
        monkeypatch.setenv("N8N_WF_EARNINGS", "env-id")
        resolved, _ = costs._resolve_workflow_ids({"n8n_workflow_ids": {"chat": "cfg-id"}})
        assert resolved["earnings"] == "env-id"
        assert resolved["chat"] == "cfg-id"


def test_lookup_keys_come_from_the_shipped_workflow_files():
    """The names are read from n8n/*.json, so renaming a workflow keeps them in sync."""
    for agent in ("technical", "sentiment", "earnings", "risk_manager", "chat"):
        assert costs._shipped_name(agent), f"no shipped workflow name for {agent}"
