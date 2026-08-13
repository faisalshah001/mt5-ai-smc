"""
Forward-test rejection diagnostics for the SMC signal strategy.

Read-only analysis tool for logs/forward_test/smc_signal.jsonl (the
JSON Lines log produced by scripts/forward_test_recorder.py against
POST /api/v2/strategy/smc-signal). This script never opens the log
file for writing or appending -- see load_records, the only function
that touches it, which opens it in "r" mode only -- and it does not
import, call, or modify any strategy, engine, or MT5 code anywhere in
this file. It answers questions about evaluations that have already
happened; it never changes what the strategy does.

Relationship to scripts/forward_test_analyzer.py
--------------------------------------------------
That script already reports status/HTTP-status/API-error counts, raw
rejection-reason frequencies, pending-signal details, a strategy-
version breakdown, and an hourly histogram. This script is scoped to a
different, complementary question it does not answer: WHICH specific
confirmation gate (H1/H4 bias, M15 CHoCH, M15 liquidity sweep, M15
Order Block, M5 local confirmation, ...) is responsible for each
rejection, how that blocking gate has shifted over the life of the
log, and how long each gate has held the strategy back in a row. It
also writes a machine-readable JSON diagnostic file and a per-
rejection CSV, neither of which the existing script produces.

Usage (PowerShell)
-------------------

    python scripts\\analyze_forward_test.py

    python scripts\\analyze_forward_test.py --log-file logs\\forward_test\\smc_signal.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Not imported from scripts/forward_test_analyzer.py, deliberately --
# that script's own docstring explains why: running this file directly
# (`python scripts\analyze_forward_test.py`) only puts the scripts/
# directory itself on sys.path, not the repository root, so a
# `scripts.<module>` import would fail unless the caller has already
# arranged for the repo root to be importable. Duplicating four path
# constants keeps this script runnable the same simple way.
_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LOG_PATH = _REPO_ROOT / "logs" / "forward_test" / "smc_signal.jsonl"
DEFAULT_JSON_OUTPUT_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "forward_test_diagnostics.json"
)
DEFAULT_CSV_OUTPUT_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "forward_test_rejections.csv"
)

REPORT_DIVIDER = "=" * 60
SECTION_DIVIDER = "-" * 60

API_ERROR_STATUS_LABEL = "API_ERROR"
OTHER_CATEGORY = "OTHER"
UNKNOWN_TIMESTAMP_LABEL = "unknown"

CSV_FIELDNAMES = (
    "timestamp",
    "status",
    "direction",
    "entry",
    "stop_loss",
    "raw_rejection_reason",
    "rejection_category",
)

# ---------------------------------------------------------------------------
# Rejection-reason categorisation
# ---------------------------------------------------------------------------
#
# Ordered, keyword-based rules -- deliberately not exact-string
# equality, so a minor wording change to a rejection message (a
# rephrase, a punctuation tweak, a bearish-direction variant of a
# bullish message) still lands in the correct category. A rule fires
# when ALL of its keywords appear (case-insensitive, substring match)
# anywhere in the reason text.
#
# Order matters. Two message variants actually observed in this
# codebase's own forward-test log legitimately contain more than one
# rule's keywords at once:
#   - the Order Block message also contains "CHoCH" and "M15"
#     ("...linked to the CHoCH's displacement was found on M15.")
#   - the Liquidity Sweep message also contains "CHoCH"
#     ("...found on M15 before the confirmed CHoCH.")
# Rules are ordered most-specific-first so the correct, more specific
# category wins before a broader one gets a chance to match.
#
# H1_H4_BIAS_MISMATCH is not one of the four example categories in
# this tool's brief, but it is a real, frequently-occurring, distinct
# upstream gate in the live data (H1 trend vs. H4 bias, checked before
# any M15 structure work happens). Folding it into OTHER would hide a
# significant share of rejections behind a meaningless label, which
# would work against this tool's entire purpose -- so it gets its own
# category instead.
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("M5_LOCAL_CONFIRMATION_MISSING", ("m5", "candles")),
    ("H1_H4_BIAS_MISMATCH", ("h1", "h4", "bias")),
    ("M15_ORDER_BLOCK_MISSING", ("order block",)),
    ("M15_LIQUIDITY_SWEEP_MISSING", ("liquidity sweep", "m15")),
    ("M15_CHOCH_MISSING", ("choch", "m15")),
)


def categorize_reason(reason_text: str) -> str:
    """
    Map a raw rejection-reason string to a normalised category.

    Falls back to OTHER_CATEGORY for any reason text that does not
    match a known pattern, so a future or unrecognised rejection
    message is still counted and reported rather than silently
    dropped or crashing the script.
    """

    lowered = reason_text.lower()

    for category, keywords in CATEGORY_RULES:
        if all(keyword in lowered for keyword in keywords):
            return category

    return OTHER_CATEGORY


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_records(log_path: Path) -> tuple[list[dict[str, Any]], int]:
    """
    Read every record from a JSON Lines forward-test log.

    Read-only: opens the file in "r" mode only. A malformed line
    (invalid JSON -- e.g. a partial write from a process killed
    mid-line) is skipped rather than fatal, so one corrupted line
    never prevents analysis of every other record. A line that parses
    as valid JSON but is not a JSON object (e.g. a bare number, a
    list, `null`) is also skipped and counted as malformed, since
    every genuine record is an object. A missing file returns an
    empty record list and a malformed count of 0, not an error.
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
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                malformed_line_count += 1
                continue

            if not isinstance(parsed, dict):
                malformed_line_count += 1
                continue

            records.append(parsed)

    return records, malformed_line_count


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string; None for anything invalid."""

    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _display_timestamp(
    parsed: Optional[datetime], raw: Any = None
) -> Optional[str]:
    """
    The best available human-readable form of a record's timestamp:
    the parsed value's ISO form when parseable, else the original raw
    string when it is at least a string, else None.
    """

    if parsed is not None:
        return parsed.isoformat()

    if isinstance(raw, str) and raw:
        return raw

    return None


def _status_label(record: dict[str, Any]) -> str:
    """
    The record's status, or API_ERROR_STATUS_LABEL when status is
    missing, null, or empty -- a null status paired with a populated
    api_error means the HTTP call itself failed before the strategy
    ever ran (see scripts/forward_test_recorder.py).
    """

    status = record.get("status")
    return status if isinstance(status, str) and status else API_ERROR_STATUS_LABEL


def _reason_strings(record: dict[str, Any]) -> list[str]:
    """
    Every usable rejection-reason string on a record, defensively.

    Returns an empty list when rejection_reasons is missing, null, or
    not a list. Non-string entries inside an otherwise-valid list are
    skipped individually rather than discarding the whole record.
    """

    reasons = record.get("rejection_reasons")

    if not isinstance(reasons, list):
        return []

    return [reason for reason in reasons if isinstance(reason, str) and reason]


def _optional_number(value: Any) -> Optional[float]:
    """Coerce a record field to float for CSV/JSON use, or None."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    return None


