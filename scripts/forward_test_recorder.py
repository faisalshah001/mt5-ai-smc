"""
Read-only forward-testing recorder for the manual EURUSD ICT/SMC
signal endpoint (POST /api/v2/strategy/smc-signal).

Demo Forward-Testing Phase 1: calls the existing endpoint exactly as
an operator would, and appends one JSON Lines record per evaluation
to a durable, append-only log file -- never overwriting prior
records. This script contains no order-placement code of any kind;
it only ever issues a POST to the existing, manual-approval-only
endpoint and records the response verbatim. It does not import or
call any strategy/engine code directly, so it cannot duplicate or
diverge from the pipeline's own logic.

Requires the server already running against a live, logged-in MT5
demo terminal (`make run`, or `uvicorn main:app --reload`). Uses only
the standard library, matching examples/python_client_example.py's
existing convention. Not part of the application or its automated
test suite for the same reason examples/ is excluded (it depends on a
live server + MT5 terminal) -- but see tests/test_forward_test_recorder.py
for the parts of this module that ARE unit-tested: the pure
record-building and append-only writing logic, which has no network
or MT5 dependency.

Usage (PowerShell):

    # One evaluation:
    python scripts\\forward_test_recorder.py

    # Repeated evaluations every 5 minutes until Ctrl+C:
    python scripts\\forward_test_recorder.py --loop --interval-seconds 300

    # Bounded run -- 12 evaluations, 5 minutes apart (1 hour total):
    python scripts\\forward_test_recorder.py --loop --interval-seconds 300 --iterations 12

Optional: set MT5_AI_BRIDGE_API_KEY in the environment if the server
has API-key auth enabled (app/security.py) -- read at call time only,
never written to the log file or persisted anywhere by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
ENDPOINT_PATH = "/api/v2/strategy/smc-signal"
DEFAULT_SYMBOL = "EURUSD"

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_INTERVAL_SECONDS = 300.0

# Safe-polling floor: M5 is the fastest timeframe this strategy
# consults, so evaluating more often than every 30s has no
# informational value and only adds load to the MT5 terminal/API.
MINIMUM_INTERVAL_SECONDS = 30.0

DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "logs"
    / "forward_test"
    / "smc_signal.jsonl"
)

# Mirrors app/security.py's constants -- duplicated here (not
# imported) so this script stays dependency-free and standalone,
# matching examples/python_client_example.py's existing convention.
API_KEY_ENV_VAR = "MT5_AI_BRIDGE_API_KEY"
API_KEY_HEADER = "X-API-Key"

# Copied verbatim from the endpoint's response into every record --
# no reinterpretation of trading fields happens anywhere in this
# module.
RECORD_FIELDS_FROM_RESPONSE = (
    "status",
    "direction",
    "entry",
    "stop_loss",
    "take_profit",
    "risk_percent",
    "risk_reward",
    "position_size",
    "confidence",
    "evidence",
    "rejection_reasons",
)


def get_strategy_version() -> str:
    """
    Resolve the current Git commit hash, to tag every record with the
    exact codebase state that produced it.

    Returns "unknown" if git is unavailable, this isn't a Git
    checkout, or the call fails for any reason -- this function never
    raises.
    """

    repo_root = Path(__file__).resolve().parent.parent

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ):
        return "unknown"

    commit_hash = result.stdout.strip()

    return commit_hash if commit_hash else "unknown"


def call_smc_signal_endpoint(
    *,
    base_url: str,
    symbol: str,
    timeout_seconds: float,
) -> tuple[Optional[dict], Optional[int], Optional[dict]]:
    """
    Call POST {base_url}/api/v2/strategy/smc-signal exactly once.

    This is the only network call this module ever makes, and it is
    always a read-only evaluation request -- no execution endpoint
    exists anywhere in this codebase for it to call instead.

    Returns (response_json, http_status_code, api_error). Exactly one
    of (response_json, api_error) is non-None on return.
    """

    payload = json.dumps({"symbol": symbol}).encode("utf-8")

    headers = {"Content-Type": "application/json"}

    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()

    if api_key:
        headers[API_KEY_HEADER] = api_key

    request = urllib.request.Request(
        f"{base_url}{ENDPOINT_PATH}",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=timeout_seconds
        ) as response:
            body = response.read().decode("utf-8")

            return json.loads(body), response.status, None

    except urllib.error.HTTPError as error:
        raw_detail = error.read().decode("utf-8", errors="replace")

        try:
            parsed_detail: Any = json.loads(raw_detail)
        except json.JSONDecodeError:
            parsed_detail = raw_detail

        return (
            None,
            error.code,
            {
                "kind": "http_error",
                "http_status_code": error.code,
                "detail": parsed_detail,
            },
        )

    except urllib.error.URLError as error:
        return (
            None,
            None,
            {
                "kind": "connection_error",
                "http_status_code": None,
                "detail": str(error.reason),
            },
        )


def build_record(
    *,
    timestamp_utc: str,
    symbol: str,
    strategy_version: str,
    http_status_code: Optional[int],
    response_json: Optional[dict],
    api_error: Optional[dict],
) -> dict[str, Any]:
    """
    Assemble one forward-test record.

    On a successful call, every field in RECORD_FIELDS_FROM_RESPONSE
    is copied directly from response_json -- verbatim, with no
    reinterpretation. On a failed call (response_json is None), those
    fields are all None and api_error describes what went wrong. Pure
    function: no I/O, no network, no clock access (timestamp_utc and
    strategy_version are supplied by the caller) -- fully
    unit-testable without a live server or MT5 terminal.
    """

    record: dict[str, Any] = {
        "timestamp_utc": timestamp_utc,
        "symbol": symbol,
        "strategy_version": strategy_version,
        "http_status_code": http_status_code,
    }

    for field in RECORD_FIELDS_FROM_RESPONSE:
        record[field] = (
            response_json.get(field) if response_json is not None else None
        )

    record["api_error"] = api_error

    return record


def append_record(record: dict[str, Any], log_path: Path) -> None:
    """
    Append one record as a single JSON line.

    Always append-only: opens the file in "a" mode only, never "w" --
    this function can create the log directory if missing, but it can
    never truncate or overwrite a prior record.
    """

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _validate_interval(interval_seconds: float) -> None:
    """Raise ValueError if interval_seconds is below the safe floor."""

    if interval_seconds < MINIMUM_INTERVAL_SECONDS:
        raise ValueError(
            "--interval-seconds must be at least "
            f"{MINIMUM_INTERVAL_SECONDS:.0f} (got {interval_seconds}). "
            "This floor exists to avoid hammering the MT5 "
            "terminal/API."
        )


def _print_summary(record: dict[str, Any]) -> None:
    """Print one human-readable line per evaluation for interactive use."""

    if record["api_error"] is not None:
        print(
            f"[{record['timestamp_utc']}] API ERROR: "
            f"{record['api_error']['kind']} "
            f"(http_status={record['http_status_code']}): "
            f"{record['api_error']['detail']}"
        )
        return

    print(
        f"[{record['timestamp_utc']}] status={record['status']} "
        f"direction={record['direction']} entry={record['entry']} "
        f"stop_loss={record['stop_loss']} "
        f"rejection_reasons={record['rejection_reasons']}"
    )


def run_once(
    *,
    base_url: str,
    symbol: str,
    strategy_version: str,
    timeout_seconds: float,
    log_path: Path,
) -> dict[str, Any]:
    """Run exactly one evaluation: call, record, append, summarise."""

    response_json, http_status_code, api_error = call_smc_signal_endpoint(
        base_url=base_url,
        symbol=symbol,
        timeout_seconds=timeout_seconds,
    )

    record = build_record(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        strategy_version=strategy_version,
        http_status_code=http_status_code,
        response_json=response_json,
        api_error=api_error,
    )

    append_record(record, log_path)

    _print_summary(record)

    return record


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only forward-testing recorder for "
            "POST /api/v2/strategy/smc-signal. Calls the existing "
            "endpoint and appends one JSON Lines record per "
            "evaluation. Never places an order."
        ),
    )

    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Server base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help=f"Symbol to request (default: {DEFAULT_SYMBOL}).",
    )
    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help=f"JSON Lines log path (default: {DEFAULT_LOG_PATH}).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Repeat evaluations on an interval instead of running once.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=(
            "Seconds between evaluations when --loop is set "
            f"(default: {DEFAULT_INTERVAL_SECONDS:.0f}, "
            f"minimum: {MINIMUM_INTERVAL_SECONDS:.0f})."
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=(
            "Number of evaluations to run when --loop is set "
            "(default: unlimited, until Ctrl+C)."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP request timeout (default: {DEFAULT_TIMEOUT_SECONDS:.0f}).",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)

    if args.loop:
        try:
            _validate_interval(args.interval_seconds)
        except ValueError as error:
            print(f"Refusing to start: {error}")
            sys.exit(2)

    strategy_version = get_strategy_version()
    log_path = Path(args.log_file)

    print("Forward-test recorder starting.")
    print(f"  Endpoint:         POST {args.base_url}{ENDPOINT_PATH}")
    print(f"  Symbol:           {args.symbol}")
    print(f"  Strategy version: {strategy_version}")
    print(f"  Log file:         {log_path}")

    if args.loop:
        iterations_desc = (
            "unlimited (Ctrl+C to stop)"
            if args.iterations is None
            else str(args.iterations)
        )
        print(f"  Loop interval:    {args.interval_seconds:.0f}s")
        print(f"  Iterations:       {iterations_desc}")

    print()

    common_kwargs = dict(
        base_url=args.base_url,
        symbol=args.symbol,
        strategy_version=strategy_version,
        timeout_seconds=args.timeout_seconds,
        log_path=log_path,
    )

    if not args.loop:
        run_once(**common_kwargs)
        return

    completed = 0

    try:
        while args.iterations is None or completed < args.iterations:
            run_once(**common_kwargs)
            completed += 1

            if args.iterations is not None and completed >= args.iterations:
                break

            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("\nStopped by user.")

    print(f"Done. {completed} evaluation(s) recorded to {log_path}")


if __name__ == "__main__":
    main()
