"""
Forward-test rejection OUTCOME diagnostics for the SMC signal strategy.

Read-only. Answers: "when the strategy rejected a setup, what did price
actually do afterward?" This script does not import, call, or modify
any strategy/engine/MT5-write code -- it only reads
logs/forward_test/smc_signal.jsonl (never opens it for writing) and
calls the real, unmodified app.mt5.market.get_candles() to fetch
historical price data for OUTCOME MEASUREMENT ONLY.

Look-ahead discipline (see module docstring section below and every
function that touches `record` vs `price_history`): the rejection
category and the expected direction for a record are derived ONLY from
that record's own logged `evidence`/`rejection_reasons` -- never from
price data. Future candles are used exclusively to measure what price
did AFTER the already-logged rejection; they never feed back into
reconstructing what the strategy "should" have decided.

Usage (PowerShell):

    python scripts\\analyze_rejection_outcomes.py

    python scripts\\analyze_rejection_outcomes.py --log-file logs\\forward_test\\smc_signal.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from app.mt5.connection import connect_mt5, disconnect_mt5  # noqa: E402
from app.mt5.market import get_candles  # noqa: E402

try:
    import MetaTrader5 as mt5  # noqa: E402
except ImportError:  # pragma: no cover - MetaTrader5 always present in this env
    mt5 = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_LOG_PATH = _REPO_ROOT / "logs" / "forward_test" / "smc_signal.jsonl"
DEFAULT_JSON_OUTPUT_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "rejection_outcome_analysis.json"
)
DEFAULT_OUTCOMES_CSV_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "rejection_outcomes.csv"
)
DEFAULT_EPISODES_CSV_PATH = (
    _REPO_ROOT / "logs" / "forward_test" / "rejection_episodes.csv"
)

REPORT_DIVIDER = "=" * 68
SECTION_DIVIDER = "-" * 68

SYMBOL = "EURUSD"
DEFAULT_PIP_SIZE = 0.0001

API_ERROR_STATUS_LABEL = "API_ERROR"
OTHER_CATEGORY = "OTHER"
UNKNOWN_TIMESTAMP_LABEL = "unknown"

OUTCOME_WINDOWS_MINUTES: tuple[int, ...] = (15, 30, 60, 120, 240)
M5_MINUTES = 5

# Same-episode continuity tolerance: the recorder runs on a 5-minute
# cadence, so a gap of up to 3 missed cycles (15 minutes) is treated as
# "still the same underlying condition, just a skipped tick" -- not a
# meaningful break. A gap larger than this (an outage, or a weekend/
# holiday close) starts a new episode even if category and direction
# both still match, because the market state either side of a large
# gap is not the same continuously-observed condition; see
# build_episodes() for the full rule.
EPISODE_MAX_GAP_MINUTES = 15

R_MULTIPLES: tuple[int, ...] = (1, 2, 3)

# ---------------------------------------------------------------------------
# Rejection-reason categorisation (same rules as scripts/analyze_forward_test.py
# -- duplicated rather than imported, matching this project's existing
# convention of keeping each scripts/ file independently runnable via
# `python scripts\<name>.py` without requiring the repo root to already
# be on sys.path for cross-script imports).
# ---------------------------------------------------------------------------

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("M5_LOCAL_CONFIRMATION_MISSING", ("m5", "candles")),
    ("H1_H4_BIAS_MISMATCH", ("h1", "h4", "bias")),
    ("M15_ORDER_BLOCK_MISSING", ("order block",)),
    ("M15_LIQUIDITY_SWEEP_MISSING", ("liquidity sweep", "m15")),
    ("M15_CHOCH_MISSING", ("choch", "m15")),
)

# Pipeline depth: how far the strategy progressed before rejecting, in
# the strategy's own gate order (app/strategies/smc_manual_signal.py::
# generate_eurusd_manual_signal -- H4 bias -> H1 confirmation -> M15
# CHoCH -> M15 liquidity sweep (checked *before* the CHoCH's own index)
# -> M15 Order Block -> M15 retracement -> M5 confirmation). Larger =
# deeper. Used only to order the gate-transition comparison; it is not
# itself a claim about outcome quality.
CATEGORY_DEPTH: dict[str, int] = {
    "H1_H4_BIAS_MISMATCH": 1,
    "M15_CHOCH_MISSING": 2,
    "M15_LIQUIDITY_SWEEP_MISSING": 3,
    "M15_ORDER_BLOCK_MISSING": 4,
    "M5_LOCAL_CONFIRMATION_MISSING": 5,
    OTHER_CATEGORY: 0,
}


def categorize_reason(reason_text: str) -> str:
    """Map a raw rejection-reason string to a normalised category."""

    lowered = reason_text.lower()

    for category, keywords in CATEGORY_RULES:
        if all(keyword in lowered for keyword in keywords):
            return category

    return OTHER_CATEGORY


# ---------------------------------------------------------------------------
# Loading (read-only; identical defensive contract to
# scripts/analyze_forward_test.py::load_records)
# ---------------------------------------------------------------------------


def load_records(log_path: Path) -> tuple[list[dict[str, Any]], int]:
    """
    Read every record from the JSON Lines forward-test log.

    Read-only: the file is opened in "r" mode only. A malformed line
    (invalid JSON, or valid JSON that is not an object) is skipped and
    counted, never fatal. A missing file returns an empty list.
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


def _status_label(record: dict[str, Any]) -> str:
    status = record.get("status")
    return status if isinstance(status, str) and status else API_ERROR_STATUS_LABEL


def _reason_strings(record: dict[str, Any]) -> list[str]:
    reasons = record.get("rejection_reasons")

    if not isinstance(reasons, list):
        return []

    return [reason for reason in reasons if isinstance(reason, str) and reason]