# ---------------------------------------------------------------------------
# Rejection sequence model
# ---------------------------------------------------------------------------


@dataclass
class RejectionEvaluation:
    """
    One (evaluation, rejection reason) pair, in the log's own file
    order. A record with N rejection reasons produces N of these (in
    this codebase's current data N is always 0 or 1, but the schema
    allows more, so every reason is preserved here rather than only
    the first -- this is the basis for the CSV export and the raw,
    per-reason-text counts).
    """

    sequence_index: int
    timestamp_display: Optional[str]
    status: str
    direction: Optional[str]
    entry: Optional[float]
    stop_loss: Optional[float]
    raw_reason: str
    category: str


@dataclass
class PrimaryRejection:
    """
    One evaluation's PRIMARY blocking category -- derived from its
    first rejection reason. This is the basis for gate-transition and
    consecutive-run analysis, so each evaluation contributes exactly
    one category rather than one per reason (matching how the console
    "REJECTION BREAKDOWN" percentages are meant to sum to the total
    evaluation count).
    """

    sequence_index: int
    timestamp_display: Optional[str]
    category: str
    raw_reason: str


def build_rejection_evaluations(
    records: list[dict[str, Any]],
) -> list[RejectionEvaluation]:
    """Expand every record's rejection reasons into RejectionEvaluations."""

    evaluations: list[RejectionEvaluation] = []

    for index, record in enumerate(records):
        reasons = _reason_strings(record)

        if not reasons:
            continue

        timestamp_display = _display_timestamp(
            _parse_timestamp(record.get("timestamp_utc")),
            record.get("timestamp_utc"),
        )
        status = _status_label(record)

        for reason in reasons:
            evaluations.append(
                RejectionEvaluation(
                    sequence_index=index,
                    timestamp_display=timestamp_display,
                    status=status,
                    direction=record.get("direction"),
                    entry=_optional_number(record.get("entry")),
                    stop_loss=_optional_number(record.get("stop_loss")),
                    raw_reason=reason,
                    category=categorize_reason(reason),
                )
            )

    return evaluations


