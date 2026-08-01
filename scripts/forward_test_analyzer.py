"""
Read-only analysis report for the forward-test JSON Lines log
produced by scripts/forward_test_recorder.py.

Demo Forward-Testing Phase 2: reads
logs/forward_test/smc_signal.jsonl and prints a console report
summarising every recorded evaluation. This module never opens the
log file for writing, appending, or any other mutating mode -- it
only ever reads it (see load_records, the sole point where the file
is touched). It contains no execution code of any kind and does not
import or call any strategy/engine/MT5 code -- it only reads records
already written by the recorder.

Uses only the Python standard library, matching
scripts/forward_test_recorder.py's existing convention.

Usage (PowerShell):

    # Analyse the default log (logs/forward_test/smc_signal.jsonl):
    python scripts\\forward_test_analyzer.py

    # Analyse a specific log file:
    python scripts\\forward_test_analyzer.py --log-file path\\to\\other.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPORT_DIVIDER = "=" * 72
SECTION_DIVIDER = "-" * 72

# Mirrors scripts/forward_test_recorder.py's own DEFAULT_LOG_PATH --
# duplicated here (not imported) so this script stays standalone and
# runnable directly (`python scripts/forward_test_analyzer.py`)
# without requiring the repository root to already be on sys.path,
# matching the recorder's own precedent of not importing from `app`.
DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "logs"
    / "forward_test"
    / "smc_signal.jsonl"
)

# The exact fields shown for every SIGNAL_PENDING_APPROVAL evaluation,
# copied verbatim from each record -- no reinterpretation.
PENDING_SIGNAL_FIELDS = (
    "timestamp_utc",
    "direction",
    "entry",
    "stop_loss",
    "take_profit",
    "confidence",
    "risk_reward",
)


def load_records(log_path: Path) -> tuple[list[dict[str, Any]], int]:
    """
    Read every record from a JSON Lines forward-test log.

    Read-only: the file is opened in "r" mode only -- this function
    never writes to, appends to, or otherwise modifies the log.

    Returns (records, malformed_line_count). A malformed line (e.g.
    from a process killed mid-write) is skipped, not fatal, so one
    corrupted line never prevents analysis of every other record. A
    missing file returns an empty list, not an error.
    """

    if not log_path.exists():
        return [], 0

    records: list[dict[str, Any]] = []
    malformed_line_count = 0

    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()

            if not stripped:
                continue

            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                malformed_line_count += 1

    return records, malformed_line_count


def _parse_timestamp_safe(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string; None on anything invalid."""

    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compute_summary_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    total_evaluations, first_timestamp, last_timestamp,
    duration_seconds, evaluations_per_hour.

    evaluations_per_hour is None when the time span between the first
    and last timestamped record is zero (e.g. a single record) --
    the rate is undefined over a zero-length window, not infinite or
    zero.
    """

    total = len(records)

    timestamps = sorted(
        ts
        for record in records
        if (ts := _parse_timestamp_safe(record.get("timestamp_utc"))) is not None
    )

    if not timestamps:
        return {
            "total_evaluations": total,
            "first_timestamp": None,
            "last_timestamp": None,
            "duration_seconds": 0.0,
            "evaluations_per_hour": None,
        }

    first = timestamps[0]
    last = timestamps[-1]
    duration_seconds = (last - first).total_seconds()

    evaluations_per_hour = (
        total / (duration_seconds / 3600.0) if duration_seconds > 0 else None
    )

    return {
        "total_evaluations": total,
        "first_timestamp": first.isoformat(),
        "last_timestamp": last.isoformat(),
        "duration_seconds": duration_seconds,
        "evaluations_per_hour": evaluations_per_hour,
    }


def compute_status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """
    Count by `status`. A record with status=None (an API-level
    failure -- see api_error) is bucketed as "API_ERROR" so every
    record lands in exactly one bucket.
    """

    counts: Counter[str] = Counter()

    for record in records:
        status = record.get("status")
        counts[status if status is not None else "API_ERROR"] += 1

    return dict(counts)


def compute_http_status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count by http_status_code (as a string; "none" if absent)."""

    counts: Counter[str] = Counter()

    for record in records:
        code = record.get("http_status_code")
        counts[str(code) if code is not None else "none"] += 1

    return dict(counts)


def compute_api_error_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count by api_error["kind"]; "none" for records with no error."""

    counts: Counter[str] = Counter()

    for record in records:
        api_error = record.get("api_error")
        kind = api_error["kind"] if api_error else "none"
        counts[kind] += 1

    return dict(counts)


def compute_rejection_reason_counts(
    records: list[dict[str, Any]],
) -> list[tuple[str, int]]:
    """
    Every rejection-reason string across every record, ranked by
    frequency descending (ties broken alphabetically for a
    deterministic, reproducible ordering).
    """

    counts: Counter[str] = Counter()

    for record in records:
        for reason in record.get("rejection_reasons") or []:
            counts[reason] += 1

    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def extract_pending_signals(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Every SIGNAL_PENDING_APPROVAL record, reduced to
    PENDING_SIGNAL_FIELDS, in the log's own chronological order
    (records are already append-only).
    """

    return [
        {field: record.get(field) for field in PENDING_SIGNAL_FIELDS}
        for record in records
        if record.get("status") == "SIGNAL_PENDING_APPROVAL"
    ]


