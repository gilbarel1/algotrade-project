"""Create the DuckDB schema (§4.2) idempotently.

Run once to produce quant_service/store.duckdb with all six tables:
prices, news, earnings, runs, recommendations, costs.

Usage:
    python store_init.py            # uses DUCKDB_PATH env or the default below
"""

import os

import duckdb

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "store.duckdb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, ts TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE,
    close DOUBLE, volume BIGINT, source TEXT,
    PRIMARY KEY(symbol, ts)
);

CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, symbol TEXT, published_at TIMESTAMP,
    headline TEXT, url TEXT, source TEXT, language TEXT,
    llm_sentiment DOUBLE, model_sentiment DOUBLE, disagreement DOUBLE,
    raw JSON
);

CREATE TABLE IF NOT EXISTS earnings (
    id TEXT PRIMARY KEY, symbol TEXT, published_at TIMESTAMP,
    language TEXT, title TEXT, url TEXT, kind TEXT,
    materiality TEXT, summary TEXT, extracted JSON
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
    mode TEXT, tickers JSON, status TEXT, report_path TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    run_id TEXT, ticker TEXT,
    draft JSON, critique JSON, final JSON,
    sentiment JSON, earnings JSON, technical JSON,
    agent_status JSON,
    PRIMARY KEY(run_id, ticker)
);

CREATE TABLE IF NOT EXISTS costs (
    run_id TEXT, agent TEXT, model TEXT, input_tokens INT,
    output_tokens INT, usd_cost DOUBLE, latency_ms INT,
    PRIMARY KEY(run_id, agent, model)
);

-- The evaluation harness (§9) prints its metrics rather than persisting them, so
-- there is no `evals` table. Older DBs that created one are dropped on init.
DROP TABLE IF EXISTS evals;
"""


def init_db(db_path: str | None = None) -> str:
    path = db_path or os.environ.get("DUCKDB_PATH", DEFAULT_DB_PATH)
    con = duckdb.connect(path)
    try:
        con.execute(SCHEMA)
    finally:
        con.close()
    return path


if __name__ == "__main__":
    created = init_db()
    print(f"DuckDB schema initialized at {created}")
