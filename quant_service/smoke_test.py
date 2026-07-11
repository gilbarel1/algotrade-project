"""Cross-platform smoke test for the Step 0 stub endpoints (§5).

Hits a running service (default http://localhost:8000) and checks every endpoint returns
its §5 contract shape. Uses only the standard library so it works identically on
Windows/PowerShell, macOS, and Linux — no curl quoting pitfalls.

Usage (with the service running via `uvicorn app:app --port 8000`):
    python smoke_test.py
    python smoke_test.py http://localhost:8000

Note: the first `/sentiment` call downloads/loads the FinBERT + HeBERT weights
(cached under HF_HOME), which can take tens of seconds — hence the generous
per-request timeout below.
"""

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

CASES = {
    "/ohlc": {"symbol": "TEVA.TA", "lookback_days": 180, "interval": "1d"},
    "/indicators": {
        "symbol": "TEVA.TA",
        "lookback_days": 120,
        "indicators": ["rsi", "macd", "bbands", "atr"],
    },
    "/sentiment": {
        "items": [
            {"id": "a1", "text": "Teva beats Q1 estimates", "language": "en"},
            {"id": "a2", "text": "report", "language": "he"},
        ]
    },
    "/report": {
        "run_id": "r_2026-06-22T13:00",
        "recommendations": [],
        "summary": "3 long, 1 hold, 1 avoid.",
    },
    "/validate": {
        "agent": "technical",
        "payload": {"signal": "bullish_momentum", "summary": "Momentum building."},
    },
    # §3.4 Risk Manager context: a contrived trio where sentiment is strongly
    # bullish, technical bearish, earnings neutral/low — checked for the §3.4
    # facts shape below (see check_riskmanager_context).
    "/riskmanager/context": {
        "ticker": "TEVA.TA",
        "sentiment": {
            "llm_sentiment": 0.5,
            "model_sentiment": 0.45,
            "disagreement": 0.05,
            "summary": "Bullish tone.",
            "status": "ok",
        },
        "earnings": {
            "is_earnings_window": False,
            "materiality": "low",
            "summary": "No recent disclosures.",
            "status": "ok",
        },
        "technical": {
            "signal": "bearish_momentum",
            "summary": "Momentum fading.",
            "status": "ok",
        },
    },
}

# Payloads that MUST validate ({"valid": true}) — the positive LLM-boundary cases.
VALIDATE_MUST_PASS = [
    {
        "agent": "risk_draft",
        "payload": {
            "recommendation": "long",
            "conviction": "medium",
            "rationale": "Two of three constructive.",
            "earnings_direction": "neutral",
        },
    },
    {
        "agent": "risk_critique",
        "payload": {
            "counter_recommendation": "hold",
            "key_objections": ["earnings window opens in 4 days"],
            "conviction_challenge": "high -> medium",
        },
    },
    {
        "agent": "risk_final",
        "payload": {
            "recommendation": "long",
            "conviction": "medium",
            "rationale": "Event risk cap applied; critique objection incorporated.",
        },
    },
]

# Payloads that MUST come back {"valid": false} — the LLM-boundary guardrail (§9.4).
VALIDATE_MUST_FAIL = [
    {"agent": "technical", "payload": {"signal": "to_the_moon", "summary": "x"}},
    {"agent": "technical", "payload": {"signal": "neutral"}},
    {"agent": "technical", "payload": {"signal": "neutral", "summary": "x", "extra": 1}},
    {"agent": "nonexistent", "payload": {}},
    # Risk Manager boundaries (§3.4): bad enum, missing required field, extra key.
    {"agent": "risk_draft", "payload": {"recommendation": "moon", "conviction": "medium", "rationale": "x", "earnings_direction": "neutral"}},
    {"agent": "risk_draft", "payload": {"recommendation": "long", "conviction": "medium", "rationale": "x"}},
    {"agent": "risk_critique", "payload": {"counter_recommendation": "hold", "key_objections": [], "conviction_challenge": "x"}},
    {"agent": "risk_final", "payload": {"recommendation": "long", "conviction": "medium", "rationale": "x", "extra": 1}},
]

