"""DuckDB writer for the `runs` and `recommendations` tables (§4.2, §6.1).

The orchestrator (n8n) cannot open DuckDB, so its three writes — open a run, store a
per-ticker recommendation, close the run — are served over HTTP by `routers/runs.py`
and land here. Same `cache.connect()` / INSERT OR REPLACE idiom as `news_store.py`.

The §4.2 schemas are fixed:
    runs(run_id TEXT PK, started_at TIMESTAMP, finished_at TIMESTAMP, mode TEXT,
         tickers JSON, status TEXT, report_path TEXT)
    recommendations(run_id TEXT, ticker TEXT, draft JSON, critique JSON, final JSON,
                    sentiment JSON, earnings JSON, technical JSON, agent_status JSON,
                    PRIMARY KEY(run_id, ticker))

Times are stored UTC and rendered Asia/Jerusalem downstream (§11.2).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, List, Optional

import duckdb


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _db_utc(dt: datetime) -> datetime:
    """Convert to a naive UTC datetime for a DuckDB TIMESTAMP column (§11.2: store UTC).

    DuckDB's TIMESTAMP is timezone-*naive*: handed an aware datetime it converts to the
    session's local zone and drops the offset, so an aware 12:00Z silently lands as 15:00
    on an Asia/Jerusalem box. Everything downstream (the `/costs/harvest` window, the
    report's rendered times) would then read those local wall-clock numbers back as UTC.
    Normalizing to UTC and stripping the tzinfo here keeps what lands in the column
    genuinely UTC.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def mint_run_id(con: duckdb.DuckDBPyConnection, now: Optional[datetime] = None) -> str:
    """`r_<UTC ISO minute>` per §5, e.g. `r_2026-06-22T13:00`.

    Two runs inside the same minute would collide on the `runs` primary key (a
    manual demo run during a scheduled hour is exactly that case), so a taken id
    falls back to second precision rather than overwriting the earlier run.
    """
    now = now or utc_now()
    run_id = now.strftime("r_%Y-%m-%dT%H:%M")
    taken = con.execute(
        "SELECT 1 FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    if taken:
        run_id = now.strftime("r_%Y-%m-%dT%H:%M:%S")
    return run_id


def start_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    mode: str,
    tickers: List[str],
    started_at: datetime,
) -> None:
    """Open the run: `finished_at`/`report_path` stay NULL until `finish_run`."""
    con.execute(
        """
        INSERT OR REPLACE INTO runs
            (run_id, started_at, finished_at, mode, tickers, status, report_path)
        VALUES (?, ?, NULL, ?, ?, 'running', NULL)
        """,
        [run_id, _db_utc(started_at), mode, json.dumps(tickers)],
    )


def finish_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    status: str,
    report_path: Optional[str],
    finished_at: datetime,
) -> int:
    """Close the run. Returns rows updated (0 if the run_id is unknown)."""
    con.execute(
        """
        UPDATE runs
           SET finished_at = ?, status = ?, report_path = ?
         WHERE run_id = ?
        """,
        [_db_utc(finished_at), status, report_path, run_id],
    )
    return con.execute(
        "SELECT COUNT(*) FROM runs WHERE run_id = ? AND finished_at IS NOT NULL",
        [run_id],
    ).fetchone()[0]


def get_run(con: duckdb.DuckDBPyConnection, run_id: str) -> Optional[dict]:
    row = con.execute(
        "SELECT run_id, started_at, finished_at, mode, tickers, status, report_path "
        "FROM runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "mode": row[3],
        "tickers": row[4],
        "status": row[5],
        "report_path": row[6],
    }


_INSERT_REC = """
INSERT OR REPLACE INTO recommendations
    (run_id, ticker, draft, critique, final,
     sentiment, earnings, technical, agent_status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _as_json(value: Any) -> Optional[str]:
    """Serialize an agent/pass object for a JSON column; NULL when absent."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def upsert_recommendation(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    ticker: str,
    draft: Any,
    critique: Any,
    final: Any,
    sentiment: Any,
    earnings: Any,
    technical: Any,
    agent_status: Any,
) -> int:
    """Insert-or-replace one §6.3 per-ticker result. Re-running a ticker replaces it."""
    con.execute(
        _INSERT_REC,
        [
            run_id,
            ticker,
            _as_json(draft),
            _as_json(critique),
            _as_json(final),
            _as_json(sentiment),
            _as_json(earnings),
            _as_json(technical),
            _as_json(agent_status),
        ],
    )
    return 1
