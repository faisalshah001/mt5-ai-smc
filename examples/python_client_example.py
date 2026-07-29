"""
Minimal example client for the MT5 AI Bridge canonical analysis endpoint.

Illustrative only -- not part of the application or its test suite.
Uses only the standard library (no `requests` dependency) so it runs
with nothing beyond what `requirements.txt` already installs.

Usage:
    python examples/python_client_example.py [symbol] [timeframe] [count]

Requires the server running locally against a running, logged-in MT5
terminal (`make run`).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"


def analyze(symbol: str, timeframe: str, count: int) -> dict:
    """Call POST /api/v2/analyze and return the parsed JSON response."""

    payload = json.dumps(
        {"symbol": symbol, "timeframe": timeframe, "count": count}
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{BASE_URL}/api/v2/analyze",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "H4"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    try:
        result = analyze(symbol, timeframe, count)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"Request failed ({error.code}): {detail}")
        return
    except urllib.error.URLError as error:
        print(
            f"Could not reach {BASE_URL} -- is the server running "
            f"(`make run`)? Detail: {error.reason}"
        )
        return

    snapshot = result.get("structure_snapshot")
    metadata = result.get("metadata", {})

    print(f"Symbol:            {result.get('symbol')}")
    print(f"Timeframe:         {result.get('timeframe')}")
    print(f"Pipeline version:  {metadata.get('pipeline_version')}")
    print(f"Total events:      {len(result.get('events', []))}")
    print(f"Active liquidity:  {metadata.get('active_liquidity_count')}")
    print(f"Active order blocks: {metadata.get('active_order_block_count')}")

    if snapshot is not None:
        print(f"External trend:    {snapshot.get('external_trend')}")
        print(f"Structure state:   {snapshot.get('structure_state')}")
        print(f"Latest event:      {snapshot.get('latest_event')}")
    else:
        print("Structure snapshot: none (empty result)")


if __name__ == "__main__":
    main()
