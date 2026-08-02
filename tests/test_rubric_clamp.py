"""The §3.4 rubric clamp, executed as the workflow's own JavaScript.

The clamp is the "mechanism" half of the rubric: the model authors the final call, and
this enforces it so a violation cannot reach the report. It lives inside an n8n Code
node, so these tests extract that node's `jsCode` from `n8n/agents/risk_manager.json`
and run it under Node with a stubbed `$()` — testing the shipped code itself rather
than a Python re-implementation that could drift from it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO_ROOT, "n8n", "agents", "risk_manager.json")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js is required to run the workflow's clamp code"
)

HARNESS = """
// `node -e` puts the first user argument at argv[1] — there is no script filename.
const fs = require('fs');
const wf = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const code = wf.nodes.find(n => n.name === 'Apply Rubric Clamp').parameters.jsCode;
const input = JSON.parse(process.argv[2]);
const $ = (name) => ({ first: () => ({ json: {
  'Prepare Context': { ticker: 'TEST', facts: input.facts,
                       sentiment: {}, earnings: {}, technical: {} },
  'Commit Draft':    { draft: input.draft },
  'Commit Critique': { critique: input.critique },
  'Commit Final':    { final: input.final },
}[name] }) });
process.stdout.write(JSON.stringify(new Function('$', code)($)[0].json));
"""


def run_clamp(facts, final, draft=None, critique=None):
    draft = draft or {"recommendation": "hold", "conviction": "low", "earnings_direction": "neutral"}
    critique = critique or {
        "counter_recommendation": "hold",
        "key_objections": ["o"],
        "conviction_challenge": "c",
    }
    payload = json.dumps({"facts": facts, "draft": draft, "critique": critique, "final": final})
    out = subprocess.run(
        ["node", "-e", HARNESS, "--", WORKFLOW, payload],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["final"]


def facts(*, bullish=1, bearish=1, force_avoid=False, cap_medium=False, degraded=0,
          strong_bearish=False):
    return {
        "agreement_counts": {"by_earnings_direction": {"neutral": {"bullish": bullish,
                                                                   "bearish": bearish}}},
        "has_strong_bearish": strong_bearish,
        "strong_signals": {"earnings": False},
        "caps": {
            "force_avoid": force_avoid,
            "any_cap_medium": cap_medium,
            "degraded_count": degraded,
            "earnings_event": False,
            "dual_sentiment": False,
        },
    }


class TestRubricEnforcement:
    def test_long_on_one_agent_is_downgraded_to_hold(self):
        out = run_clamp(facts(bullish=1), {"recommendation": "long", "conviction": "high",
                                           "rationale": "r"})
        assert out["recommendation"] == "hold"

    def test_long_on_two_agents_survives_but_is_capped(self):
        out = run_clamp(facts(bullish=2), {"recommendation": "long", "conviction": "high",
                                           "rationale": "r"})
        assert out["recommendation"] == "long"
        assert out["conviction"] == "medium"

    def test_short_without_a_strong_bearish_signal_becomes_hold(self):
        out = run_clamp(facts(bearish=2), {"recommendation": "short", "conviction": "medium",
                                           "rationale": "r"})
        assert out["recommendation"] == "hold"

    def test_short_with_a_strong_bearish_signal_survives(self):
        out = run_clamp(facts(bearish=2, strong_bearish=True),
                        {"recommendation": "short", "conviction": "medium", "rationale": "r"})
        assert out["recommendation"] == "short"

    def test_avoid_without_two_degraded_agents_becomes_hold(self):
        """`avoid` means insufficient evidence — never a directional call."""
        out = run_clamp(facts(degraded=1), {"recommendation": "avoid", "conviction": "low",
                                            "rationale": "r"})
        assert out["recommendation"] == "hold"

    def test_two_degraded_agents_force_avoid(self):
        out = run_clamp(facts(force_avoid=True, degraded=2),
                        {"recommendation": "long", "conviction": "high", "rationale": "r"})
        assert out["recommendation"] == "avoid"
        assert out["conviction"] == "low"


class TestClampIsAlwaysRecorded:
    """A silent rewrite would destroy the auditability the trace exists for."""

    def test_a_changed_recommendation_leads_the_rationale(self):
        out = run_clamp(facts(degraded=1),
                        {"recommendation": "avoid", "conviction": "low",
                         "rationale": "Final call: AVOID until sentiment recovers."})
        assert out["rationale"].startswith("[Rubric clamp:")
        # It must say which value came from where, and flag what follows as pre-clamp.
        assert "'hold'" in out["rationale"] and "'avoid'" in out["rationale"]
        assert "before the clamp" in out["rationale"]
        # The model's own words survive verbatim.
        assert "Final call: AVOID until sentiment recovers." in out["rationale"]

    def test_a_conviction_cap_appends_instead(self):
        """Nothing contradicts, so the argument stays on top."""
        out = run_clamp(facts(bullish=2), {"recommendation": "long", "conviction": "high",
                                           "rationale": "Two of three constructive."})
        assert out["rationale"].startswith("Two of three constructive.")
        assert "[Rubric clamp:" in out["rationale"]

    def test_an_untouched_call_gets_no_note(self):
        out = run_clamp(facts(), {"recommendation": "hold", "conviction": "low",
                                  "rationale": "Balanced."})
        assert out["rationale"] == "Balanced."
