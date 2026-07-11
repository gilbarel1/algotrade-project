"""Per-LLM-call cost logging into the `costs` table (§4.2, §9.4).

Pricing comes from the §7 model table; `usd_cost` is computed here rather than
anywhere near the LLM, so a model swap is a one-line change (§7: "any agent's
model is a one-field change").

The §4.2 `costs` schema is fixed and keyed per agent+model, not per call:

    costs(run_id TEXT, agent TEXT, model TEXT, input_tokens INT,
          output_tokens INT, usd_cost DOUBLE, latency_ms INT,
          PRIMARY KEY(run_id, agent, model))

So the individual calls an agent makes with one model (the retry pass, the three
self-consistency samples, the Risk Manager's three stages) are summed into a
single row — tokens, cost, and latency all add up. `write_costs` takes rows that
are *already* aggregated and does an INSERT OR REPLACE, which makes a re-harvest
of the same run idempotent (it rewrites identical totals) rather than doubling
them (§5 `/costs/harvest`).
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List

import duckdb

logger = logging.getLogger(__name__)

# §7 model assignments and cost, USD per 1M tokens: (input, output).
PRICES_PER_1M: Dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
}


def usd_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call from the §7 price table.

    An unknown model is priced at 0.0 with a warning rather than raising: a model
    swap must never take down a run, and a visibly-zero cost is honest about the
    fact that we have no price for it (no invented number).
    """
    price = PRICES_PER_1M.get(model)
    if price is None:
        logger.warning(
            "no price for model %r (§7 table: %s) — logging usd_cost 0.0",
            model,
            ", ".join(sorted(PRICES_PER_1M)),
        )
        return 0.0
    in_per_1m, out_per_1m = price
    return (input_tokens * in_per_1m + output_tokens * out_per_1m) / 1_000_000


def aggregate_calls(calls: Iterable[dict]) -> List[dict]:
    """Sum per-call usage into one row per (agent, model) — the §4.2 `costs` key.

    Each call carries `agent`, `model`, `input_tokens`, `output_tokens`,
    `latency_ms`. `usd_cost` is computed per call and summed, so a run that used
    two models for one agent is priced correctly.
    """
    rows: Dict[tuple[str, str], dict] = {}
    for call in calls:
        agent = call["agent"]
        model = call["model"]
        in_tok = int(call.get("input_tokens") or 0)
        out_tok = int(call.get("output_tokens") or 0)
        row = rows.setdefault(
            (agent, model),
            {
                "agent": agent,
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "usd_cost": 0.0,
                "latency_ms": 0,
                "calls": 0,
            },
        )
        row["input_tokens"] += in_tok
        row["output_tokens"] += out_tok
        row["usd_cost"] += usd_cost(model, in_tok, out_tok)
        row["latency_ms"] += int(call.get("latency_ms") or 0)
        row["calls"] += 1
    return sorted(rows.values(), key=lambda r: (r["agent"], r["model"]))


_INSERT = """
INSERT OR REPLACE INTO costs
    (run_id, agent, model, input_tokens, output_tokens, usd_cost, latency_ms)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def write_costs(
    con: duckdb.DuckDBPyConnection, run_id: str, rows: Iterable[dict]
) -> int:
    """Insert-or-replace aggregated cost rows for a run. Returns rows written.

    `rows` must already be one entry per (agent, model) — see `aggregate_calls`.
    """
    written = 0
    for row in rows:
        con.execute(
            _INSERT,
            [
                run_id,
                row["agent"],
                row["model"],
                int(row["input_tokens"]),
                int(row["output_tokens"]),
                float(row["usd_cost"]),
                int(row["latency_ms"]),
            ],
        )
        written += 1
    return written