def compute_strategy_version_summary(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Group evaluations by strategy_version. Each group reports its own
    total evaluation count and its own status-count breakdown, reusing
    compute_status_counts per group -- no separate counting logic.
    Missing/empty strategy_version is grouped under "unknown", mirroring
    the recorder's own fallback value.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        version = record.get("strategy_version") or "unknown"
        grouped[version].append(record)

    return {
        version: {
            "total_evaluations": len(group_records),
            "status_counts": compute_status_counts(group_records),
        }
        for version, group_records in grouped.items()
    }


def compute_hourly_histogram(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """
    Evaluation count per UTC calendar hour, in chronological order.
    Hour buckets are labelled "YYYY-MM-DD HH:00". Records with a
    missing/unparseable timestamp are excluded (not fatal).
    """

    counts: Counter[str] = Counter()

    for record in records:
        timestamp = _parse_timestamp_safe(record.get("timestamp_utc"))

        if timestamp is None:
            continue

        bucket = timestamp.strftime("%Y-%m-%d %H:00")
        counts[bucket] += 1

    return sorted(counts.items())


def _format_duration(total_seconds: float) -> str:
    """Render a duration in seconds as e.g. "1d 4h 12m 3s"."""

    seconds_int = int(total_seconds)
    days, remainder = divmod(seconds_int, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")

    parts.append(f"{seconds}s")

    return " ".join(parts)


def _format_ranked_counts(counts: dict[str, int], total: int) -> list[str]:
    """Format a {label: count} mapping as ranked, percentage-annotated lines."""

    lines = []

    for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        percentage = (count / total) * 100.0 if total else 0.0
        lines.append(f"{label:<28} {count:>5}   ({percentage:5.1f}%)")

    return lines


def generate_report(
    records: list[dict[str, Any]],
    *,
    log_path: Path,
    malformed_line_count: int = 0,
) -> str:
    """
    Build the full console report as a single string. Pure function:
    no I/O, no file access -- operates entirely on already-loaded
    records, so it is fully unit-testable without a log file on disk.
    """

    lines: list[str] = [
        REPORT_DIVIDER,
        "FORWARD-TEST ANALYSIS REPORT",
        REPORT_DIVIDER,
        f"Log file: {log_path}",
    ]

    if malformed_line_count:
        lines.append(
            f"WARNING: {malformed_line_count} malformed line(s) in the "
            "log were skipped."
        )

    if not records:
        lines.append("")
        lines.append("No records found in the log.")
        lines.append(REPORT_DIVIDER)
        return "\n".join(lines)

    total = len(records)
    stats = compute_summary_stats(records)

    lines.append("")
    lines.append(f"Total evaluations:    {stats['total_evaluations']}")
    lines.append(f"First timestamp:      {stats['first_timestamp'] or 'N/A'}")
    lines.append(f"Last timestamp:       {stats['last_timestamp'] or 'N/A'}")
    lines.append(f"Total duration:       {_format_duration(stats['duration_seconds'])}")

    if stats["evaluations_per_hour"] is not None:
        lines.append(f"Evaluations per hour: {stats['evaluations_per_hour']:.2f}")
    else:
        lines.append("Evaluations per hour: N/A (insufficient time span)")

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("STATUS COUNTS")
    lines.append(SECTION_DIVIDER)
    lines.extend(_format_ranked_counts(compute_status_counts(records), total))

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("HTTP STATUS CODE COUNTS")
    lines.append(SECTION_DIVIDER)
    lines.extend(_format_ranked_counts(compute_http_status_counts(records), total))

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("API ERROR COUNTS")
    lines.append(SECTION_DIVIDER)
    lines.extend(_format_ranked_counts(compute_api_error_counts(records), total))

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("REJECTION REASONS (ranked by frequency)")
    lines.append(SECTION_DIVIDER)
    rejection_counts = compute_rejection_reason_counts(records)

    if rejection_counts:
        for reason, count in rejection_counts:
            lines.append(f"{count:>4}x  {reason}")
    else:
        lines.append("(none)")

    pending_signals = extract_pending_signals(records)
    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append(f"SIGNAL_PENDING_APPROVAL EVALUATIONS ({len(pending_signals)})")
    lines.append(SECTION_DIVIDER)

    if pending_signals:
        for signal in pending_signals:
            lines.append(
                f"{signal['timestamp_utc']}  "
                f"{signal['direction']:<4} "
                f"entry={signal['entry']} "
                f"sl={signal['stop_loss']} "
                f"tp={signal['take_profit']} "
                f"confidence={signal['confidence']} "
                f"rr={signal['risk_reward']}"
            )
    else:
        lines.append("(none)")

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("STRATEGY VERSION SUMMARY")
    lines.append(SECTION_DIVIDER)
    version_summary = compute_strategy_version_summary(records)

    for version, summary in sorted(version_summary.items()):
        status_breakdown = ", ".join(
            f"{status}={count}"
            for status, count in sorted(
                summary["status_counts"].items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        lines.append(
            f"{version}  evaluations={summary['total_evaluations']}  "
            f"[{status_breakdown}]"
        )

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("HOURLY EVALUATION HISTOGRAM (UTC)")
    lines.append(SECTION_DIVIDER)
    histogram = compute_hourly_histogram(records)

    if histogram:
        max_count = max(count for _, count in histogram)

        for bucket, count in histogram:
            bar_length = int((count / max_count) * 40) if max_count else 0
            bar = "#" * bar_length
            lines.append(f"{bucket}  {bar:<40} {count}")
    else:
        lines.append("(no timestamped records)")

    lines.append("")
    lines.append(REPORT_DIVIDER)

    return "\n".join(lines)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only analysis report for the forward-test JSON "
            "Lines log produced by scripts/forward_test_recorder.py. "
            "Never modifies the log file."
        ),
    )

    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help=f"JSON Lines log path to analyse (default: {DEFAULT_LOG_PATH}).",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)
    log_path = Path(args.log_file)

    records, malformed_line_count = load_records(log_path)

    report = generate_report(
        records,
        log_path=log_path,
        malformed_line_count=malformed_line_count,
    )

    print(report)


if __name__ == "__main__":
    main()