def build_primary_sequence(
    records: list[dict[str, Any]],
) -> list[PrimaryRejection]:
    """
    One PrimaryRejection per record that has at least one rejection
    reason, in file order.

    Records with no rejection reason at all (a successful/pending
    signal, or an API_ERROR where the strategy never ran) are
    deliberately excluded from this sequence. That is what makes
    consecutive-run and gate-transition analysis (both built on top of
    this sequence) treat a connection blip or a non-rejection status
    as a gap that does not interrupt a run, rather than as a
    "different reason" -- the gate did not change; the strategy simply
    was not evaluated that cycle.
    """

    sequence: list[PrimaryRejection] = []

    for index, record in enumerate(records):
        reasons = _reason_strings(record)

        if not reasons:
            continue

        primary_reason = reasons[0]
        timestamp_display = _display_timestamp(
            _parse_timestamp(record.get("timestamp_utc")),
            record.get("timestamp_utc"),
        )

        sequence.append(
            PrimaryRejection(
                sequence_index=index,
                timestamp_display=timestamp_display,
                category=categorize_reason(primary_reason),
                raw_reason=primary_reason,
            )
        )

    return sequence


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def compute_status_counts(records: list[dict[str, Any]]) -> Counter[str]:
    """Count evaluations by status label (API_ERROR for a null status)."""

    counts: Counter[str] = Counter()

    for record in records:
        counts[_status_label(record)] += 1

    return counts


def compute_raw_reason_counts(
    evaluations: list[RejectionEvaluation],
) -> Counter[str]:
    """Count every individual raw rejection-reason string's occurrences."""

    return Counter(evaluation.raw_reason for evaluation in evaluations)


def compute_category_counts(
    primary_sequence: list[PrimaryRejection],
) -> Counter[str]:
    """Count each evaluation's primary blocking category, once each."""

    return Counter(item.category for item in primary_sequence)


def compute_percentages(
    counts: Counter[str], total: int
) -> dict[str, float]:
    """Each count as a percentage of `total`, rounded to 2 decimals."""

    if total <= 0:
        return {key: 0.0 for key in counts}

    return {
        key: round((count / total) * 100.0, 2) for key, count in counts.items()
    }


def compute_first_last_occurrence(
    keyed_timestamps: list[tuple[str, Optional[str]]],
) -> dict[str, dict[str, Optional[str]]]:
    """
    keyed_timestamps: (key, display_timestamp) pairs in file order.

    Returns {key: {"first": ..., "last": ...}}, using file order (the
    order the pairs are supplied in) to determine first/last, falling
    back to UNKNOWN_TIMESTAMP_LABEL when a record's timestamp could
    not be displayed at all.
    """

    first: dict[str, str] = {}
    last: dict[str, str] = {}

    for key, timestamp_display in keyed_timestamps:
        resolved = timestamp_display or UNKNOWN_TIMESTAMP_LABEL

        if key not in first:
            first[key] = resolved

        last[key] = resolved

    return {key: {"first": first[key], "last": last[key]} for key in first}


# ---------------------------------------------------------------------------
# Consecutive runs and gate transitions
# ---------------------------------------------------------------------------


@dataclass
class RejectionRun:
    """One maximal run of consecutive same-category rejections."""

    category: str
    count: int
    start_timestamp: Optional[str]
    end_timestamp: Optional[str]
    start_sequence_index: int
    end_sequence_index: int


def compute_consecutive_runs(
    primary_sequence: list[PrimaryRejection],
) -> list[RejectionRun]:
    """
    Every maximal run of consecutive same-category entries in
    primary_sequence, in chronological order. Because
    build_primary_sequence already excludes non-rejection records,
    an API_ERROR or a pending/approved signal between two same-
    category rejections does not break the run -- see that function's
    docstring for why that is the intended behaviour.
    """

    if not primary_sequence:
        return []

    runs: list[RejectionRun] = []
    current_items = [primary_sequence[0]]

    def _flush(items: list[PrimaryRejection]) -> None:
        first_item = items[0]
        last_item = items[-1]
        runs.append(
            RejectionRun(
                category=first_item.category,
                count=len(items),
                start_timestamp=first_item.timestamp_display,
                end_timestamp=last_item.timestamp_display,
                start_sequence_index=first_item.sequence_index,
                end_sequence_index=last_item.sequence_index,
            )
        )

    for item in primary_sequence[1:]:
        if item.category == current_items[-1].category:
            current_items.append(item)
        else:
            _flush(current_items)
            current_items = [item]

    _flush(current_items)

    return runs


