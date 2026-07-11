"""`python -m ops.cost_report` — summarize the last N runs' LLM costs (§9.4).

Reads the `costs` and `runs` tables written during a run (§4.2); makes no network
calls and never writes. Run from `quant_service/`:

    python -m ops.cost_report              # last 5 runs
    python -m ops.cost_report --runs 20
"""

from __future__ import annotations

import argparse

from data import cache


def _fmt_usd(value: float) -> str:
    return f"${value:,.4f}"


def report(n_runs: int) -> str:
    con = cache.connect()
    try:
        runs = con.execute(
            """
            SELECT run_id, started_at, mode, status
              FROM runs
             ORDER BY started_at DESC
             LIMIT ?
            """,
            [n_runs],
        ).fetchall()

        if not runs:
            return "No runs recorded yet — run the orchestrator first."

        lines = [f"LLM cost report — last {len(runs)} run(s)", ""]
        grand_total = 0.0

        for run_id, started_at, mode, status in runs:
            rows = con.execute(
                """
                SELECT agent, model, input_tokens, output_tokens, usd_cost, latency_ms
                  FROM costs
                 WHERE run_id = ?
                 ORDER BY agent, model
                """,
                [run_id],
            ).fetchall()

            run_total = sum(row[4] for row in rows)
            grand_total += run_total
            lines.append(
                f"{run_id}  [{mode}/{status}]  {started_at}  total {_fmt_usd(run_total)}"
            )
            if not rows:
                lines.append("    (no LLM calls logged)")
            for agent, model, in_tok, out_tok, usd, latency in rows:
                lines.append(
                    f"    {agent:<13} {model:<30} "
                    f"in {in_tok:>7,}  out {out_tok:>6,}  "
                    f"{_fmt_usd(usd):>10}  {latency:>7,} ms"
                )
            lines.append("")

        lines.append(f"Grand total across {len(runs)} run(s): {_fmt_usd(grand_total)}")
        return "\n".join(lines)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=5, help="how many recent runs to summarize"
    )
    args = parser.parse_args()
    print(report(args.runs))


if __name__ == "__main__":
    main()