# Minimal shape checks against the §5 contracts.
REQUIRED_KEYS = {
    "/ohlc": ["symbol", "interval", "as_of", "candles", "summary"],
    "/indicators": ["symbol", "as_of", "indicators", "summary"],
    "/sentiment": ["scores", "summary"],
    "/report": ["run_id", "pdf_path"],
    "/validate": ["agent", "valid", "errors"],
    "/riskmanager/context": ["ticker", "prompts", "rubric", "facts", "summary"],
}


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Generous timeout: the first /sentiment call loads the HF models (see module docstring).
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# A §6.3-shaped Risk Manager result, used to exercise /recommendations/store.
_SAMPLE_RECOMMENDATION = {
    "draft": {"recommendation": "long", "conviction": "high", "rationale": "smoke draft"},
    "critique": {
        "counter_recommendation": "hold",
        "key_objections": ["smoke objection"],
        "conviction_challenge": "high -> medium",
    },
    "final": {
        "recommendation": "long",
        "conviction": "medium",
        "rationale": "smoke final",
    },
    "sentiment": {"llm_sentiment": -0.1, "model_sentiment": -0.05, "disagreement": 0.05,
                  "summary": "smoke", "status": "ok"},
    "earnings": {"is_earnings_window": False, "materiality": "low", "summary": "smoke",
                 "status": "ok"},
    "technical": {"signal": "bullish_momentum", "summary": "smoke", "status": "ok"},
    "agent_status": {"sentiment": "ok", "earnings": "ok", "technical": "ok"},
}


def check_orchestration():
    """Exercise the §6.1 orchestration writes end-to-end, then clean up after itself.

    Runs the same four calls the orchestrator makes — /runs/start,
    /recommendations/store, /costs/harvest, /runs/finish — and reads the rows back
    out of DuckDB, so the tables (§4.2) are verified, not just the HTTP shapes. The
    synthetic run is deleted at the end so it never pollutes `python -m ops.cost_report`.

    /costs/harvest needs a live n8n plus N8N_API_KEY; without them it must *degrade*
    (summary prefixed `degraded:`), never 500 — which is what this asserts.
    """
    failures = 0
    run_id = None
    try:
        status, start = post("/runs/start", {"mode": "manual"})
        run_id = start.get("run_id")
        ok = (
            status == 200
            and isinstance(run_id, str)
            and run_id.startswith("r_")
            and isinstance(start.get("watchlist"), list)
            and start.get("watchlist")
            and all(k in start for k in ("window_minutes", "window_days", "lookback_days"))
        )
        if ok:
            print(
                f"OK   /runs/start -> {run_id} "
                f"({len(start['watchlist'])} tickers, windows "
                f"{start['window_minutes']}m/{start['window_days']}d/"
                f"{start['lookback_days']}d)"
            )
        else:
            print(f"FAIL /runs/start: status={status} body={start}")
            return failures + 1

        payload = dict(_SAMPLE_RECOMMENDATION, run_id=run_id, ticker="TEVA.TA")
        status, body = post("/recommendations/store", payload)
        if status == 200 and body.get("stored") == 1:
            print("OK   /recommendations/store -> stored 1")
        else:
            print(f"FAIL /recommendations/store: status={status} body={body}")
            failures += 1

        status, harvest = post("/costs/harvest", {"run_id": run_id})
        if status == 200 and all(
            k in harvest for k in ("run_id", "rows", "calls", "summary")
        ):
            print(f"OK   /costs/harvest -> {harvest['summary']}")
        else:
            print(f"FAIL /costs/harvest: status={status} body={harvest}")
            failures += 1

        status, body = post(
            "/runs/finish",
            {
                "run_id": run_id,
                "status": "ok",
                "report_path": "reports/1970-01-01/0000/report.pdf",
            },
        )
        if status == 200 and not str(body.get("summary", "")).startswith("degraded"):
            print(f"OK   /runs/finish -> {body['summary']}")
        else:
            print(f"FAIL /runs/finish: status={status} body={body}")
            failures += 1

        failures += _check_tables(run_id)
    except Exception as exc:  # noqa: BLE001 - surface any transport error
        print(f"FAIL orchestration endpoints: {exc}")
        failures += 1
    finally:
        if run_id:
            _cleanup(run_id)
    return failures