def longest_run_per_category(
    runs: list[RejectionRun],
) -> dict[str, RejectionRun]:
    """The single longest run achieved by each category, across the log."""

    best: dict[str, RejectionRun] = {}

    for run in runs:
        current_best = best.get(run.category)

        if current_best is None or run.count > current_best.count:
            best[run.category] = run

    return best


@dataclass
class GateTransition:
    """A change in the blocking category between two consecutive runs."""

    from_category: str
    to_category: str
    from_last_timestamp: Optional[str]
    to_first_timestamp: Optional[str]


def compute_gate_transitions(runs: list[RejectionRun]) -> list[GateTransition]:
    """
    One GateTransition per boundary between consecutive runs. Since
    each run is already a maximal same-category block, every run
    boundary is, by definition, a change in the blocking gate.
    """

    return [
        GateTransition(
            from_category=previous_run.category,
            to_category=next_run.category,
            from_last_timestamp=previous_run.end_timestamp,
            to_first_timestamp=next_run.start_timestamp,
        )
        for previous_run, next_run in zip(runs, runs[1:])
    ]


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------


def _format_ranked_counts(
    counts: Counter[str], percentages: dict[str, float]
) -> list[str]:
    """Format a {label: count} mapping as ranked, percentage-annotated lines."""

    lines = []

    for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        percentage = percentages.get(label, 0.0)
        lines.append(f"{label:<16} {count:>6}   {percentage:6.2f}%")

    return lines


