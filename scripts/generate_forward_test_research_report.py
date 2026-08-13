"""
Master forward-test research report for the SMC signal strategy.

Read-only, orchestration-only. Answers one question:

    "Is the strategy correctly filtering low-quality opportunities, or
    is one particular gate consistently rejecting moves that later
    become profitable?"

This script does not answer that question itself -- it produces the
evidence. It never places trades, never modifies MT5 positions, never
modifies strategy settings, never calls execution endpoints, and never
modifies logs/forward_test/smc_signal.jsonl (opened read-only) or any
other historical log.

Reuse strategy
---------------
Per the task brief ("prefer importing/reusing existing functions...
do not duplicate large amounts of existing logic unnecessarily...
do not modify those existing scripts unless absolutely required"),
this script imports its entire computational core from
scripts/analyze_rejection_outcomes.py: log loading, exact-duplicate
handling, rejection categorisation, evidence-only direction
extraction, pip-size detection, per-window MFE/MAE/net-movement
outcome measurement, the R-multiple walk-forward, and -- critically --
the ONE episode-grouping definition (category + direction + a
15-minute continuity gap), reused verbatim rather than re-invented.
scripts/analyze_forward_test.py contributes its generic, status-
agnostic compute_status_counts().

Neither existing script is modified. The one genuine interface gap
(scripts.analyze_rejection_outcomes.fetch_price_history() hard-codes
its own module-level SYMBOL constant rather than accepting one as a
parameter, so it cannot serve a --symbol override) is bridged by a
small, local, symbol-parameterised fetch function in this file
(fetch_price_history_for_symbol) that otherwise mirrors it exactly --
this is judged a small, necessary, and clearly-documented exception,
not the "large amounts of existing logic" the brief says to avoid
duplicating.

Everything genuinely new in this file (status/setup/day-of-week/
time-of-day/direction cross-tabulation, the gate-transition PAIR
frequency table, multi-window/multi-threshold MFE/MAE statistics,
protective-filter and missed-opportunity labelling, the baseline
mechanism, and the actual-setup analysis) is built entirely on top of
the reused Episode/EvaluationOutcome objects -- no second episode
definition, no duplicate price fetching, no duplicate MT5 round trips.

Look-ahead discipline (inherited from the reused functions, and
preserved here): every category and direction is derived only from a
record's own logged evidence at its own timestamp. Future candles are
used exclusively to MEASURE outcomes after that timestamp; they are
never fed back into reconstructing rejection-time strategy state.

Usage (PowerShell):

    python scripts\\generate_forward_test_research_report.py

    python scripts\\generate_forward_test_research_report.py --save-baseline

    python scripts\\generate_forward_test_research_report.py --help
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from app.mt5.connection import connect_mt5, disconnect_mt5  # noqa: E402
from app.mt5.market import get_candles  # noqa: E402

from scripts.analyze_forward_test import (  # noqa: E402
    compute_status_counts as _raw_status_counts,
)
from scripts.analyze_rejection_outcomes import (  # noqa: E402
    API_ERROR_STATUS_LABEL,
    CATEGORY_DEPTH,
    OUTCOME_WINDOWS_MINUTES,
    R_MULTIPLES,
    Episode,
    EvaluationOutcome,
    WindowOutcome,
    _mean,
    _median,
    _parse_timestamp,
    _status_label,
    build_episodes,
    build_evaluation_outcomes,
    compute_window_outcome,
    deduplicate_exact_records,
    determine_pip_size,
    find_reference_index,
    load_records,
)
from scripts.analyze_rejection_outcomes import SYMBOL as DEFAULT_SYMBOL  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_LOG_PATH = _REPO_ROOT / "logs" / "forward_test" / "smc_signal.jsonl"
DEFAULT_JSON_OUTPUT_PATH = _REPO_ROOT / "logs" / "forward_test" / "research_report.json"
DEFAULT_CSV_OUTPUT_PATH = _REPO_ROOT / "logs" / "forward_test" / "research_report.csv"
DEFAULT_TXT_OUTPUT_PATH = _REPO_ROOT / "logs" / "forward_test" / "research_report.txt"
DEFAULT_HOUR_CSV_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "research_report_by_hour.csv"
)
DEFAULT_WEEKDAY_CSV_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "research_report_by_weekday.csv"
)
DEFAULT_BASELINE_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "research_baseline.json"
)

REPORT_DIVIDER = "=" * 72
SECTION_DIVIDER = "-" * 72

M5_MINUTES = 5
UNKNOWN_TIMESTAMP_LABEL = "unknown"

# Confirmed possible statuses (main.py / app/strategies/smc_manual_signal.py
# docstrings and return schema): "SIGNAL_PENDING_APPROVAL", "NO_SETUP",
# "BLOCKED". A null status (API_ERROR_STATUS_LABEL) means the HTTP call
# itself failed. Anything else observed in the log is reported as an
# "unknown status" rather than silently folded into one of these.
REAL_SETUP_STATUS = "SIGNAL_PENDING_APPROVAL"
BLOCKED_STATUS = "BLOCKED"
NO_SETUP_STATUS = "NO_SETUP"
KNOWN_STATUSES = {NO_SETUP_STATUS, BLOCKED_STATUS, REAL_SETUP_STATUS, API_ERROR_STATUS_LABEL}

FAVORABLE_PIP_THRESHOLDS: tuple[int, ...] = (5, 10, 15, 20, 30)
ADVERSE_PIP_THRESHOLDS: tuple[int, ...] = (5, 10, 15, 20)

# Descriptive only -- explicitly NOT a claim of statistical significance
# (the task brief requires this framing verbatim).
SAMPLE_SIZE_BOUNDARIES: tuple[tuple[int, str], ...] = (
    (10, "VERY SMALL SAMPLE"),
    (30, "SMALL SAMPLE"),
    (100, "DEVELOPING SAMPLE"),
)
SAMPLE_SIZE_LARGE_LABEL = "LARGER OBSERVATIONAL SAMPLE"

# Fixed-UTC, DST-naive session buckets -- deliberately NOT adjusted for
# any session's local daylight-saving changes (documented explicitly,
# per the task brief's warning against silent DST assumptions). A
# mutually exclusive partition of the full 24-hour UTC day, so every
# episode's start hour falls in exactly one bucket -- no double
# counting. Boundaries are the commonly-cited approximate ranges for
# each region's main trading hours; treat as a descriptive grouping
# only, not a precise market-open/close definition.
SESSION_BUCKETS_UTC: tuple[tuple[str, int, int], ...] = (
    ("Asia (00:00-08:00 UTC)", 0, 8),
    ("London (08:00-13:00 UTC)", 8, 13),
    ("Overlap London/New York (13:00-16:00 UTC)", 13, 16),
    ("New York (16:00-21:00 UTC)", 16, 21),
    ("Off-session (21:00-24:00 UTC)", 21, 24),
)

WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def sample_size_label(count: int) -> str:
    """
    Descriptive-only sample-size label. Must never be read as a claim
    of statistical significance -- see SAMPLE_SIZE_BOUNDARIES.
    """

    for boundary, label in SAMPLE_SIZE_BOUNDARIES:
        if count < boundary:
            return label

    return SAMPLE_SIZE_LARGE_LABEL


# ---------------------------------------------------------------------------
# Price history -- small, symbol-parameterised local fetch.
#
# scripts.analyze_rejection_outcomes.fetch_price_history() hard-codes its
# own module-level SYMBOL constant rather than accepting one as a
# parameter, so it cannot serve a --symbol CLI override. Rather than
# modify that already-tested, reused function (or monkeypatch its
# module global, which would be fragile), this mirrors its logic in a
# small, symbol-parameterised local function. Still uses the real,
# unmodified app.mt5.market.get_candles() -- the already-fixed
# broker-UTC timestamp correction is inherited exactly the same way.
# ---------------------------------------------------------------------------


def fetch_price_history_for_symbol(
    symbol: str, earliest_needed: datetime
) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    span = now - earliest_needed
    candles_needed = int(span.total_seconds() // (M5_MINUTES * 60)) + 200

    frame = get_candles(symbol, "M5", max(candles_needed, 500))

    return frame.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Section 1: Dataset overview
# ---------------------------------------------------------------------------


def build_dataset_overview(
    *,
    log_path: Path,
    raw_records: list[dict[str, Any]],
    records: list[dict[str, Any]],
    malformed_line_count: int,
    duplicate_count: int,
    evaluations: list[EvaluationOutcome],
    episodes: list[Episode],
) -> dict[str, Any]:
    all_timestamps: list[datetime] = [
        ts for record in records if (ts := _parse_timestamp(record.get("timestamp_utc"))) is not None
    ]

    status_counts = _raw_status_counts(records)

    no_setup_count = status_counts.get(NO_SETUP_STATUS, 0)
    setup_count = status_counts.get(REAL_SETUP_STATUS, 0)
    api_error_count = status_counts.get(API_ERROR_STATUS_LABEL, 0)
    unknown_status_count = sum(
        count for status, count in status_counts.items() if status not in KNOWN_STATUSES
    )

    buy_count = 0
    sell_count = 0
    for record in records:
        if _status_label(record) == REAL_SETUP_STATUS:
            direction = record.get("direction")
            if direction == "BUY":
                buy_count += 1
            elif direction == "SELL":
                sell_count += 1

    weekday_flags = {ts.weekday() for ts in all_timestamps}
    includes_weekday = any(day < 5 for day in weekday_flags)
    includes_weekend = any(day >= 5 for day in weekday_flags)

    sorted_ts = sorted(all_timestamps)
    closure_gaps: list[dict[str, Any]] = []
    for previous, current in zip(sorted_ts, sorted_ts[1:]):
        gap_hours = (current - previous).total_seconds() / 3600.0
        if gap_hours >= 4.0:
            closure_gaps.append(
                {
                    "gap_start": previous.isoformat(),
                    "gap_end": current.isoformat(),
                    "gap_hours": round(gap_hours, 2),
                }
            )

    return {
        "report_generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "log_file": str(log_path),
        "first_evaluation_timestamp": sorted_ts[0].isoformat() if sorted_ts else None,
        "last_evaluation_timestamp": sorted_ts[-1].isoformat() if sorted_ts else None,
        "calendar_duration_days": (
            round((sorted_ts[-1] - sorted_ts[0]).total_seconds() / 86400.0, 2)
            if len(sorted_ts) >= 2
            else 0.0
        ),
        "total_lines_read": len(raw_records) + malformed_line_count,
        "malformed_rows": malformed_line_count,
        "duplicate_records_skipped": duplicate_count,
        "valid_evaluations": len(records),
        "api_errors": api_error_count,
        "no_setup_count": no_setup_count,
        "setup_ready_count": setup_count,
        "buy_signals": buy_count,
        "sell_signals": sell_count,
        "unknown_status_count": unknown_status_count,
        "directional_evaluations": sum(1 for e in evaluations if not e.direction_unknown),
        "unknown_direction_evaluations": sum(1 for e in evaluations if e.direction_unknown),
        "deduplicated_rejection_episodes": len(episodes),
        "sample_includes_weekdays": includes_weekday,
        "sample_includes_weekends": includes_weekend,
        "market_closure_gaps_ge_4h": closure_gaps,
    }


# ---------------------------------------------------------------------------
# Section 2: Status distribution
# ---------------------------------------------------------------------------


def build_status_distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _raw_status_counts(records)
    total = len(records)

    return {
        "total": total,
        "by_status": {
            status: {
                "count": count,
                "percentage": round(100.0 * count / total, 2) if total else 0.0,
            }
            for status, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        },
    }


# ---------------------------------------------------------------------------
# Section 3: Rejection gate distribution (raw + episode-based)
# ---------------------------------------------------------------------------


def build_rejection_gate_distribution(
    evaluations: list[EvaluationOutcome], episodes: list[Episode]
) -> dict[str, Any]:
    total_evaluations = len(evaluations)
    total_episodes = len(episodes)

    raw_counts: Counter[str] = Counter(e.category for e in evaluations)
    episode_counts: Counter[str] = Counter(ep.category for ep in episodes)

    by_category: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        by_category[episode.category].append(episode)

    last_category = episodes[-1].category if episodes else None

    result: dict[str, Any] = {}

    all_categories = set(raw_counts) | set(episode_counts)

    for category in sorted(all_categories):
        category_episodes = by_category.get(category, [])
        first_episode = category_episodes[0] if category_episodes else None
        last_episode = category_episodes[-1] if category_episodes else None
        longest_episode = (
            max(category_episodes, key=lambda ep: ep.evaluation_count)
            if category_episodes
            else None
        )
        is_current = last_category == category

        result[category] = {
            "raw_evaluation_count": raw_counts.get(category, 0),
            "raw_evaluation_percentage": (
                round(100.0 * raw_counts.get(category, 0) / total_evaluations, 2)
                if total_evaluations
                else 0.0
            ),
            "episode_count": episode_counts.get(category, 0),
            "episode_percentage": (
                round(100.0 * episode_counts.get(category, 0) / total_episodes, 2)
                if total_episodes
                else 0.0
            ),
            "first_occurrence": first_episode.start_timestamp if first_episode else None,
            "last_occurrence": last_episode.end_timestamp if last_episode else None,
            "longest_run_evaluations": (
                longest_episode.evaluation_count if longest_episode else 0
            ),
            "longest_run_start": longest_episode.start_timestamp if longest_episode else None,
            "longest_run_end": longest_episode.end_timestamp if longest_episode else None,
            "currently_active_run": (
                episodes[-1].evaluation_count if is_current else 0
            ),
            "currently_active_since": (episodes[-1].start_timestamp if is_current else None),
            "sample_size_label": sample_size_label(episode_counts.get(category, 0)),
        }

    return result


# ---------------------------------------------------------------------------
# Section 4: Gate transitions -- chronological chain AND pair frequency
# ---------------------------------------------------------------------------


def build_gate_transitions(episodes: list[Episode]) -> dict[str, Any]:
    chain = [ep.category for ep in episodes]

    pair_counts: Counter[tuple[str, str]] = Counter()
    for previous_episode, next_episode in zip(episodes, episodes[1:]):
        pair_counts[(previous_episode.category, next_episode.category)] += 1

    pairs = [
        {
            "from_category": from_category,
            "to_category": to_category,
            "occurrences": count,
        }
        for (from_category, to_category), count in sorted(
            pair_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]

    return {
        "chronological_episode_chain": chain,
        "transition_pair_counts": pairs,
    }


# ---------------------------------------------------------------------------
# Section 5/6: Multi-window MFE/MAE/net-movement statistics
# ---------------------------------------------------------------------------


def _signed_expected_pips(window: WindowOutcome) -> Optional[float]:
    """
    Net movement relative to the expected direction (positive =
    favourable, negative = adverse), recovered from the two
    already-computed non-negative fields on WindowOutcome (exactly one
    of which is nonzero) rather than re-deriving it from scratch.
    """

    if (
        window.movement_in_expected_direction_pips is None
        or window.movement_against_expected_direction_pips is None
    ):
        return None

    return (
        window.movement_in_expected_direction_pips
        - window.movement_against_expected_direction_pips
    )


@dataclass
class WindowStats:
    window_minutes: int
    episode_count: int
    usable_episode_count: int
    mean_mfe_pips: Optional[float]
    median_mfe_pips: Optional[float]
    mean_mae_pips: Optional[float]
    median_mae_pips: Optional[float]
    mean_net_directional_pips: Optional[float]
    median_net_directional_pips: Optional[float]
    pct_favorable_at_least: dict[int, Optional[float]]
    pct_adverse_at_least: dict[int, Optional[float]]
    mfe_mae_ratio_mean: Optional[float]
    mfe_mae_ratio_median: Optional[float]


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """
    numerator / denominator, safely: None if either input is missing,
    or a labelled sentinel- free None if denominator is zero (never
    raises ZeroDivisionError, never fabricates an infinite/NaN value
    into JSON/CSV output).
    """

    if numerator is None or denominator is None or denominator == 0:
        return None

    return round(numerator / denominator, 3)


def compute_window_stats_by_category(
    episodes: list[Episode],
) -> dict[str, dict[int, WindowStats]]:
    by_category: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        by_category[episode.category].append(episode)

    result: dict[str, dict[int, WindowStats]] = {}

    for category, category_episodes in by_category.items():
        directional = [e for e in category_episodes if e.direction is not None]
        result[category] = {}

        for window_minutes in OUTCOME_WINDOWS_MINUTES:
            usable = [
                e
                for e in directional
                if window_minutes in e.windows and not e.windows[window_minutes].insufficient_data
            ]

            mfe_values = [
                e.windows[window_minutes].mfe_pips
                for e in usable
                if e.windows[window_minutes].mfe_pips is not None
            ]
            mae_values = [
                e.windows[window_minutes].mae_pips
                for e in usable
                if e.windows[window_minutes].mae_pips is not None
            ]
            net_values = [
                signed
                for e in usable
                if (signed := _signed_expected_pips(e.windows[window_minutes])) is not None
            ]

            mean_mfe = _mean(mfe_values)
            median_mfe = _median(mfe_values)
            mean_mae = _mean(mae_values)
            median_mae = _median(mae_values)

            pct_favorable = {
                threshold: (
                    round(100.0 * sum(1 for v in mfe_values if v >= threshold) / len(mfe_values), 2)
                    if mfe_values
                    else None
                )
                for threshold in FAVORABLE_PIP_THRESHOLDS
            }
            pct_adverse = {
                threshold: (
                    round(100.0 * sum(1 for v in mae_values if v >= threshold) / len(mae_values), 2)
                    if mae_values
                    else None
                )
                for threshold in ADVERSE_PIP_THRESHOLDS
            }

            result[category][window_minutes] = WindowStats(
                window_minutes=window_minutes,
                episode_count=len(category_episodes),
                usable_episode_count=len(usable),
                mean_mfe_pips=round(mean_mfe, 2) if mean_mfe is not None else None,
                median_mfe_pips=round(median_mfe, 2) if median_mfe is not None else None,
                mean_mae_pips=round(mean_mae, 2) if mean_mae is not None else None,
                median_mae_pips=round(median_mae, 2) if median_mae is not None else None,
                mean_net_directional_pips=(
                    round(_mean(net_values), 2) if net_values else None
                ),
                median_net_directional_pips=(
                    round(_median(net_values), 2) if net_values else None
                ),
                pct_favorable_at_least=pct_favorable,
                pct_adverse_at_least=pct_adverse,
                mfe_mae_ratio_mean=_safe_ratio(mean_mfe, mean_mae),
                mfe_mae_ratio_median=_safe_ratio(median_mfe, median_mae),
            )

    return result


# ---------------------------------------------------------------------------
# Section 7/8: Filter-protection and missed-opportunity candidates
#
# Diagnostic labelling only -- never a claim that a filter IS good or
# bad. Ranked at the 60-minute window (this project's existing
# convention for a single representative window; all 5 windows remain
# available in the JSON output for closer inspection).
# ---------------------------------------------------------------------------


def build_protection_and_opportunity_analysis(
    window_stats: dict[str, dict[int, WindowStats]],
) -> dict[str, Any]:
    reference_window = 60

    rows = []
    for category, per_window in window_stats.items():
        stats = per_window.get(reference_window)
        if stats is None or stats.usable_episode_count == 0:
            continue
        rows.append((category, stats))

    protective_candidates = [
        {
            "category": category,
            "sample_size": stats.usable_episode_count,
            "sample_size_label": sample_size_label(stats.usable_episode_count),
            "mean_mfe_pips_60m": stats.mean_mfe_pips,
            "mean_mae_pips_60m": stats.mean_mae_pips,
            "mfe_mae_ratio_60m": stats.mfe_mae_ratio_mean,
            "label": "Evidence consistent with protective filtering",
        }
        for category, stats in sorted(
            rows,
            key=lambda item: (
                item[1].mfe_mae_ratio_mean
                if item[1].mfe_mae_ratio_mean is not None
                else float("inf")
            ),
        )
        if stats.mfe_mae_ratio_mean is not None and stats.mfe_mae_ratio_mean < 1.0
    ]

    opportunity_candidates = [
        {
            "category": category,
            "sample_size": stats.usable_episode_count,
            "sample_size_label": sample_size_label(stats.usable_episode_count),
            "mean_mfe_pips_60m": stats.mean_mfe_pips,
            "mean_mae_pips_60m": stats.mean_mae_pips,
            "mfe_mae_ratio_60m": stats.mfe_mae_ratio_mean,
            "pct_ge_10_pips_favorable_60m": stats.pct_favorable_at_least.get(10),
            "pct_ge_20_pips_favorable_60m": stats.pct_favorable_at_least.get(20),
            "label": "Candidates for future review",
        }
        for category, stats in sorted(
            rows,
            key=lambda item: -(item[1].mfe_mae_ratio_mean or 0.0),
        )
        if (
            stats.mfe_mae_ratio_mean is not None
            and stats.mfe_mae_ratio_mean > 1.0
            and stats.mean_mfe_pips is not None
            and stats.mean_mfe_pips > 0
        )
    ]

    return {
        "reference_window_minutes": reference_window,
        "protective_filter_candidates": protective_candidates,
        "future_review_candidates": opportunity_candidates,
    }


# ---------------------------------------------------------------------------
# Section 9: Deep-gate analysis
# ---------------------------------------------------------------------------


def build_deep_gate_analysis(window_stats: dict[str, dict[int, WindowStats]]) -> dict[str, Any]:
    reference_window = 60

    ordered = sorted(
        (
            (category, per_window.get(reference_window))
            for category, per_window in window_stats.items()
        ),
        key=lambda item: CATEGORY_DEPTH.get(item[0], -1),
    )

    rows = [
        {
            "category": category,
            "depth": CATEGORY_DEPTH.get(category, -1),
            "sample_size": stats.usable_episode_count if stats else 0,
            "mean_mfe_pips_60m": stats.mean_mfe_pips if stats else None,
            "mean_mae_pips_60m": stats.mean_mae_pips if stats else None,
            "mfe_mae_ratio_60m": stats.mfe_mae_ratio_mean if stats else None,
        }
        for category, stats in ordered
        if stats is not None and stats.usable_episode_count > 0
    ]

    mfe_sequence = [row["mean_mfe_pips_60m"] for row in rows if row["mean_mfe_pips_60m"] is not None]
    mae_sequence = [row["mean_mae_pips_60m"] for row in rows if row["mean_mae_pips_60m"] is not None]

    mfe_monotonic_increasing = (
        all(b >= a for a, b in zip(mfe_sequence, mfe_sequence[1:])) if len(mfe_sequence) > 1 else None
    )
    mae_monotonic_decreasing = (
        all(b <= a for a, b in zip(mae_sequence, mae_sequence[1:])) if len(mae_sequence) > 1 else None
    )

    return {
        "reference_window_minutes": reference_window,
        "by_depth": rows,
        "mfe_appears_monotonic_with_depth": mfe_monotonic_increasing,
        "mae_appears_monotonic_with_depth": mae_monotonic_decreasing,
        "note": (
            "Monotonicity flags describe this sample only and are not "
            "assumed to hold in general; a small sample can easily "
            "appear monotonic or non-monotonic by chance."
        ),
    }


# ---------------------------------------------------------------------------
# Section 10: Time-of-day analysis
# ---------------------------------------------------------------------------


def _session_for_hour(hour: int) -> str:
    for name, start, end in SESSION_BUCKETS_UTC:
        if start <= hour < end:
            return name

    return "Unclassified"  # unreachable given SESSION_BUCKETS_UTC covers 0-24


def build_time_of_day_analysis(episodes: list[Episode]) -> dict[str, Any]:
    by_hour: dict[int, list[Episode]] = defaultdict(list)

    for episode in episodes:
        start = _parse_timestamp(episode.start_timestamp)
        if start is None:
            continue
        by_hour[start.hour].append(episode)

    hour_rows = []
    for hour in sorted(by_hour):
        hour_episodes = by_hour[hour]
        directional = [e for e in hour_episodes if e.direction is not None]
        mfe_values = [
            e.windows[60].mfe_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mfe_pips is not None
        ]
        mae_values = [
            e.windows[60].mae_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mae_pips is not None
        ]
        categories = Counter(e.category for e in hour_episodes)

        hour_rows.append(
            {
                "utc_hour": hour,
                "session": _session_for_hour(hour),
                "episode_count": len(hour_episodes),
                "sample_size_label": sample_size_label(len(hour_episodes)),
                "categories": dict(categories),
                "mean_mfe_pips_60m": round(_mean(mfe_values), 2) if mfe_values else None,
                "mean_mae_pips_60m": round(_mean(mae_values), 2) if mae_values else None,
            }
        )

    session_totals: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        start = _parse_timestamp(episode.start_timestamp)
        if start is None:
            continue
        session_totals[_session_for_hour(start.hour)].append(episode)

    session_rows = []
    for name, _, _ in SESSION_BUCKETS_UTC:
        session_episodes = session_totals.get(name, [])
        directional = [e for e in session_episodes if e.direction is not None]
        mfe_values = [
            e.windows[60].mfe_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mfe_pips is not None
        ]
        mae_values = [
            e.windows[60].mae_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mae_pips is not None
        ]

        session_rows.append(
            {
                "session": name,
                "episode_count": len(session_episodes),
                "sample_size_label": sample_size_label(len(session_episodes)),
                "mean_mfe_pips_60m": round(_mean(mfe_values), 2) if mfe_values else None,
                "mean_mae_pips_60m": round(_mean(mae_values), 2) if mae_values else None,
            }
        )

    return {
        "session_boundaries_utc": [
            {"session": name, "start_hour_utc": start, "end_hour_utc": end}
            for name, start, end in SESSION_BUCKETS_UTC
        ],
        "session_boundary_note": (
            "Fixed UTC hour ranges, NOT adjusted for any session's local "
            "daylight-saving changes -- descriptive grouping only."
        ),
        "by_utc_hour": hour_rows,
        "by_session": session_rows,
    }


# ---------------------------------------------------------------------------
# Section 11: Day-of-week analysis
# ---------------------------------------------------------------------------


def build_day_of_week_analysis(
    evaluations: list[EvaluationOutcome], episodes: list[Episode]
) -> dict[str, Any]:
    evaluations_by_day: dict[int, int] = defaultdict(int)
    for evaluation in evaluations:
        ts = _parse_timestamp(evaluation.timestamp)
        if ts is not None:
            evaluations_by_day[ts.weekday()] += 1

    episodes_by_day: dict[int, list[Episode]] = defaultdict(list)
    for episode in episodes:
        ts = _parse_timestamp(episode.start_timestamp)
        if ts is not None:
            episodes_by_day[ts.weekday()].append(episode)

    rows = []
    for day_index, day_name in enumerate(WEEKDAY_NAMES):
        day_episodes = episodes_by_day.get(day_index, [])
        directional = [e for e in day_episodes if e.direction is not None]
        mfe_values = [
            e.windows[60].mfe_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mfe_pips is not None
        ]
        mae_values = [
            e.windows[60].mae_pips
            for e in directional
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mae_pips is not None
        ]
        categories = Counter(e.category for e in day_episodes)

        rows.append(
            {
                "day": day_name,
                "is_weekend": day_index >= 5,
                "evaluations": evaluations_by_day.get(day_index, 0),
                "episodes": len(day_episodes),
                "sample_size_label": sample_size_label(len(day_episodes)),
                "categories": dict(categories),
                "mean_mfe_pips_60m": round(_mean(mfe_values), 2) if mfe_values else None,
                "mean_mae_pips_60m": round(_mean(mae_values), 2) if mae_values else None,
            }
        )

    return {"by_day": rows}


# ---------------------------------------------------------------------------
# Section 12: Direction analysis
# ---------------------------------------------------------------------------


def build_direction_analysis(episodes: list[Episode]) -> dict[str, Any]:
    groups: dict[str, list[Episode]] = {"bullish": [], "bearish": [], "unknown": []}

    for episode in episodes:
        key = episode.direction if episode.direction in ("bullish", "bearish") else "unknown"
        groups[key].append(episode)

    rows = {}
    for key, group_episodes in groups.items():
        mfe_values = [
            e.windows[60].mfe_pips
            for e in group_episodes
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mfe_pips is not None
        ]
        mae_values = [
            e.windows[60].mae_pips
            for e in group_episodes
            if 60 in e.windows and not e.windows[60].insufficient_data and e.windows[60].mae_pips is not None
        ]
        net_values = [
            signed
            for e in group_episodes
            if 60 in e.windows
            and not e.windows[60].insufficient_data
            and (signed := _signed_expected_pips(e.windows[60])) is not None
        ]
        categories = Counter(e.category for e in group_episodes)

        rows[key] = {
            "episode_count": len(group_episodes),
            "sample_size_label": sample_size_label(len(group_episodes)),
            "categories": dict(categories),
            "mean_mfe_pips_60m": round(_mean(mfe_values), 2) if mfe_values else None,
            "mean_mae_pips_60m": round(_mean(mae_values), 2) if mae_values else None,
            "mean_net_directional_pips_60m": round(_mean(net_values), 2) if net_values else None,
        }

    return rows


# ---------------------------------------------------------------------------
# Section 13: R-multiple analysis
# ---------------------------------------------------------------------------


def build_r_multiple_analysis(episodes: list[Episode]) -> dict[str, Any]:
    usable = [ep for ep in episodes if ep.r_analysis.available]

    outcome_summary: dict[int, dict[str, Any]] = {}
    for multiple in R_MULTIPLES:
        outcomes = [ep.r_analysis.outcome_by_multiple.get(multiple) for ep in usable]
        target_first = sum(1 for o in outcomes if o == "target_first")
        stop_first = sum(1 for o in outcomes if o == "stop_first")
        undetermined = sum(1 for o in outcomes if o == "undetermined_within_horizon")

        outcome_summary[multiple] = {
            "target_first_count": target_first,
            "target_first_percentage": (
                round(100.0 * target_first / len(outcomes), 2) if outcomes else None
            ),
            "stop_first_count": stop_first,
            "undetermined_count": undetermined,
        }

    return {
        "episodes_with_usable_r_data": len(usable),
        "total_episodes": len(episodes),
        "sample_size_label": sample_size_label(len(usable)),
        "reliability_warning": (
            "Sample is extremely small; treat any percentage here as "
            "illustrative only, not a reliable estimate."
            if len(usable) < 10
            else None
        ),
        "by_r_multiple": outcome_summary,
    }


# ---------------------------------------------------------------------------
# Section 14: Actual setup / signal analysis
#
# Deliberately separate from the rejection/episode machinery above --
# an actual SIGNAL_PENDING_APPROVAL record already carries its own
# real entry/stop_loss/take_profit (app/strategies/smc_manual_signal.py),
# not a hypothetical order-block-derived stop, so it must never be
# mixed into the rejected-episode statistics.
# ---------------------------------------------------------------------------


_SETUP_DIRECTION_MAP = {"BUY": "bullish", "SELL": "bearish"}


def _walk_actual_setup_r_outcome(
    price_history: pd.DataFrame,
    reference_index: int,
    eval_timestamp: datetime,
    entry: float,
    stop: float,
    direction: str,
    horizon_minutes: int,
) -> dict[int, str]:
    """
    Same walk-forward mechanics as
    scripts.analyze_rejection_outcomes.compute_r_analysis, intentionally
    re-implemented in miniature here rather than reused: that function
    is coupled to sourcing its stop from evidence.order_block, whereas
    an actual setup's entry/stop are already real, recorded values on
    the record itself. This is a small (~20 line), clearly-documented
    duplication of a self-contained loop, not a duplication of the
    surrounding categorisation/direction/episode logic.
    """

    r_distance = abs(entry - stop)
    outcome_by_multiple: dict[int, str] = {m: "undetermined_within_horizon" for m in R_MULTIPLES}

    if r_distance <= 0:
        return outcome_by_multiple

    horizon_end = eval_timestamp + timedelta(minutes=horizon_minutes)
    forward = price_history.iloc[reference_index + 1 :]
    forward = forward[forward["time"] <= horizon_end]

    if forward.empty:
        return outcome_by_multiple

    if direction == "bullish":
        stop_level = entry - r_distance
        targets = {m: entry + m * r_distance for m in R_MULTIPLES}
    else:
        stop_level = entry + r_distance
        targets = {m: entry - m * r_distance for m in R_MULTIPLES}

    remaining = set(R_MULTIPLES)

    for _, row in forward.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        stop_hit = (low <= stop_level) if direction == "bullish" else (high >= stop_level)

        for multiple in list(remaining):
            target_hit = (
                (high >= targets[multiple]) if direction == "bullish" else (low <= targets[multiple])
            )

            if stop_hit:
                outcome_by_multiple[multiple] = "stop_first"
                remaining.discard(multiple)
            elif target_hit:
                outcome_by_multiple[multiple] = "target_first"
                remaining.discard(multiple)

        if not remaining:
            break

    return outcome_by_multiple


def build_actual_setup_analysis(
    records: list[dict[str, Any]],
    price_history: pd.DataFrame,
    pip_size: float,
) -> dict[str, Any]:
    setups = []

    for record in records:
        if _status_label(record) != REAL_SETUP_STATUS:
            continue

        raw_direction = record.get("direction")
        direction = _SETUP_DIRECTION_MAP.get(raw_direction)
        entry = record.get("entry")
        stop_loss = record.get("stop_loss")
        take_profit = record.get("take_profit")
        timestamp = _parse_timestamp(record.get("timestamp_utc"))

        if (
            direction is None
            or not isinstance(entry, (int, float))
            or not isinstance(stop_loss, (int, float))
            or timestamp is None
        ):
            continue

        reference_index = find_reference_index(price_history, timestamp)
        if reference_index is None:
            continue

        max_window = max(OUTCOME_WINDOWS_MINUTES)
        windows = {
            window_minutes: compute_window_outcome(
                price_history, reference_index, timestamp, window_minutes, direction, pip_size
            )
            for window_minutes in OUTCOME_WINDOWS_MINUTES
        }

        r_outcomes = _walk_actual_setup_r_outcome(
            price_history,
            reference_index,
            timestamp,
            float(entry),
            float(stop_loss),
            direction,
            max_window,
        )

        setups.append(
            {
                "timestamp": timestamp.isoformat(),
                "direction": raw_direction,
                "entry": entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "mfe_pips_60m": windows[60].mfe_pips,
                "mae_pips_60m": windows[60].mae_pips,
                "mfe_pips_240m": windows[240].mfe_pips,
                "mae_pips_240m": windows[240].mae_pips,
                "r_distance_pips": abs(float(entry) - float(stop_loss)) / pip_size,
                "r_outcome_1r": r_outcomes.get(1),
                "r_outcome_2r": r_outcomes.get(2),
                "r_outcome_3r": r_outcomes.get(3),
            }
        )

    return {
        "setup_count": len(setups),
        "setups": setups,
        "message": (
            None
            if setups
            else "No actual strategy setups are available for performance analysis."
        ),
    }


# ---------------------------------------------------------------------------
# Section 16: Baseline comparison
# ---------------------------------------------------------------------------


def build_baseline_snapshot(
    dataset_overview: dict[str, Any],
    gate_distribution: dict[str, Any],
    window_stats: dict[str, dict[int, WindowStats]],
    actual_setup_analysis: dict[str, Any],
) -> dict[str, Any]:
    return {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_evaluations": dataset_overview["valid_evaluations"],
        "deduplicated_rejection_episodes": dataset_overview["deduplicated_rejection_episodes"],
        "actual_setup_count": actual_setup_analysis["setup_count"],
        "by_category": {
            category: {
                "episode_count": info["episode_count"],
                "mean_mfe_pips_60m": (
                    window_stats.get(category, {}).get(60).mean_mfe_pips
                    if window_stats.get(category, {}).get(60)
                    else None
                ),
                "mean_mae_pips_60m": (
                    window_stats.get(category, {}).get(60).mean_mae_pips
                    if window_stats.get(category, {}).get(60)
                    else None
                ),
                "mfe_mae_ratio_60m": (
                    window_stats.get(category, {}).get(60).mfe_mae_ratio_mean
                    if window_stats.get(category, {}).get(60)
                    else None
                ),
            }
            for category, info in gate_distribution.items()
        },
    }


def load_baseline(baseline_path: Path) -> Optional[dict[str, Any]]:
    if not baseline_path.exists():
        return None

    try:
        with baseline_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def save_baseline(
    snapshot: dict[str, Any], baseline_path: Path, *, replace: bool
) -> tuple[bool, str]:
    """
    Returns (saved, message). Refuses to overwrite an existing baseline
    unless replace=True (--replace-baseline), per the task brief's
    explicit "do not silently overwrite" requirement.
    """

    baseline_path.parent.mkdir(parents=True, exist_ok=True)

    if baseline_path.exists() and not replace:
        return (
            False,
            f"Baseline already exists at {baseline_path} -- not overwritten. "
            "Pass --save-baseline together with --replace-baseline to replace it.",
        )

    with baseline_path.open("w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=False)
        handle.write("\n")

    verb = "Replaced" if baseline_path.exists() and replace else "Saved"
    return True, f"{verb} baseline at {baseline_path}."


def compare_to_baseline(
    current: dict[str, Any], baseline: Optional[dict[str, Any]]
) -> dict[str, Any]:
    if baseline is None:
        return {
            "baseline_available": False,
            "message": (
                "No baseline exists yet. Run with --save-baseline to create "
                "one from this run's results."
            ),
        }

    def _delta(current_value: Optional[float], baseline_value: Optional[float]) -> Optional[float]:
        if current_value is None or baseline_value is None:
            return None
        return round(current_value - baseline_value, 2)

    category_comparison = {}
    all_categories = set(current["by_category"]) | set(baseline.get("by_category", {}))

    for category in sorted(all_categories):
        current_cat = current["by_category"].get(category, {})
        baseline_cat = baseline.get("by_category", {}).get(category, {})

        category_comparison[category] = {
            "episode_count_current": current_cat.get("episode_count"),
            "episode_count_baseline": baseline_cat.get("episode_count"),
            "mean_mfe_pips_60m_current": current_cat.get("mean_mfe_pips_60m"),
            "mean_mfe_pips_60m_baseline": baseline_cat.get("mean_mfe_pips_60m"),
            "mean_mfe_pips_60m_delta": _delta(
                current_cat.get("mean_mfe_pips_60m"), baseline_cat.get("mean_mfe_pips_60m")
            ),
            "mean_mae_pips_60m_current": current_cat.get("mean_mae_pips_60m"),
            "mean_mae_pips_60m_baseline": baseline_cat.get("mean_mae_pips_60m"),
            "mean_mae_pips_60m_delta": _delta(
                current_cat.get("mean_mae_pips_60m"), baseline_cat.get("mean_mae_pips_60m")
            ),
            "mfe_mae_ratio_60m_current": current_cat.get("mfe_mae_ratio_60m"),
            "mfe_mae_ratio_60m_baseline": baseline_cat.get("mfe_mae_ratio_60m"),
        }

    return {
        "baseline_available": True,
        "baseline_saved_at_utc": baseline.get("saved_at_utc"),
        "total_evaluations_current": current["total_evaluations"],
        "total_evaluations_baseline": baseline.get("total_evaluations"),
        "episodes_current": current["deduplicated_rejection_episodes"],
        "episodes_baseline": baseline.get("deduplicated_rejection_episodes"),
        "actual_setup_count_current": current["actual_setup_count"],
        "actual_setup_count_baseline": baseline.get("actual_setup_count"),
        "by_category": category_comparison,
    }


# ---------------------------------------------------------------------------
# Section 17: Research conclusion (observational only)
# ---------------------------------------------------------------------------


def build_research_conclusion(
    *,
    dataset_overview: dict[str, Any],
    protection_and_opportunity: dict[str, Any],
    r_multiple_analysis: dict[str, Any],
    actual_setup_analysis: dict[str, Any],
    gate_distribution: dict[str, Any],
) -> dict[str, Any]:
    current_observations = [
        f"{dataset_overview['valid_evaluations']} valid evaluations analysed, "
        f"{dataset_overview['deduplicated_rejection_episodes']} de-duplicated rejection episodes.",
        f"{dataset_overview['directional_evaluations']} evaluations had a known "
        f"directional expectation; {dataset_overview['unknown_direction_evaluations']} did not.",
    ]

    insufficient_data_areas = [
        f"{category}: {info['episode_count']} episode(s) ({info['sample_size_label']})"
        for category, info in gate_distribution.items()
        if info["episode_count"] < 10
    ]

    return {
        "current_observations": current_observations,
        "protective_filter_candidates": [
            f"{row['category']} (n={row['sample_size']}, {row['sample_size_label']})"
            for row in protection_and_opportunity["protective_filter_candidates"]
        ],
        "future_review_candidates": [
            f"{row['category']} (n={row['sample_size']}, {row['sample_size_label']})"
            for row in protection_and_opportunity["future_review_candidates"]
        ],
        "insufficient_data_areas": insufficient_data_areas,
        "actual_setup_count": actual_setup_analysis["setup_count"],
        "actual_setup_message": actual_setup_analysis["message"],
        "r_multiple_sample_note": (
            f"{r_multiple_analysis['episodes_with_usable_r_data']} episode(s) had usable "
            f"R data ({r_multiple_analysis['sample_size_label']})."
        ),
    }


# ---------------------------------------------------------------------------
# Text report assembly
# ---------------------------------------------------------------------------


def _fmt(value: Optional[float], decimals: int = 1) -> str:
    return f"{value:.{decimals}f}" if value is not None else "n/a"


def generate_text_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [REPORT_DIVIDER, "FORWARD TEST MASTER RESEARCH REPORT", REPORT_DIVIDER, ""]

    overview = payload["dataset_overview"]
    lines.append(SECTION_DIVIDER)
    lines.append("1. DATASET OVERVIEW")
    lines.append(SECTION_DIVIDER)
    for key in (
        "report_generated_at_utc",
        "log_file",
        "first_evaluation_timestamp",
        "last_evaluation_timestamp",
        "calendar_duration_days",
        "total_lines_read",
        "malformed_rows",
        "duplicate_records_skipped",
        "valid_evaluations",
        "api_errors",
        "no_setup_count",
        "setup_ready_count",
        "buy_signals",
        "sell_signals",
        "unknown_status_count",
        "directional_evaluations",
        "unknown_direction_evaluations",
        "deduplicated_rejection_episodes",
        "sample_includes_weekdays",
        "sample_includes_weekends",
    ):
        lines.append(f"{key}: {overview[key]}")
    if overview["market_closure_gaps_ge_4h"]:
        lines.append(f"market closure gaps (>=4h): {len(overview['market_closure_gaps_ge_4h'])}")
    lines.append("")

    status = payload["status_distribution"]
    lines.append(SECTION_DIVIDER)
    lines.append("2. STATUS DISTRIBUTION")
    lines.append(SECTION_DIVIDER)
    for label, info in status["by_status"].items():
        lines.append(f"{label:<24} {info['count']:>6}   {info['percentage']:>6.2f}%")
    lines.append("")

    gates = payload["rejection_gate_distribution"]
    lines.append(SECTION_DIVIDER)
    lines.append("3. REJECTION GATE DISTRIBUTION")
    lines.append(SECTION_DIVIDER)
    for category, info in sorted(gates.items(), key=lambda kv: -kv[1]["episode_count"]):
        lines.append("")
        lines.append(f"{category}  [{info['sample_size_label']}]")
        lines.append(
            f"  raw: {info['raw_evaluation_count']} ({info['raw_evaluation_percentage']}%)   "
            f"episodes: {info['episode_count']} ({info['episode_percentage']}%)"
        )
        lines.append(f"  first: {info['first_occurrence']}   last: {info['last_occurrence']}")
        lines.append(
            f"  longest run: {info['longest_run_evaluations']} evaluations "
            f"({info['longest_run_start']} .. {info['longest_run_end']})"
        )
        if info["currently_active_run"]:
            lines.append(
                f"  CURRENTLY ACTIVE: {info['currently_active_run']} evaluations, "
                f"since {info['currently_active_since']}"
            )
    lines.append("")

    transitions = payload["gate_transitions"]
    lines.append(SECTION_DIVIDER)
    lines.append("4. REJECTION GATE TRANSITIONS")
    lines.append(SECTION_DIVIDER)
    if transitions["chronological_episode_chain"]:
        lines.append(f"\n{' '*4}v\n".join(transitions["chronological_episode_chain"]))
    lines.append("")
    lines.append("Transition pair frequency:")
    for pair in transitions["transition_pair_counts"]:
        lines.append(
            f"  {pair['from_category']} -> {pair['to_category']}: {pair['occurrences']}"
        )
    lines.append("")

    window_stats = payload["window_stats"]
    lines.append(SECTION_DIVIDER)
    lines.append("5/6. REJECTION OUTCOME ANALYSIS + MFE/MAE RATIO (all windows)")
    lines.append(SECTION_DIVIDER)
    for category, per_window in window_stats.items():
        lines.append("")
        lines.append(category)
        for window_minutes in OUTCOME_WINDOWS_MINUTES:
            stats = per_window[window_minutes]
            lines.append(
                f"  {window_minutes:>3}m  n={stats.usable_episode_count:<3} "
                f"MFE avg/med={_fmt(stats.mean_mfe_pips)}/{_fmt(stats.median_mfe_pips)}  "
                f"MAE avg/med={_fmt(stats.mean_mae_pips)}/{_fmt(stats.median_mae_pips)}  "
                f"net avg/med={_fmt(stats.mean_net_directional_pips)}/{_fmt(stats.median_net_directional_pips)}  "
                f"ratio avg/med={_fmt(stats.mfe_mae_ratio_mean, 2)}/{_fmt(stats.mfe_mae_ratio_median, 2)}"
            )
    lines.append("")

    protection = payload["protection_and_opportunity"]
    lines.append(SECTION_DIVIDER)
    lines.append("7/8. FILTER PROTECTION / MISSED-OPPORTUNITY ANALYSIS")
    lines.append(SECTION_DIVIDER)
    lines.append("Protective filter candidates:")
    for row in protection["protective_filter_candidates"]:
        lines.append(
            f"  {row['category']} (n={row['sample_size']}, {row['sample_size_label']}) -- {row['label']}"
        )
    if not protection["protective_filter_candidates"]:
        lines.append("  (none identified in this sample)")
    lines.append("Future review candidates:")
    for row in protection["future_review_candidates"]:
        lines.append(
            f"  {row['category']} (n={row['sample_size']}, {row['sample_size_label']}) -- {row['label']}"
        )
    if not protection["future_review_candidates"]:
        lines.append("  (none identified in this sample)")
    lines.append("")

    deep_gate = payload["deep_gate_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("9. DEEP-GATE ANALYSIS")
    lines.append(SECTION_DIVIDER)
    for row in deep_gate["by_depth"]:
        lines.append(
            f"  [depth {row['depth']}] {row['category']:<30} n={row['sample_size']:<4} "
            f"MFE={_fmt(row['mean_mfe_pips_60m']):>6}p MAE={_fmt(row['mean_mae_pips_60m']):>6}p "
            f"ratio={_fmt(row['mfe_mae_ratio_60m'], 2)}"
        )
    lines.append(f"MFE appears monotonic with depth: {deep_gate['mfe_appears_monotonic_with_depth']}")
    lines.append(f"MAE appears monotonic with depth: {deep_gate['mae_appears_monotonic_with_depth']}")
    lines.append("")

    tod = payload["time_of_day_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("10. TIME-OF-DAY ANALYSIS")
    lines.append(SECTION_DIVIDER)
    lines.append(tod["session_boundary_note"])
    for row in tod["by_session"]:
        lines.append(
            f"  {row['session']:<40} n={row['episode_count']:<4} "
            f"MFE={_fmt(row['mean_mfe_pips_60m']):>6}p MAE={_fmt(row['mean_mae_pips_60m']):>6}p"
        )
    lines.append("")

    dow = payload["day_of_week_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("11. DAY-OF-WEEK ANALYSIS")
    lines.append(SECTION_DIVIDER)
    for row in dow["by_day"]:
        marker = " (weekend/no-market)" if row["is_weekend"] else ""
        lines.append(
            f"  {row['day']:<10}{marker:<22} evaluations={row['evaluations']:<5} episodes={row['episodes']:<4} "
            f"MFE={_fmt(row['mean_mfe_pips_60m']):>6}p MAE={_fmt(row['mean_mae_pips_60m']):>6}p"
        )
    lines.append("")

    direction = payload["direction_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("12. DIRECTION ANALYSIS")
    lines.append(SECTION_DIVIDER)
    for key, info in direction.items():
        lines.append(
            f"  {key:<10} n={info['episode_count']:<4} MFE={_fmt(info['mean_mfe_pips_60m']):>6}p "
            f"MAE={_fmt(info['mean_mae_pips_60m']):>6}p net={_fmt(info['mean_net_directional_pips_60m']):>6}p"
        )
    lines.append("")

    r_analysis = payload["r_multiple_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("13. R-MULTIPLE ANALYSIS")
    lines.append(SECTION_DIVIDER)
    lines.append(
        f"Episodes with usable R data: {r_analysis['episodes_with_usable_r_data']} / "
        f"{r_analysis['total_episodes']}  [{r_analysis['sample_size_label']}]"
    )
    if r_analysis["reliability_warning"]:
        lines.append(f"WARNING: {r_analysis['reliability_warning']}")
    for multiple, info in r_analysis["by_r_multiple"].items():
        lines.append(
            f"  +{multiple}R before -1R: {info['target_first_count']} "
            f"({_fmt(info['target_first_percentage'])}%)   stop-first: {info['stop_first_count']}   "
            f"undetermined: {info['undetermined_count']}"
        )
    lines.append("")

    setups = payload["actual_setup_analysis"]
    lines.append(SECTION_DIVIDER)
    lines.append("14. ACTUAL SETUP / SIGNAL ANALYSIS")
    lines.append(SECTION_DIVIDER)
    if setups["message"]:
        lines.append(setups["message"])
    else:
        for setup in setups["setups"]:
            lines.append(
                f"  {setup['timestamp']}  {setup['direction']}  entry={setup['entry']} "
                f"sl={setup['stop_loss']} tp={setup['take_profit']}  "
                f"MFE60m={_fmt(setup['mfe_pips_60m'])}p MAE60m={_fmt(setup['mae_pips_60m'])}p"
            )
    lines.append("")

    lines.append(SECTION_DIVIDER)
    lines.append("15. SAMPLE-SIZE WARNINGS (per category, episode-based)")
    lines.append(SECTION_DIVIDER)
    for category, info in gates.items():
        lines.append(f"  {category:<32} n={info['episode_count']:<4} {info['sample_size_label']}")
    lines.append("")

    baseline = payload["baseline_comparison"]
    lines.append(SECTION_DIVIDER)
    lines.append("16. BASELINE COMPARISON")
    lines.append(SECTION_DIVIDER)
    if not baseline["baseline_available"]:
        lines.append(baseline["message"])
    else:
        lines.append(f"Baseline saved at: {baseline['baseline_saved_at_utc']}")
        lines.append(
            f"Total evaluations: {baseline['total_evaluations_current']} "
            f"(baseline: {baseline['total_evaluations_baseline']})"
        )
        lines.append(
            f"Episodes: {baseline['episodes_current']} (baseline: {baseline['episodes_baseline']})"
        )
        lines.append(
            f"Actual setups: {baseline['actual_setup_count_current']} "
            f"(baseline: {baseline['actual_setup_count_baseline']})"
        )
        for category, info in baseline["by_category"].items():
            lines.append(
                f"  {category:<32} episodes {info['episode_count_current']} "
                f"(was {info['episode_count_baseline']})   "
                f"MFE60m {_fmt(info['mean_mfe_pips_60m_current'])}p "
                f"(delta {_fmt(info['mean_mfe_pips_60m_delta'])})   "
                f"MAE60m {_fmt(info['mean_mae_pips_60m_current'])}p "
                f"(delta {_fmt(info['mean_mae_pips_60m_delta'])})"
            )
    lines.append("")

    conclusion = payload["research_conclusion"]
    lines.append(SECTION_DIVIDER)
    lines.append("17. RESEARCH CONCLUSION (observational only)")
    lines.append(SECTION_DIVIDER)
    lines.append("CURRENT OBSERVATIONS")
    for item in conclusion["current_observations"]:
        lines.append(f"  - {item}")
    lines.append("PROTECTIVE FILTER CANDIDATES")
    for item in conclusion["protective_filter_candidates"]:
        lines.append(f"  - {item}")
    if not conclusion["protective_filter_candidates"]:
        lines.append("  (none identified in this sample)")
    lines.append("FUTURE REVIEW CANDIDATES")
    for item in conclusion["future_review_candidates"]:
        lines.append(f"  - {item}")
    if not conclusion["future_review_candidates"]:
        lines.append("  (none identified in this sample)")
    lines.append("INSUFFICIENT DATA AREAS")
    for item in conclusion["insufficient_data_areas"]:
        lines.append(f"  - {item}")
    lines.append(f"ACTUAL SETUP COUNT: {conclusion['actual_setup_count']}")
    if conclusion["actual_setup_message"]:
        lines.append(f"  {conclusion['actual_setup_message']}")
    lines.append("")
    lines.append(
        "This report is observational only. It does not recommend or "
        "imply any change to strategy thresholds, gates, or parameters."
    )
    lines.append(REPORT_DIVIDER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _window_stats_to_dict(stats: WindowStats) -> dict[str, Any]:
    return {
        "window_minutes": stats.window_minutes,
        "episode_count": stats.episode_count,
        "usable_episode_count": stats.usable_episode_count,
        "mean_mfe_pips": stats.mean_mfe_pips,
        "median_mfe_pips": stats.median_mfe_pips,
        "mean_mae_pips": stats.mean_mae_pips,
        "median_mae_pips": stats.median_mae_pips,
        "mean_net_directional_pips": stats.mean_net_directional_pips,
        "median_net_directional_pips": stats.median_net_directional_pips,
        "pct_favorable_at_least": stats.pct_favorable_at_least,
        "pct_adverse_at_least": stats.pct_adverse_at_least,
        "mfe_mae_ratio_mean": stats.mfe_mae_ratio_mean,
        "mfe_mae_ratio_median": stats.mfe_mae_ratio_median,
    }


def write_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    serializable = dict(payload)
    serializable["window_stats"] = {
        category: {str(minutes): _window_stats_to_dict(stats) for minutes, stats in per_window.items()}
        for category, per_window in payload["window_stats"].items()
    }

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(serializable, handle, indent=2, sort_keys=False)
        handle.write("\n")


CATEGORY_CSV_FIELDNAMES = (
    "category",
    "raw_evaluation_count",
    "episode_count",
    "sample_size_label",
    "window_minutes",
    "usable_episode_count",
    "mean_mfe_pips",
    "median_mfe_pips",
    "mean_mae_pips",
    "median_mae_pips",
    "mean_net_directional_pips",
    "mfe_mae_ratio_mean",
)


def write_category_csv(
    gate_distribution: dict[str, Any],
    window_stats: dict[str, dict[int, WindowStats]],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATEGORY_CSV_FIELDNAMES)
        writer.writeheader()

        for category, info in gate_distribution.items():
            per_window = window_stats.get(category, {})
            for window_minutes in OUTCOME_WINDOWS_MINUTES:
                stats = per_window.get(window_minutes)
                writer.writerow(
                    {
                        "category": category,
                        "raw_evaluation_count": info["raw_evaluation_count"],
                        "episode_count": info["episode_count"],
                        "sample_size_label": info["sample_size_label"],
                        "window_minutes": window_minutes,
                        "usable_episode_count": stats.usable_episode_count if stats else 0,
                        "mean_mfe_pips": stats.mean_mfe_pips if stats else "",
                        "median_mfe_pips": stats.median_mfe_pips if stats else "",
                        "mean_mae_pips": stats.mean_mae_pips if stats else "",
                        "median_mae_pips": stats.median_mae_pips if stats else "",
                        "mean_net_directional_pips": (
                            stats.mean_net_directional_pips if stats else ""
                        ),
                        "mfe_mae_ratio_mean": stats.mfe_mae_ratio_mean if stats else "",
                    }
                )


def write_hour_csv(time_of_day: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ("utc_hour", "session", "episode_count", "mean_mfe_pips_60m", "mean_mae_pips_60m")

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in time_of_day["by_utc_hour"]:
            writer.writerow({key: row[key] for key in fieldnames})


def write_weekday_csv(day_of_week: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = (
        "day",
        "is_weekend",
        "evaluations",
        "episodes",
        "mean_mfe_pips_60m",
        "mean_mae_pips_60m",
    )

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in day_of_week["by_day"]:
            writer.writerow({key: row[key] for key in fieldnames})


def write_text_report(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.write("\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Master forward-test research report: combines rejection-gate "
            "diagnostics, de-duplicated episode outcome analysis, and "
            "cross-cutting breakdowns (time-of-day, day-of-week, "
            "direction, R-multiple, actual setups) into one evidence-only "
            "report. Read-only: never places trades, never modifies MT5 "
            "positions or strategy settings, never modifies historical logs."
        ),
    )

    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help=f"JSON Lines log path to analyse (default: {DEFAULT_LOG_PATH}).",
    )
    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
        help=f"Symbol to fetch price history for (default: {DEFAULT_SYMBOL}).",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save this run's summary as logs/forward_test/research_baseline.json.",
    )
    parser.add_argument(
        "--replace-baseline",
        action="store_true",
        help="Required together with --save-baseline to overwrite an existing baseline.",
    )
    parser.add_argument(
        "--json-output",
        default=str(DEFAULT_JSON_OUTPUT_PATH),
        help=f"Path to write the JSON report (default: {DEFAULT_JSON_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--csv-output",
        default=str(DEFAULT_CSV_OUTPUT_PATH),
        help=f"Path to write the by-category CSV (default: {DEFAULT_CSV_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--txt-output",
        default=str(DEFAULT_TXT_OUTPUT_PATH),
        help=f"Path to write the human-readable text report (default: {DEFAULT_TXT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--baseline-file",
        default=str(DEFAULT_BASELINE_PATH),
        help=f"Baseline JSON path (default: {DEFAULT_BASELINE_PATH}).",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    args = _parse_args(argv)

    log_path = Path(args.log_file)
    symbol = args.symbol
    json_output_path = Path(args.json_output)
    csv_output_path = Path(args.csv_output)
    txt_output_path = Path(args.txt_output)
    baseline_path = Path(args.baseline_file)

    raw_records, malformed_line_count = load_records(log_path)
    records, duplicate_count = deduplicate_exact_records(raw_records)

    connect_mt5()

    try:
        pip_size = determine_pip_size(symbol)

        timestamps = [
            ts for record in records if (ts := _parse_timestamp(record.get("timestamp_utc"))) is not None
        ]

        if not timestamps:
            print("No timestamped records found in the log; nothing to analyse.")
            return

        price_history = fetch_price_history_for_symbol(symbol, min(timestamps))

        evaluations, exclusions = build_evaluation_outcomes(records, price_history, pip_size)
        episodes = build_episodes(evaluations)
        actual_setup_analysis = build_actual_setup_analysis(records, price_history, pip_size)
    finally:
        disconnect_mt5()

    dataset_overview = build_dataset_overview(
        log_path=log_path,
        raw_records=raw_records,
        records=records,
        malformed_line_count=malformed_line_count,
        duplicate_count=duplicate_count,
        evaluations=evaluations,
        episodes=episodes,
    )
    status_distribution = build_status_distribution(records)
    gate_distribution = build_rejection_gate_distribution(evaluations, episodes)
    gate_transitions = build_gate_transitions(episodes)
    window_stats = compute_window_stats_by_category(episodes)
    protection_and_opportunity = build_protection_and_opportunity_analysis(window_stats)
    deep_gate_analysis = build_deep_gate_analysis(window_stats)
    time_of_day_analysis = build_time_of_day_analysis(episodes)
    day_of_week_analysis = build_day_of_week_analysis(evaluations, episodes)
    direction_analysis = build_direction_analysis(episodes)
    r_multiple_analysis = build_r_multiple_analysis(episodes)

    baseline_snapshot = build_baseline_snapshot(
        dataset_overview, gate_distribution, window_stats, actual_setup_analysis
    )
    existing_baseline = load_baseline(baseline_path)
    baseline_comparison = compare_to_baseline(baseline_snapshot, existing_baseline)

    research_conclusion = build_research_conclusion(
        dataset_overview=dataset_overview,
        protection_and_opportunity=protection_and_opportunity,
        r_multiple_analysis=r_multiple_analysis,
        actual_setup_analysis=actual_setup_analysis,
        gate_distribution=gate_distribution,
    )

    payload: dict[str, Any] = {
        "dataset_overview": dataset_overview,
        "data_quality_exclusions": dict(exclusions),
        "status_distribution": status_distribution,
        "rejection_gate_distribution": gate_distribution,
        "gate_transitions": gate_transitions,
        "window_stats": window_stats,
        "protection_and_opportunity": protection_and_opportunity,
        "deep_gate_analysis": deep_gate_analysis,
        "time_of_day_analysis": time_of_day_analysis,
        "day_of_week_analysis": day_of_week_analysis,
        "direction_analysis": direction_analysis,
        "r_multiple_analysis": r_multiple_analysis,
        "actual_setup_analysis": actual_setup_analysis,
        "baseline_comparison": baseline_comparison,
        "research_conclusion": research_conclusion,
        "pip_size_used": pip_size,
        "symbol": symbol,
    }

    text_report = generate_text_report(payload)
    print(text_report)

    write_json(payload, json_output_path)
    write_category_csv(gate_distribution, window_stats, csv_output_path)
    write_hour_csv(time_of_day_analysis, DEFAULT_HOUR_CSV_PATH)
    write_weekday_csv(day_of_week_analysis, DEFAULT_WEEKDAY_CSV_PATH)
    write_text_report(text_report, txt_output_path)

    print(f"\nJSON report written to:      {json_output_path}")
    print(f"By-category CSV written to:  {csv_output_path}")
    print(f"By-hour CSV written to:      {DEFAULT_HOUR_CSV_PATH}")
    print(f"By-weekday CSV written to:   {DEFAULT_WEEKDAY_CSV_PATH}")
    print(f"Text report written to:      {txt_output_path}")

    if args.save_baseline:
        saved, message = save_baseline(
            baseline_snapshot, baseline_path, replace=args.replace_baseline
        )
        print(f"\n{message}")
    elif not baseline_path.exists():
        print(
            "\nNo baseline exists yet. Re-run with --save-baseline to create "
            f"one at {baseline_path}."
        )


if __name__ == "__main__":
    main()
