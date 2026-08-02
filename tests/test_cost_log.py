"""Cost accounting (§9.4): §7 pricing and the (agent, model) aggregation key."""
from __future__ import annotations

import pytest

from ops import cost_log


class TestPricing:
    def test_haiku_pricing(self):
        # $1 in / $5 out per 1M tokens.
        assert cost_log.usd_cost("anthropic/claude-haiku-4.5", 1_000_000, 0) == pytest.approx(1.0)
        assert cost_log.usd_cost("anthropic/claude-haiku-4.5", 0, 1_000_000) == pytest.approx(5.0)

    def test_a_realistic_call(self):
        # 5,588 in + 825 out on Haiku, the Risk Manager's three passes in one run.
        got = cost_log.usd_cost("anthropic/claude-haiku-4.5", 5588, 825)
        assert got == pytest.approx((5588 * 1.0 + 825 * 5.0) / 1_000_000)

    @pytest.mark.parametrize(
        "model", ["google/gemini-2.5-flash-lite", "x-ai/grok-4.3", "anthropic/claude-haiku-4.5"]
    )
    def test_every_model_in_the_design_table_is_priced(self, model):
        assert cost_log.usd_cost(model, 1000, 100) > 0

    def test_an_unknown_model_costs_zero_rather_than_raising(self):
        """A model swap must never take down a run; a visible 0.0 is the honest
        answer when we have no price, rather than an invented one."""
        assert cost_log.usd_cost("someone/new-model-9", 10_000, 5_000) == 0.0


class TestAggregation:
    def _call(self, agent, model, i, o, ms):
        return {
            "agent": agent,
            "model": model,
            "input_tokens": i,
            "output_tokens": o,
            "latency_ms": ms,
        }

    def test_calls_collapse_onto_the_primary_key(self):
        """§4.2 keys `costs` by (run_id, agent, model) — three Risk Manager passes
        are one row, with summed tokens."""
        rows = cost_log.aggregate_calls(
            [
                self._call("risk_manager", "anthropic/claude-haiku-4.5", 1000, 100, 1000),
                self._call("risk_manager", "anthropic/claude-haiku-4.5", 2000, 200, 2000),
                self._call("risk_manager", "anthropic/claude-haiku-4.5", 3000, 300, 3000),
            ]
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["input_tokens"] == 6000
        assert row["output_tokens"] == 600
        assert row["calls"] == 3

    def test_different_agents_stay_separate(self):
        rows = cost_log.aggregate_calls(
            [
                self._call("technical", "google/gemini-2.5-flash-lite", 283, 67, 1600),
                self._call("earnings", "x-ai/grok-4.3", 13161, 2176, 20522),
            ]
        )
        assert {r["agent"] for r in rows} == {"technical", "earnings"}

    def test_the_same_agent_on_two_models_stays_separate(self):
        """A mid-run model swap must not silently merge into one row."""
        rows = cost_log.aggregate_calls(
            [
                self._call("earnings", "x-ai/grok-4.3", 100, 10, 100),
                self._call("earnings", "anthropic/claude-haiku-4.5", 100, 10, 100),
            ]
        )
        assert len(rows) == 2

    def test_cost_is_computed_per_row(self):
        rows = cost_log.aggregate_calls(
            [self._call("technical", "google/gemini-2.5-flash-lite", 1_000_000, 0, 10)]
        )
        assert rows[0]["usd_cost"] == pytest.approx(0.10)

    def test_no_calls_gives_no_rows(self):
        assert cost_log.aggregate_calls([]) == []