def generate_console_report(
    *,
    log_path: Path,
    total_evaluations: int,
    malformed_line_count: int,
    status_counts: Counter[str],
    status_percentages: dict[str, float],
    category_counts: Counter[str],
    category_percentages: dict[str, float],
    category_occurrences: dict[str, dict[str, Optional[str]]],
    runs: list[RejectionRun],
    transitions: list[GateTransition],
) -> str:
    """
    Build the full console report as a single string. Pure function:
    no I/O -- operates entirely on already-computed values, so it is
    fully unit-testable without a log file on disk.
    """

    lines: list[str] = [
        REPORT_DIVIDER,
        "FORWARD TEST DIAGNOSTIC REPORT",
        REPORT_DIVIDER,
        "",
        "Log file:",
        str(log_path),
        "",
        f"Total evaluations: {total_evaluations}",
    ]

    if malformed_line_count:
        lines.append(
            f"Malformed lines skipped: {malformed_line_count}"
        )

    if total_evaluations == 0:
        lines.append("")
        lines.append("No records found in the log.")
        lines.append(REPORT_DIVIDER)
        return "\n".join(lines)

    lines.append("")
    lines.append("STATUS SUMMARY")
    lines.append(SECTION_DIVIDER)
    lines.extend(_format_ranked_counts(status_counts, status_percentages))

    lines.append("")
    lines.append("REJECTION BREAKDOWN")
    lines.append(SECTION_DIVIDER)

    if category_counts:
        for category, count in sorted(
            category_counts.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            percentage = category_percentages.get(category, 0.0)
            occurrence = category_occurrences.get(category, {})
            lines.append(category)
            lines.append(f"Count: {count}")
            lines.append(f"Percentage: {percentage:.2f}%")
            lines.append(
                f"First occurrence: {occurrence.get('first', UNKNOWN_TIMESTAMP_LABEL)}"
            )
            lines.append(
                f"Last occurrence: {occurrence.get('last', UNKNOWN_TIMESTAMP_LABEL)}"
            )
            lines.append("")
    else:
        lines.append("(no rejections recorded)")
        lines.append("")

    lines.append(SECTION_DIVIDER)
    lines.append("REJECTION GATE TRANSITIONS")
    lines.append(SECTION_DIVIDER)

    if runs:
        # ASCII-only by design (no "->"/arrow Unicode glyphs): the
        # default Windows console codepage (commonly cp1252) cannot
        # encode most arrow characters, and this script must run in a
        # plain `python scripts\analyze_forward_test.py` PowerShell
        # session without requiring the operator to reconfigure their
        # console's codepage first.
        chain_categories = [run.category for run in runs]
        lines.append(f"\n{' '*4}v\n".join(chain_categories))
        lines.append("")

        if transitions:
            lines.append("Transition timestamps:")
            for transition in transitions:
                lines.append(
                    f"  {transition.from_category} "
                    f"(last: {transition.from_last_timestamp or UNKNOWN_TIMESTAMP_LABEL}) "
                    f"-> {transition.to_category} "
                    f"(first: {transition.to_first_timestamp or UNKNOWN_TIMESTAMP_LABEL})"
                )
        else:
            lines.append(
                "Only one blocking gate observed in this log -- no transitions."
            )
    else:
        lines.append("(no rejections recorded)")

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("LONGEST REJECTION RUNS")
    lines.append(SECTION_DIVIDER)

    longest = longest_run_per_category(runs)

    if longest:
        for run in sorted(longest.values(), key=lambda r: (-r.count, r.category)):
            lines.append(run.category)
            lines.append(f"Start: {run.start_timestamp or UNKNOWN_TIMESTAMP_LABEL}")
            lines.append(f"End: {run.end_timestamp or UNKNOWN_TIMESTAMP_LABEL}")
            lines.append(f"Evaluations: {run.count}")
            lines.append("")
    else:
        lines.append("(no rejections recorded)")
        lines.append("")

    lines.append(SECTION_DIVIDER)
    lines.append("CURRENT STREAK (most recent run)")
    lines.append(SECTION_DIVIDER)

    if runs:
        current_run = runs[-1]
        lines.append(
            f"{current_run.category} has been the blocking reason for the "
            f"last {current_run.count} consecutive evaluation(s), since "
            f"{current_run.start_timestamp or UNKNOWN_TIMESTAMP_LABEL}."
        )
    else:
        lines.append("(no rejections recorded)")

    lines.append("")
    lines.append(REPORT_DIVIDER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def build_json_diagnostics(
    *,
    log_path: Path,
    total_evaluations: int,
    malformed_line_count: int,
    status_counts: Counter[str],
    category_counts: Counter[str],
    category_percentages: dict[str, float],
    category_occurrences: dict[str, dict[str, Optional[str]]],
    raw_reason_counts: Counter[str],
    raw_reason_percentages: dict[str, float],
    raw_reason_occurrences: dict[str, dict[str, Optional[str]]],
    runs: list[RejectionRun],
    transitions: list[GateTransition],
) -> dict[str, Any]:
    """
    Assemble the machine-readable diagnostics payload. Includes every
    field the tool's brief requires, plus the additional raw-reason-
    level and current-streak detail the brief explicitly permits.
    """

    longest = longest_run_per_category(runs)
    current_run = runs[-1] if runs else None

    return {
        "log_file": str(log_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "malformed_line_count": malformed_line_count,
        "total_evaluations": total_evaluations,
        "status_counts": dict(status_counts),
        "rejection_counts": dict(category_counts),
        "rejection_percentages": category_percentages,
        "first_occurrence": {
            category: occurrence["first"]
            for category, occurrence in category_occurrences.items()
        },
        "last_occurrence": {
            category: occurrence["last"]
            for category, occurrence in category_occurrences.items()
        },
        "longest_runs": {
            category: {
                "count": run.count,
                "start_timestamp": run.start_timestamp,
                "end_timestamp": run.end_timestamp,
            }
            for category, run in longest.items()
        },
        "gate_transitions": [
            {
                "from_category": transition.from_category,
                "to_category": transition.to_category,
                "from_last_timestamp": transition.from_last_timestamp,
                "to_first_timestamp": transition.to_first_timestamp,
            }
            for transition in transitions
        ],
        "current_streak": (
            {
                "category": current_run.category,
                "count": current_run.count,
                "since_timestamp": current_run.start_timestamp,
            }
            if current_run is not None
            else None
        ),
        "raw_reason_counts": dict(raw_reason_counts),
        "raw_reason_percentages": raw_reason_percentages,
        "raw_reason_first_occurrence": {
            reason: occurrence["first"]
            for reason, occurrence in raw_reason_occurrences.items()
        },
        "raw_reason_last_occurrence": {
            reason: occurrence["last"]
            for reason, occurrence in raw_reason_occurrences.items()
        },
        "raw_reason_category_map": {
            reason: categorize_reason(reason) for reason in raw_reason_counts
        },
    }


def write_json_diagnostics(payload: dict[str, Any], output_path: Path) -> None:
    """Write the diagnostics payload as pretty-printed JSON, creating dirs."""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------


def write_rejections_csv(
    evaluations: list[RejectionEvaluation], output_path: Path
) -> None:
    """
    Write one CSV row per (evaluation, rejection reason) pair, in
    file/chronological order, creating the output directory if
    needed. An empty evaluations list still produces a header-only
    CSV rather than no file at all.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()

        for evaluation in evaluations:
            writer.writerow(
                {
                    "timestamp": evaluation.timestamp_display or "",
                    "status": evaluation.status,
                    "direction": evaluation.direction or "",
                    "entry": (
                        "" if evaluation.entry is None else evaluation.entry
                    ),
                    "stop_loss": (
                        ""
                        if evaluation.stop_loss is None
                        else evaluation.stop_loss
                    ),
                    "raw_rejection_reason": evaluation.raw_reason,
                    "rejection_category": evaluation.category,
                }
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic report for the SMC forward-test rejection log. "
            "Read-only: never modifies the log, the strategy, or any "
            "trading logic."
        ),
    )

    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help=f"JSON Lines log path to analyse (default: {DEFAULT_LOG_PATH}).",
    )

    parser.add_argument(
        "--json-output",
        default=str(DEFAULT_JSON_OUTPUT_PATH),
        help=(
            "Path to write the JSON diagnostics report "
            f"(default: {DEFAULT_JSON_OUTPUT_PATH})."
        ),
    )

    parser.add_argument(
        "--csv-output",
        default=str(DEFAULT_CSV_OUTPUT_PATH),
        help=(
            "Path to write the per-rejection CSV "
            f"(default: {DEFAULT_CSV_OUTPUT_PATH})."
        ),
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    # Defensive: the console report is ASCII-only by construction (see
    # generate_console_report), but a rejection-reason string read
    # from the log is arbitrary external data, not something this
    # script controls. Reconfiguring stdout to replace, rather than
    # crash on, an unencodable character keeps this script robust on
    # a default Windows console codepage even if that ever changes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    args = _parse_args(argv)

    log_path = Path(args.log_file)
    json_output_path = Path(args.json_output)
    csv_output_path = Path(args.csv_output)

    records, malformed_line_count = load_records(log_path)
    total_evaluations = len(records)

    evaluations = build_rejection_evaluations(records)
    primary_sequence = build_primary_sequence(records)

    status_counts = compute_status_counts(records)
    status_percentages = compute_percentages(status_counts, total_evaluations)

    category_counts = compute_category_counts(primary_sequence)
    category_percentages = compute_percentages(category_counts, total_evaluations)
    category_occurrences = compute_first_last_occurrence(
        [(item.category, item.timestamp_display) for item in primary_sequence]
    )

    raw_reason_counts = compute_raw_reason_counts(evaluations)
    raw_reason_percentages = compute_percentages(
        raw_reason_counts, total_evaluations
    )
    raw_reason_occurrences = compute_first_last_occurrence(
        [
            (evaluation.raw_reason, evaluation.timestamp_display)
            for evaluation in evaluations
        ]
    )

    runs = compute_consecutive_runs(primary_sequence)
    transitions = compute_gate_transitions(runs)

    console_report = generate_console_report(
        log_path=log_path,
        total_evaluations=total_evaluations,
        malformed_line_count=malformed_line_count,
        status_counts=status_counts,
        status_percentages=status_percentages,
        category_counts=category_counts,
        category_percentages=category_percentages,
        category_occurrences=category_occurrences,
        runs=runs,
        transitions=transitions,
    )

    print(console_report)

    json_payload = build_json_diagnostics(
        log_path=log_path,
        total_evaluations=total_evaluations,
        malformed_line_count=malformed_line_count,
        status_counts=status_counts,
        category_counts=category_counts,
        category_percentages=category_percentages,
        category_occurrences=category_occurrences,
        raw_reason_counts=raw_reason_counts,
        raw_reason_percentages=raw_reason_percentages,
        raw_reason_occurrences=raw_reason_occurrences,
        runs=runs,
        transitions=transitions,
    )
    write_json_diagnostics(json_payload, json_output_path)

    write_rejections_csv(evaluations, csv_output_path)

    print(f"\nJSON diagnostics written to: {json_output_path}")
    print(f"CSV rejections written to:   {csv_output_path}")


if __name__ == "__main__":
    main()