def _check_tables(run_id):
    """The §4.2 rows the orchestrator is supposed to have written."""
    try:
        from data import cache  # local import: only needed for the DB assertions
    except ImportError as exc:
        print(f"SKIP table checks (run from quant_service/): {exc}")
        return 0

    failures = 0
    con = cache.connect()
    try:
        row = con.execute(
            "SELECT status, report_path, finished_at FROM runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if row and row[0] == "ok" and row[1] and row[2] is not None:
            print(f"OK   runs row closed: status={row[0]} report_path={row[1]}")
        else:
            print(f"FAIL runs row not closed properly: {row}")
            failures += 1

        rec = con.execute(
            "SELECT draft, critique, final, agent_status FROM recommendations "
            "WHERE run_id = ? AND ticker = 'TEVA.TA'",
            [run_id],
        ).fetchone()
        if rec and all(rec):
            print("OK   recommendations row has draft, critique, final, agent_status")
        else:
            print(f"FAIL recommendations row incomplete: {rec}")
            failures += 1
    finally:
        con.close()
    return failures


def _cleanup(run_id):
    """Remove the synthetic smoke run so it never shows up in the cost report."""
    try:
        from data import cache
    except ImportError:
        return
    con = cache.connect()
    try:
        for table in ("costs", "recommendations", "runs"):
            con.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
    finally:
        con.close()


def main():
    failures = 0
    for path, payload in CASES.items():
        try:
            status, body = post(path, payload)
        except Exception as exc:  # noqa: BLE001 - surface any transport error
            print(f"FAIL {path}: {exc}")
            failures += 1
            continue
        missing = [k for k in REQUIRED_KEYS[path] if k not in body]
        if status == 200 and not missing:
            print(f"OK   {path} -> {json.dumps(body, ensure_ascii=False)}")
        else:
            print(f"FAIL {path}: status={status} missing_keys={missing} body={body}")
            failures += 1
            continue

        # §3.4 deterministic facts: the contrived trio (sentiment bullish 0.5/0.45,
        # technical bearish, earnings neutral/low, all ok) must map directions and
        # counts exactly, with no conviction caps active.
        if path == "/riskmanager/context":
            facts = body.get("facts", {})
            checks = {
                "sentiment_direction": facts.get("sentiment_direction") == "bullish",
                "technical_direction": facts.get("technical_direction") == "bearish",
                "no_caps": facts.get("caps", {}).get("any_cap_medium") is False,
                "not_force_avoid": facts.get("caps", {}).get("force_avoid") is False,
                "count_long_neutral": (
                    facts.get("agreement_counts", {})
                    .get("by_earnings_direction", {})
                    .get("neutral", {})
                    .get("bullish")
                    == 1
                ),
            }
            bad = [k for k, ok in checks.items() if not ok]
            if bad:
                print(f"FAIL /riskmanager/context facts wrong: {bad} facts={facts}")
                failures += 1
            else:
                print("OK   /riskmanager/context facts (directions, counts, no caps)")

    # Positive cases: well-formed LLM payloads must validate.
    for payload in VALIDATE_MUST_PASS:
        try:
            status, body = post("/validate", payload)
        except Exception as exc:  # noqa: BLE001 - surface any transport error
            print(f"FAIL /validate (positive) {payload['agent']}: {exc}")
            failures += 1
            continue
        if status == 200 and body.get("valid") is True and not body.get("errors"):
            print(f"OK   /validate accepts {payload['agent']}")
        else:
            print(f"FAIL /validate rejected valid {payload['agent']}: {body}")
            failures += 1

    # Negative cases: malformed LLM payloads must be rejected, never accepted.
    for payload in VALIDATE_MUST_FAIL:
        try:
            status, body = post("/validate", payload)
        except Exception as exc:  # noqa: BLE001 - surface any transport error
            print(f"FAIL /validate (negative) {payload}: {exc}")
            failures += 1
            continue
        if status == 200 and body.get("valid") is False and body.get("errors"):
            print(f"OK   /validate rejects {json.dumps(payload['payload'])} -> {body['errors']}")
        else:
            print(f"FAIL /validate accepted malformed payload {payload}: {body}")
            failures += 1

    # §6.1 orchestration writes (runs, recommendations, costs).
    failures += check_orchestration()

    print("\n" + ("All endpoints OK." if failures == 0 else f"{failures} endpoint(s) FAILED."))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