def deduplicate_exact_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Drop records whose timestamp_utc exactly repeats an earlier record
    (a genuine duplicate write, not a new 5-minute evaluation tick).
    Keeps the first occurrence, in file order. Records with a missing/
    unparseable timestamp are never treated as duplicates of anything
    (each is kept), since there is no reliable key to compare them by.
    """

    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    duplicate_count = 0

    for record in records:
        timestamp = record.get("timestamp_utc")

        if not isinstance(timestamp, str) or not timestamp:
            kept.append(record)
            continue

        if timestamp in seen:
            duplicate_count += 1
            continue

        seen.add(timestamp)
        kept.append(record)

    return kept, duplicate_count


# ---------------------------------------------------------------------------
# Directional expectation -- evidence-derived ONLY, never invented
# ---------------------------------------------------------------------------


def extract_expected_direction(record: dict[str, Any]) -> Optional[str]:
    """
    The directional expectation that existed AT REJECTION TIME,
    derived only from that record's own logged evidence -- never from
    price data, and never guessed from the rejection-reason text alone
    (which is direction-agnostic wording like "the CHoCH's
    displacement" that doesn't itself say bullish/bearish once
    stripped of the interpolated word).

    Preference order, both already validated against the strategy's
    own evidence schema (app/strategies/smc_manual_signal.py):
      1. evidence.h1_confirmation.expected_direction -- present
         whenever H1 confirmation was evaluated at all, including when
         it FAILED (h1_confirmation.passed == False), since
         expected_direction is the H4 bias direction being tested
         against, not the (dis)agreeing H1 trend itself.
      2. evidence.h4_bias.direction -- present whenever H4 bias passed;
         used only as a fallback for the (unobserved in current data,
         but schema-legal) case where h1_confirmation was never
         evaluated at all.

    Returns None (never a guessed value) if neither field is present
    and valid -- e.g. a record rejected at the H4-bias stage itself,
    where no directional expectation existed yet.
    """

    evidence = record.get("evidence")

    if not isinstance(evidence, dict):
        return None

    h1_confirmation = evidence.get("h1_confirmation")

    if isinstance(h1_confirmation, dict):
        direction = h1_confirmation.get("expected_direction")

        if direction in ("bullish", "bearish"):
            return direction

    h4_bias = evidence.get("h4_bias")

    if isinstance(h4_bias, dict):
        direction = h4_bias.get("direction")

        if direction in ("bullish", "bearish"):
            return direction

    return None


# ---------------------------------------------------------------------------
# Pip size -- derived from live symbol metadata where available
# ---------------------------------------------------------------------------


def determine_pip_size(symbol: str) -> float:
    """
    Derive the canonical pip size from live MT5 symbol metadata.

    Standard convention: a 5-(or 3-)digit quote reports an extra
    fractional "pipette" digit, so pip size = 10 * point; a 4-(or
    2-)digit quote already reports at pip precision, so pip size =
    point. Falls back to DEFAULT_PIP_SIZE (0.0001, correct for EURUSD
    either way) if symbol metadata is unavailable for any reason --
    this is a read-only diagnostic, so a metadata lookup failure must
    never block the analysis.
    """

    try:
        info = mt5.symbol_info(symbol) if mt5 is not None else None

        if info is None or not hasattr(info, "digits") or not hasattr(info, "point"):
            return DEFAULT_PIP_SIZE

        if info.digits in (3, 5):
            return info.point * 10
        if info.digits in (2, 4):
            return info.point

        return DEFAULT_PIP_SIZE

    except Exception:
        return DEFAULT_PIP_SIZE


# ---------------------------------------------------------------------------
# Price history (bulk fetch via the real, fixed get_candles())
# ---------------------------------------------------------------------------


def fetch_price_history(earliest_needed: datetime, latest_needed: datetime) -> pd.DataFrame:
    """
    Fetch enough M5 EURUSD history, via the real, unmodified
    app.mt5.market.get_candles(), to cover every evaluation's reference
    point and its full forward outcome-measurement horizon in one bulk
    call -- not one MT5 round-trip per log record.

    Deliberately uses get_candles() as-is (count-based, "most recent N
    candles from now") rather than a hand-rolled time-range fetch: this
    is exactly what "use the current get_candles() behaviour... do not
    manually subtract broker offsets again" means in practice -- the
    already-fixed, already-tested timestamp correction is inherited
    automatically, with no second offset computation anywhere in this
    script.
    """

    now = datetime.now(timezone.utc)
    span = now - earliest_needed
    candles_needed = int(span.total_seconds() // (M5_MINUTES * 60)) + 200  # buffer

    frame = get_candles(SYMBOL, "M5", max(candles_needed, 500))

    return frame.reset_index(drop=True)


def find_reference_index(price_history: pd.DataFrame, eval_timestamp: datetime) -> Optional[int]:
    """
    The index of the most recent CLOSED M5 candle at or before
    eval_timestamp -- "the price the strategy would have been looking
    at" for this evaluation. None if no candle in price_history is old
    enough (eval_timestamp predates the fetched history).
    """

    times = price_history["time"]
    eligible = price_history.index[times <= eval_timestamp]

    if len(eligible) == 0:
        return None

    return int(eligible[-1])


# ---------------------------------------------------------------------------
# Outcome measurement (future candles used ONLY here, never to
# reconstruct rejection-time strategy state)
# ---------------------------------------------------------------------------


@dataclass
class WindowOutcome:
    window_minutes: int
    candles_available: int
    reference_price: float
    ending_price: float
    net_movement_pips: float
    movement_in_expected_direction_pips: Optional[float]
    movement_against_expected_direction_pips: Optional[float]
    mfe_price: Optional[float]
    mfe_pips: Optional[float]
    mae_price: Optional[float]
    mae_pips: Optional[float]
    insufficient_data: bool


def compute_window_outcome(
    price_history: pd.DataFrame,
    reference_index: int,
    eval_timestamp: datetime,
    window_minutes: int,
    direction: Optional[str],
    pip_size: float,
) -> WindowOutcome:
    """
    Outcome over one forward window, anchored at the EVALUATION's own
    timestamp -- not at the reference candle's timestamp. These two are
    almost always minutes apart (the candle is simply the last closed
    one at/before the evaluation), but can diverge by hours when a
    weekend/holiday closure sits immediately before the evaluation:
    the reference candle is then the last pre-closure candle, while the
    evaluation itself was logged well after (once trading had resumed
    or the evaluation cycle simply ran again). Anchoring the window
    boundary on the stale candle's own time would make the window look
    like it fell entirely inside the closure -- wrongly reporting
    "insufficient data" even though real forward price action exists
    starting shortly after the evaluation's actual timestamp. Anchoring
    on eval_timestamp instead measures "what happened in the W minutes
    after this evaluation ran", which is what the window is actually
    supposed to mean.

    Candles strictly after reference_index are the ONLY ones used --
    this is the look-ahead boundary: reference_index itself is "now"
    (already known at rejection time); everything from
    reference_index + 1 onward is future data used exclusively to
    MEASURE what happened, never fed back into direction/category
    determination (those are already fixed by extract_expected_direction
    and categorize_reason before this function is ever called).

    If the market was closed for all or part of the requested calendar
    window (a weekend, a holiday), fewer than the "expected" number of
    M5 candles will exist in price_history for that span -- this is
    handled honestly: candles_available reports exactly how many exist,
    and insufficient_data is True (with all price-derived fields None)
    only when NO forward candle exists at all inside the window, rather
    than fabricating a "flat" 0-pip outcome for a period the market was
    simply closed.
    """

    reference_price = float(price_history.at[reference_index, "close"])
    window_end_time = eval_timestamp + timedelta(minutes=window_minutes)

    forward = price_history.iloc[reference_index + 1 :]
    in_window = forward[forward["time"] <= window_end_time]

    if in_window.empty:
        return WindowOutcome(
            window_minutes=window_minutes,
            candles_available=0,
            reference_price=reference_price,
            ending_price=float("nan"),
            net_movement_pips=float("nan"),
            movement_in_expected_direction_pips=None,
            movement_against_expected_direction_pips=None,
            mfe_price=None,
            mfe_pips=None,
            mae_price=None,
            mae_pips=None,
            insufficient_data=True,
        )

    ending_price = float(in_window["close"].iloc[-1])
    highest_high = float(in_window["high"].max())
    lowest_low = float(in_window["low"].min())

    net_movement_pips = (ending_price - reference_price) / pip_size

    movement_in_expected: Optional[float] = None
    movement_against: Optional[float] = None
    mfe_price: Optional[float] = None
    mfe_pips: Optional[float] = None
    mae_price: Optional[float] = None
    mae_pips: Optional[float] = None

    if direction == "bullish":
        signed_expected_pips = net_movement_pips
        mfe_price = highest_high
        mfe_pips = (highest_high - reference_price) / pip_size
        mae_price = lowest_low
        mae_pips = (reference_price - lowest_low) / pip_size
    elif direction == "bearish":
        signed_expected_pips = -net_movement_pips
        mfe_price = lowest_low
        mfe_pips = (reference_price - lowest_low) / pip_size
        mae_price = highest_high
        mae_pips = (highest_high - reference_price) / pip_size
    else:
        signed_expected_pips = None

    if signed_expected_pips is not None:
        movement_in_expected = max(signed_expected_pips, 0.0)
        movement_against = max(-signed_expected_pips, 0.0)

    return WindowOutcome(
        window_minutes=window_minutes,
        candles_available=len(in_window),
        reference_price=reference_price,
        ending_price=ending_price,
        net_movement_pips=net_movement_pips,
        movement_in_expected_direction_pips=movement_in_expected,
        movement_against_expected_direction_pips=movement_against,
        mfe_price=mfe_price,
        mfe_pips=mfe_pips,
        mae_price=mae_price,
        mae_pips=mae_pips,
        insufficient_data=False,
    )


@dataclass
class RAnalysis:
    available: bool
    reason_unavailable: Optional[str]
    hypothetical_entry: Optional[float] = None
    hypothetical_stop: Optional[float] = None
    r_distance_pips: Optional[float] = None
    outcome_by_multiple: dict[int, Optional[str]] = field(default_factory=dict)


def compute_r_analysis(
    record: dict[str, Any],
    price_history: pd.DataFrame,
    reference_index: int,
    eval_timestamp: datetime,
    reference_price: float,
    direction: Optional[str],
    pip_size: float,
    horizon_minutes: int,
) -> RAnalysis:
    """
    Hypothetical, diagnostic-only R-multiple outcome.

    A defensible stop distance exists in the recorded evidence ONLY
    when evidence.order_block is a confirmed block (this only happens
    for M5_LOCAL_CONFIRMATION_MISSING rejections -- every earlier gate
    rejects before an Order Block is ever confirmed, so evidence.
    order_block is {} for them, exactly as documented in the earlier
    CHoCH audit). The stop is the block's own distal_level -- the same
    level app.strategies.smc_manual_signal.py itself would use
    (stop_loss_price = order_block.distal_level). No stop is ever
    invented for any other category.

    hypothetical_entry uses the same reference_price as the rest of
    this script's outcome measurement (the M5 close at evaluation
    time), NOT a reconstruction of the strategy's own entry-validation
    logic (evaluate_entry_within_order_block) -- this is intentionally
    simplified and is not a claim that this would have been an
    executable trade.

    The search horizon is anchored on eval_timestamp, not on the
    reference candle's own timestamp -- same reasoning as
    compute_window_outcome (see that function's docstring): anchoring
    on a stale candle right after a weekend/holiday closure would
    wrongly shrink the effective search horizon to nothing.
    """

    if direction not in ("bullish", "bearish"):
        return RAnalysis(available=False, reason_unavailable="direction_unknown")

    evidence = record.get("evidence")
    order_block = evidence.get("order_block") if isinstance(evidence, dict) else None

    if not isinstance(order_block, dict) or not order_block.get("passed"):
        return RAnalysis(
            available=False,
            reason_unavailable="no_confirmed_order_block_in_evidence",
        )

    distal_level = order_block.get("distal_level")

    if not isinstance(distal_level, (int, float)):
        return RAnalysis(
            available=False,
            reason_unavailable="order_block_missing_distal_level",
        )

    entry = reference_price
    stop = float(distal_level)
    r_distance = abs(entry - stop)

    if r_distance <= 0:
        return RAnalysis(
            available=False,
            reason_unavailable="zero_or_invalid_stop_distance",
        )

    horizon_end = eval_timestamp + timedelta(minutes=horizon_minutes)
    forward = price_history.iloc[reference_index + 1 :]
    forward = forward[forward["time"] <= horizon_end]

    outcome_by_multiple: dict[int, Optional[str]] = {m: None for m in R_MULTIPLES}

    if forward.empty:
        for multiple in R_MULTIPLES:
            outcome_by_multiple[multiple] = "undetermined_within_horizon"

        return RAnalysis(
            available=True,
            reason_unavailable=None,
            hypothetical_entry=entry,
            hypothetical_stop=stop,
            r_distance_pips=r_distance / pip_size,
            outcome_by_multiple=outcome_by_multiple,
        )

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
                (high >= targets[multiple])
                if direction == "bullish"
                else (low <= targets[multiple])
            )

            # Conservative, deterministic same-candle tie-break: if both
            # the stop and this target could have been touched within
            # the same candle, the stop is assumed to have been hit
            # first (cannot be verified from OHLC alone; this is the
            # safer assumption for a diagnostic, not an optimistic one).
            if stop_hit and target_hit:
                outcome_by_multiple[multiple] = "stop_first"
                remaining.discard(multiple)
            elif stop_hit:
                outcome_by_multiple[multiple] = "stop_first"
                remaining.discard(multiple)
            elif target_hit:
                outcome_by_multiple[multiple] = "target_first"
                remaining.discard(multiple)

        if not remaining:
            break

    for multiple in remaining:
        outcome_by_multiple[multiple] = "undetermined_within_horizon"

    return RAnalysis(
        available=True,
        reason_unavailable=None,
        hypothetical_entry=entry,
        hypothetical_stop=stop,
        r_distance_pips=r_distance / pip_size,
        outcome_by_multiple=outcome_by_multiple,
    )


# ---------------------------------------------------------------------------
# Per-evaluation outcome record
# ---------------------------------------------------------------------------


@dataclass
class EvaluationOutcome:
    sequence_index: int
    timestamp: str
    status: str
    category: str
    raw_reason: str
    direction: Optional[str]
    direction_unknown: bool
    reference_price: Optional[float]
    windows: dict[int, WindowOutcome]
    r_analysis: RAnalysis
    exclusion_reason: Optional[str]


def build_evaluation_outcomes(
    records: list[dict[str, Any]],
    price_history: pd.DataFrame,
    pip_size: float,
) -> tuple[list[EvaluationOutcome], Counter[str]]:
    """
    One EvaluationOutcome per rejection record with at least one
    rejection reason (non-rejection / API_ERROR records are excluded
    here and accounted for by the caller's exclusion summary).
    """

    outcomes: list[EvaluationOutcome] = []
    exclusions: Counter[str] = Counter()

    max_window = max(OUTCOME_WINDOWS_MINUTES)

    for index, record in enumerate(records):
        status = _status_label(record)
        reasons = _reason_strings(record)

        if status == API_ERROR_STATUS_LABEL:
            exclusions["api_error"] += 1
            continue

        if not reasons:
            exclusions["no_rejection_reason_not_a_rejection"] += 1
            continue

        timestamp = _parse_timestamp(record.get("timestamp_utc"))

        if timestamp is None:
            exclusions["missing_or_unparseable_timestamp"] += 1
            continue

        raw_reason = reasons[0]
        category = categorize_reason(raw_reason)
        direction = extract_expected_direction(record)
        direction_unknown = direction is None

        reference_index = find_reference_index(price_history, timestamp)

        if reference_index is None:
            exclusions["no_matching_candle_before_timestamp"] += 1
            continue

        reference_price = float(price_history.at[reference_index, "close"])

        windows: dict[int, WindowOutcome] = {}
        any_window_has_data = False

        for window_minutes in OUTCOME_WINDOWS_MINUTES:
            outcome = compute_window_outcome(
                price_history,
                reference_index,
                timestamp,
                window_minutes,
                direction,
                pip_size=pip_size,
            )
            windows[window_minutes] = outcome

            if not outcome.insufficient_data:
                any_window_has_data = True

        if not any_window_has_data:
            exclusions["insufficient_forward_horizon"] += 1
            continue

        r_analysis = compute_r_analysis(
            record,
            price_history,
            reference_index,
            timestamp,
            reference_price,
            direction,
            pip_size=pip_size,
            horizon_minutes=max_window,
        )

        outcomes.append(
            EvaluationOutcome(
                sequence_index=index,
                timestamp=timestamp.isoformat(),
                status=status,
                category=category,
                raw_reason=raw_reason,
                direction=direction,
                direction_unknown=direction_unknown,
                reference_price=reference_price,
                windows=windows,
                r_analysis=r_analysis,
                exclusion_reason=None,
            )
        )

    return outcomes, exclusions


# ---------------------------------------------------------------------------
# Episode de-duplication
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    category: str
    direction: Optional[str]
    start_timestamp: str
    end_timestamp: str
    evaluation_count: int
    start_sequence_index: int
    start_reference_price: Optional[float]
    windows: dict[int, WindowOutcome]
    r_analysis: RAnalysis


def build_episodes(evaluations: list[EvaluationOutcome]) -> list[Episode]:
    """
    Group consecutive evaluations into one episode when:
      - the rejection category is the same, AND
      - the expected direction is the same (including both being
        "unknown" -- two unknown-direction evaluations in a row are
        still the same non-signal condition), AND
      - no more than EPISODE_MAX_GAP_MINUTES elapsed since the
        previous evaluation in the group (a longer gap -- an outage,
        weekend, or holiday close -- starts a new episode even if
        category and direction still match on the other side of it,
        since that is not a continuously-observed condition).

    Each episode's outcome (windows, R-analysis) is taken from its
    FIRST evaluation only -- the point at which this rejection
    condition first appeared -- not re-measured or averaged across
    every repeated evaluation inside the episode. This is the entire
    purpose of de-duplication: 299 identical 5-minutely observations of
    one ongoing condition must contribute one outcome measurement, not
    299 overlapping ones.
    """

    if not evaluations:
        return []

    episodes: list[Episode] = []
    current_group: list[EvaluationOutcome] = [evaluations[0]]

    def _flush(group: list[EvaluationOutcome]) -> None:
        first = group[0]
        last = group[-1]
        episodes.append(
            Episode(
                category=first.category,
                direction=first.direction,
                start_timestamp=first.timestamp,
                end_timestamp=last.timestamp,
                evaluation_count=len(group),
                start_sequence_index=first.sequence_index,
                start_reference_price=first.reference_price,
                windows=first.windows,
                r_analysis=first.r_analysis,
            )
        )

    for evaluation in evaluations[1:]:
        previous = current_group[-1]

        same_category = evaluation.category == previous.category
        same_direction = evaluation.direction == previous.direction

        gap_minutes: Optional[float] = None
        previous_ts = _parse_timestamp(previous.timestamp)
        current_ts = _parse_timestamp(evaluation.timestamp)

        if previous_ts is not None and current_ts is not None:
            gap_minutes = (current_ts - previous_ts).total_seconds() / 60.0

        within_gap_tolerance = (
            gap_minutes is not None and gap_minutes <= EPISODE_MAX_GAP_MINUTES
        )

        if same_category and same_direction and within_gap_tolerance:
            current_group.append(evaluation)
        else:
            _flush(current_group)
            current_group = [evaluation]

    _flush(current_group)

    return episodes


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None

    ordered = sorted(values)
    n = len(ordered)
    midpoint = n // 2

    if n % 2 == 1:
        return ordered[midpoint]

    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


@dataclass
class CategoryStats:
    category: str
    episode_count: int
    directional_episode_count: int
    mean_mfe_pips: dict[int, Optional[float]]
    median_mfe_pips: dict[int, Optional[float]]
    mean_mae_pips: dict[int, Optional[float]]
    median_mae_pips: dict[int, Optional[float]]
    pct_moved_ge_10_pips_expected: dict[int, Optional[float]]
    pct_moved_ge_20_pips_expected: dict[int, Optional[float]]
    pct_moved_against_first_at_60m: Optional[float]


def compute_category_stats(episodes: list[Episode]) -> dict[str, CategoryStats]:
    by_category: dict[str, list[Episode]] = defaultdict(list)

    for episode in episodes:
        by_category[episode.category].append(episode)

    stats: dict[str, CategoryStats] = {}

    for category, category_episodes in by_category.items():
        directional = [e for e in category_episodes if e.direction is not None]

        mean_mfe: dict[int, Optional[float]] = {}
        median_mfe: dict[int, Optional[float]] = {}
        mean_mae: dict[int, Optional[float]] = {}
        median_mae: dict[int, Optional[float]] = {}
        pct_ge_10: dict[int, Optional[float]] = {}
        pct_ge_20: dict[int, Optional[float]] = {}

        for window_minutes in OUTCOME_WINDOWS_MINUTES:
            mfe_values = [
                e.windows[window_minutes].mfe_pips
                for e in directional
                if window_minutes in e.windows
                and e.windows[window_minutes].mfe_pips is not None
            ]
            mae_values = [
                e.windows[window_minutes].mae_pips
                for e in directional
                if window_minutes in e.windows
                and e.windows[window_minutes].mae_pips is not None
            ]
            expected_values = [
                e.windows[window_minutes].movement_in_expected_direction_pips
                for e in directional
                if window_minutes in e.windows
                and e.windows[window_minutes].movement_in_expected_direction_pips
                is not None
            ]

            mean_mfe[window_minutes] = _mean(mfe_values)
            median_mfe[window_minutes] = _median(mfe_values)
            mean_mae[window_minutes] = _mean(mae_values)
            median_mae[window_minutes] = _median(mae_values)

            pct_ge_10[window_minutes] = (
                round(100.0 * sum(1 for v in expected_values if v >= 10) / len(expected_values), 2)
                if expected_values
                else None
            )
            pct_ge_20[window_minutes] = (
                round(100.0 * sum(1 for v in expected_values if v >= 20) / len(expected_values), 2)
                if expected_values
                else None
            )

        against_first_flags = []
        for e in directional:
            w60 = e.windows.get(60)
            if (
                w60 is not None
                and not w60.insufficient_data
                and w60.movement_against_expected_direction_pips is not None
            ):
                against_first_flags.append(
                    w60.movement_against_expected_direction_pips
                    > w60.movement_in_expected_direction_pips
                )

        pct_against_first = (
            round(100.0 * sum(against_first_flags) / len(against_first_flags), 2)
            if against_first_flags
            else None
        )

        stats[category] = CategoryStats(
            category=category,
            episode_count=len(category_episodes),
            directional_episode_count=len(directional),
            mean_mfe_pips=mean_mfe,
            median_mfe_pips=median_mfe,
            mean_mae_pips=mean_mae,
            median_mae_pips=median_mae,
            pct_moved_ge_10_pips_expected=pct_ge_10,
            pct_moved_ge_20_pips_expected=pct_ge_20,
            pct_moved_against_first_at_60m=pct_against_first,
        )

    return stats


def gate_transition_comparison(stats: dict[str, CategoryStats]) -> list[dict[str, Any]]:
    """
    Ranks categories by pipeline depth (CATEGORY_DEPTH) and reports
    each one's 60-minute mean MFE/MAE side by side, so a reader can see
    whether progressing deeper through the gates before rejecting
    corresponds to better subsequent price behaviour -- a comparison,
    not a conclusion; no threshold here recommends any change.
    """

    ordered_categories = sorted(
        stats.keys(), key=lambda c: CATEGORY_DEPTH.get(c, -1)
    )

    rows = []

    for category in ordered_categories:
        s = stats[category]
        rows.append(
            {
                "category": category,
                "depth": CATEGORY_DEPTH.get(category, -1),
                "episode_count": s.episode_count,
                "mean_mfe_pips_60m": s.mean_mfe_pips.get(60),
                "mean_mae_pips_60m": s.mean_mae_pips.get(60),
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------


def _fmt(value: Optional[float], decimals: int = 1) -> str:
    return f"{value:.{decimals}f}" if value is not None else "n/a"


def generate_console_report(
    *,
    log_path: Path,
    total_lines: int,
    malformed_line_count: int,
    duplicate_count: int,
    total_records: int,
    exclusions: Counter[str],
    evaluations: list[EvaluationOutcome],
    episodes: list[Episode],
    category_stats: dict[str, CategoryStats],
    transitions: list[dict[str, Any]],
) -> str:
    lines: list[str] = [
        REPORT_DIVIDER,
        "FORWARD TEST REJECTION OUTCOME REPORT",
        REPORT_DIVIDER,
        "",
        f"Log file: {log_path}",
        f"Total lines read: {total_lines}",
        f"Malformed lines skipped: {malformed_line_count}",
        f"Exact-duplicate records skipped: {duplicate_count}",
        "",
        f"Total evaluations (parsed records): {total_records}",
        f"Directional evaluations analysed: {sum(1 for e in evaluations if not e.direction_unknown)}",
        f"Unknown-direction evaluations (counted, excluded from directional stats): "
        f"{sum(1 for e in evaluations if e.direction_unknown)}",
        f"De-duplicated rejection episodes: {len(episodes)}",
        "",
        SECTION_DIVIDER,
        "DATA QUALITY / EXCLUSIONS",
        SECTION_DIVIDER,
    ]

    if exclusions:
        for reason, count in sorted(exclusions.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"{reason:<45} {count:>6}")
    else:
        lines.append("(none)")

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("BY REJECTION CATEGORY (de-duplicated episodes)")
    lines.append(SECTION_DIVIDER)

    for category, s in sorted(
        category_stats.items(), key=lambda kv: (-kv[1].episode_count, kv[0])
    ):
        lines.append("")
        lines.append(category)
        lines.append(f"Episodes: {s.episode_count}  (directional: {s.directional_episode_count})")
        lines.append(f"Average MFE 60m: {_fmt(s.mean_mfe_pips.get(60))} pips")
        lines.append(f"Average MAE 60m: {_fmt(s.mean_mae_pips.get(60))} pips")
        lines.append(f"Median MFE 60m:  {_fmt(s.median_mfe_pips.get(60))} pips")
        lines.append(f"Median MAE 60m:  {_fmt(s.median_mae_pips.get(60))} pips")
        lines.append(f"% moved >= 10 pips expected direction (60m): {_fmt(s.pct_moved_ge_10_pips_expected.get(60), 1)}%")
        lines.append(f"% moved >= 20 pips expected direction (60m): {_fmt(s.pct_moved_ge_20_pips_expected.get(60), 1)}%")
        lines.append(f"% moved against expected direction first (60m): {_fmt(s.pct_moved_against_first_at_60m, 1)}%")

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("GATE TRANSITION COMPARISON (by pipeline depth, 60m window)")
    lines.append(SECTION_DIVIDER)

    for row in transitions:
        lines.append(
            f"[depth {row['depth']}] {row['category']:<30} "
            f"episodes={row['episode_count']:<4} "
            f"mean_MFE={_fmt(row['mean_mfe_pips_60m']):>6}p "
            f"mean_MAE={_fmt(row['mean_mae_pips_60m']):>6}p"
        )

    lines.append("")
    lines.append(SECTION_DIVIDER)
    lines.append("SUMMARY COMPARISONS")
    lines.append(SECTION_DIVIDER)

    ranked_by_low_mae = sorted(
        (s for s in category_stats.values() if s.mean_mae_pips.get(60) is not None),
        key=lambda s: s.mean_mae_pips[60],
    )
    ranked_by_high_mfe = sorted(
        (s for s in category_stats.values() if s.mean_mfe_pips.get(60) is not None),
        key=lambda s: -s.mean_mfe_pips[60],
    )
    deepest_with_data = max(
        (row for row in transitions if row["mean_mfe_pips_60m"] is not None),
        key=lambda row: row["depth"],
        default=None,
    )

    if ranked_by_low_mae:
        best = ranked_by_low_mae[0]
        lines.append(
            f"BEST FILTER AT AVOIDING BAD TRADES (lowest mean MAE 60m): "
            f"{best.category} ({_fmt(best.mean_mae_pips[60])} pips)"
        )
    else:
        lines.append("BEST FILTER AT AVOIDING BAD TRADES: insufficient data")

    if ranked_by_high_mfe:
        missed = ranked_by_high_mfe[0]
        lines.append(
            f"FILTER MOST ASSOCIATED WITH MISSED MOVES (highest mean MFE 60m): "
            f"{missed.category} ({_fmt(missed.mean_mfe_pips[60])} pips)"
        )
    else:
        lines.append("FILTER MOST ASSOCIATED WITH MISSED MOVES: insufficient data")

    if deepest_with_data is not None:
        lines.append(
            f"DEEPEST GATE REACHED BEFORE PROFITABLE MOVES (with data): "
            f"{deepest_with_data['category']} (depth {deepest_with_data['depth']})"
        )
    else:
        lines.append("DEEPEST GATE REACHED BEFORE PROFITABLE MOVES: insufficient data")

    lines.append("")
    lines.append(
        "NOTE: these are observational statistics only. No strategy "
        "parameter is recommended or changed based on this report."
    )
    lines.append(REPORT_DIVIDER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON / CSV output
# ---------------------------------------------------------------------------


def _window_to_dict(window: WindowOutcome) -> dict[str, Any]:
    return {
        "window_minutes": window.window_minutes,
        "candles_available": window.candles_available,
        "insufficient_data": window.insufficient_data,
        "reference_price": window.reference_price,
        "ending_price": window.ending_price,
        "net_movement_pips": window.net_movement_pips,
        "movement_in_expected_direction_pips": window.movement_in_expected_direction_pips,
        "movement_against_expected_direction_pips": window.movement_against_expected_direction_pips,
        "mfe_pips": window.mfe_pips,
        "mae_pips": window.mae_pips,
    }


def _r_analysis_to_dict(r: RAnalysis) -> dict[str, Any]:
    return {
        "available": r.available,
        "reason_unavailable": r.reason_unavailable,
        "hypothetical_entry": r.hypothetical_entry,
        "hypothetical_stop": r.hypothetical_stop,
        "r_distance_pips": r.r_distance_pips,
        "outcome_by_multiple": r.outcome_by_multiple,
    }


def build_json_payload(
    *,
    log_path: Path,
    total_lines: int,
    malformed_line_count: int,
    duplicate_count: int,
    exclusions: Counter[str],
    evaluations: list[EvaluationOutcome],
    episodes: list[Episode],
    category_stats: dict[str, CategoryStats],
    transitions: list[dict[str, Any]],
    pip_size: float,
) -> dict[str, Any]:
    return {
        "log_file": str(log_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pip_size_used": pip_size,
        "outcome_windows_minutes": list(OUTCOME_WINDOWS_MINUTES),
        "episode_max_gap_minutes": EPISODE_MAX_GAP_MINUTES,
        "total_lines_read": total_lines,
        "malformed_line_count": malformed_line_count,
        "duplicate_record_count": duplicate_count,
        "exclusion_counts": dict(exclusions),
        "total_evaluations_analysed": len(evaluations),
        "directional_evaluations": sum(1 for e in evaluations if not e.direction_unknown),
        "unknown_direction_evaluations": sum(1 for e in evaluations if e.direction_unknown),
        "episode_count": len(episodes),
        "category_stats": {
            category: {
                "episode_count": s.episode_count,
                "directional_episode_count": s.directional_episode_count,
                "mean_mfe_pips": s.mean_mfe_pips,
                "median_mfe_pips": s.median_mfe_pips,
                "mean_mae_pips": s.mean_mae_pips,
                "median_mae_pips": s.median_mae_pips,
                "pct_moved_ge_10_pips_expected": s.pct_moved_ge_10_pips_expected,
                "pct_moved_ge_20_pips_expected": s.pct_moved_ge_20_pips_expected,
                "pct_moved_against_first_at_60m": s.pct_moved_against_first_at_60m,
            }
            for category, s in category_stats.items()
        },
        "gate_transition_comparison": transitions,
        "episodes": [
            {
                "category": ep.category,
                "direction": ep.direction,
                "start_timestamp": ep.start_timestamp,
                "end_timestamp": ep.end_timestamp,
                "evaluation_count": ep.evaluation_count,
                "start_reference_price": ep.start_reference_price,
                "windows": {
                    str(minutes): _window_to_dict(window)
                    for minutes, window in ep.windows.items()
                },
                "r_analysis": _r_analysis_to_dict(ep.r_analysis),
            }
            for ep in episodes
        ],
    }


def write_json(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


OUTCOMES_CSV_FIELDNAMES = (
    "timestamp",
    "status",
    "category",
    "raw_reason",
    "direction",
    "direction_unknown",
    "reference_price",
    "mfe_pips_60m",
    "mae_pips_60m",
    "net_movement_pips_60m",
    "r_available",
    "r_distance_pips",
    "r_outcome_1r",
    "r_outcome_2r",
    "r_outcome_3r",
)


def write_outcomes_csv(evaluations: list[EvaluationOutcome], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTCOMES_CSV_FIELDNAMES)
        writer.writeheader()

        for evaluation in evaluations:
            w60 = evaluation.windows.get(60)
            writer.writerow(
                {
                    "timestamp": evaluation.timestamp,
                    "status": evaluation.status,
                    "category": evaluation.category,
                    "raw_reason": evaluation.raw_reason,
                    "direction": evaluation.direction or "",
                    "direction_unknown": evaluation.direction_unknown,
                    "reference_price": evaluation.reference_price,
                    "mfe_pips_60m": w60.mfe_pips if w60 else "",
                    "mae_pips_60m": w60.mae_pips if w60 else "",
                    "net_movement_pips_60m": w60.net_movement_pips if w60 else "",
                    "r_available": evaluation.r_analysis.available,
                    "r_distance_pips": evaluation.r_analysis.r_distance_pips,
                    "r_outcome_1r": evaluation.r_analysis.outcome_by_multiple.get(1),
                    "r_outcome_2r": evaluation.r_analysis.outcome_by_multiple.get(2),
                    "r_outcome_3r": evaluation.r_analysis.outcome_by_multiple.get(3),
                }
            )


EPISODES_CSV_FIELDNAMES = (
    "start_timestamp",
    "end_timestamp",
    "evaluation_count",
    "category",
    "direction",
    "start_reference_price",
    "mfe_pips_60m",
    "mae_pips_60m",
    "mfe_pips_240m",
    "mae_pips_240m",
    "r_available",
    "r_outcome_1r",
    "r_outcome_2r",
    "r_outcome_3r",
)


def write_episodes_csv(episodes: list[Episode], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EPISODES_CSV_FIELDNAMES)
        writer.writeheader()

        for episode in episodes:
            w60 = episode.windows.get(60)
            w240 = episode.windows.get(240)
            writer.writerow(
                {
                    "start_timestamp": episode.start_timestamp,
                    "end_timestamp": episode.end_timestamp,
                    "evaluation_count": episode.evaluation_count,
                    "category": episode.category,
                    "direction": episode.direction or "",
                    "start_reference_price": episode.start_reference_price,
                    "mfe_pips_60m": w60.mfe_pips if w60 else "",
                    "mae_pips_60m": w60.mae_pips if w60 else "",
                    "mfe_pips_240m": w240.mfe_pips if w240 else "",
                    "mae_pips_240m": w240.mae_pips if w240 else "",
                    "r_available": episode.r_analysis.available,
                    "r_outcome_1r": episode.r_analysis.outcome_by_multiple.get(1),
                    "r_outcome_2r": episode.r_analysis.outcome_by_multiple.get(2),
                    "r_outcome_3r": episode.r_analysis.outcome_by_multiple.get(3),
                }
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome diagnostics for SMC forward-test rejections: what did "
            "price do after each rejection? Read-only; never modifies the "
            "log or any strategy/trading logic."
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
        help=f"Path to write the JSON analysis (default: {DEFAULT_JSON_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--outcomes-csv",
        default=str(DEFAULT_OUTCOMES_CSV_PATH),
        help=f"Path to write the per-evaluation outcomes CSV (default: {DEFAULT_OUTCOMES_CSV_PATH}).",
    )
    parser.add_argument(
        "--episodes-csv",
        default=str(DEFAULT_EPISODES_CSV_PATH),
        help=f"Path to write the de-duplicated episodes CSV (default: {DEFAULT_EPISODES_CSV_PATH}).",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    args = _parse_args(argv)

    log_path = Path(args.log_file)
    json_output_path = Path(args.json_output)
    outcomes_csv_path = Path(args.outcomes_csv)
    episodes_csv_path = Path(args.episodes_csv)

    raw_records, malformed_line_count = load_records(log_path)
    records, duplicate_count = deduplicate_exact_records(raw_records)

    connect_mt5()

    try:
        pip_size = determine_pip_size(SYMBOL)

        timestamps = [
            ts
            for record in records
            if (ts := _parse_timestamp(record.get("timestamp_utc"))) is not None
        ]

        if not timestamps:
            print("No timestamped records found in the log; nothing to analyse.")
            disconnect_mt5()
            return

        price_history = fetch_price_history(min(timestamps), max(timestamps))

        evaluations, exclusions = build_evaluation_outcomes(
            records, price_history, pip_size
        )
    finally:
        disconnect_mt5()

    episodes = build_episodes(evaluations)
    category_stats = compute_category_stats(episodes)
    transitions = gate_transition_comparison(category_stats)

    report = generate_console_report(
        log_path=log_path,
        total_lines=len(raw_records) + malformed_line_count,
        malformed_line_count=malformed_line_count,
        duplicate_count=duplicate_count,
        total_records=len(records),
        exclusions=exclusions,
        evaluations=evaluations,
        episodes=episodes,
        category_stats=category_stats,
        transitions=transitions,
    )

    print(report)

    payload = build_json_payload(
        log_path=log_path,
        total_lines=len(raw_records) + malformed_line_count,
        malformed_line_count=malformed_line_count,
        duplicate_count=duplicate_count,
        exclusions=exclusions,
        evaluations=evaluations,
        episodes=episodes,
        category_stats=category_stats,
        transitions=transitions,
        pip_size=pip_size,
    )
    write_json(payload, json_output_path)
    write_outcomes_csv(evaluations, outcomes_csv_path)
    write_episodes_csv(episodes, episodes_csv_path)

    print(f"\nJSON analysis written to:   {json_output_path}")
    print(f"Outcomes CSV written to:    {outcomes_csv_path}")
    print(f"Episodes CSV written to:    {episodes_csv_path}")


if __name__ == "__main__":
    main()
