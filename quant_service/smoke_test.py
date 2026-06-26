"""Cross-platform smoke test for the Step 0 stub endpoints (§5).

Hits a running service (default http://localhost:8000) and checks every endpoint returns
its §5 contract shape. Uses only the standard library so it works identically on
Windows/PowerShell, macOS, and Linux — no curl quoting pitfalls.

Usage (with the service running via `uvicorn app:app --port 8000`):
    python smoke_test.py
    python smoke_test.py http://localhost:8000
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
}

# Minimal shape checks against the §5 contracts.
REQUIRED_KEYS = {
    "/ohlc": ["symbol", "interval", "as_of", "candles", "summary"],
    "/indicators": ["symbol", "as_of", "indicators", "summary"],
    "/sentiment": ["scores", "summary"],
    "/report": ["run_id", "pdf_path"],
}


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


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

    print("\n" + ("All endpoints OK." if failures == 0 else f"{failures} endpoint(s) FAILED."))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
