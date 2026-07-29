from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

import pandas as pd

from app.analysis.candle_validation import validate_and_normalize_candles
from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import detect_swing_points
from app.analysis.models import (
    AnalysisResult,
    MarketEvent,
    StructureSnapshot,
)
from app.analysis.order_blocks import detect_order_blocks
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators


def _validate_parameters(
    *,
    symbol: str,
    timeframe: str,
) -> None:
    """
    Validate the symbol/timeframe parameters supplied to the analysis
    engine.

    Candle-data validation and normalisation is handled separately by
    app.analysis.candle_validation.validate_and_normalize_candles —
    the single, pipeline-independent candle-data-hygiene entry point
    for this codebase (SMC_SPECIFICATION.md, §3, Decision A).
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(
            "symbol must be a non-empty string."
        )

    if not isinstance(timeframe, str) or not timeframe.strip():
        raise ValueError(
            "timeframe must be a non-empty string."
        )


def _optional_float(
    value: Any,
) -> Optional[float]:
    """
    Convert a value to float, returning None for missing data.
    """
    if value is None or pd.isna(value):
        return None

    return float(value)


def _optional_text(
    value: Any,
) -> Optional[str]:
    """
    Convert a value to text, returning None for missing data.
    """
    if value is None or pd.isna(value):
        return None

    return str(value)


def _optional_datetime(
    value: Any,
) -> Optional[datetime]:
    """
    Convert a pandas or Python datetime value to UTC.
    """
    if value is None or pd.isna(value):
        return None

    timestamp = pd.Timestamp(value)

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    return timestamp.to_pydatetime()


def _first_existing_value(
    row: pd.Series,
    *column_names: str,
) -> Any:
    """
    Return the first available non-missing column value.
    """
    for column_name in column_names:
        if column_name not in row.index:
            continue

        value = row[column_name]

        if not pd.isna(value):
            return value

    return None


def _build_structure_events(
    structure_dataframe: pd.DataFrame,
) -> list[MarketEvent]:
    """
    Convert state-machine events into MarketEvent objects.

    Supported structure events:

    - BOS
    - MSS
    - MSS_INVALIDATED
    - CHoCH
    """
    required_columns = {
        "time",
        "structure_event",
        "event_direction",
    }

    missing_columns = required_columns.difference(
        structure_dataframe.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))

        raise ValueError(
            "Cannot build structure events. Missing columns: "
            f"{missing}"
        )

    events: list[MarketEvent] = []
    event_number = 0

    # Decision #6 (§19): maps each MSS occurrence's origin position to
    # its own MarketEvent.event_id, built incrementally in row order —
    # an MSS always precedes its own eventual invalidation (if any),
    # so a forward-only pass is sufficient; no look-ahead required.
    mss_event_id_by_origin_position: dict[int, str] = {}

    for position, (_, row) in enumerate(
        structure_dataframe.iterrows()
    ):
        event_type = _optional_text(
            row.get("structure_event")
        )

        if event_type not in {
            "BOS",
            "MSS",
            "MSS_INVALIDATED",
            "CHoCH",
        }:
            continue

        direction = _optional_text(
            row.get("event_direction")
        )

        if direction not in {
            "bullish",
            "bearish",
            "neutral",
        }:
            direction = "neutral"

        event_time = _optional_datetime(
            row.get("time")
        )

        if event_time is None:
            continue

        event_number += 1

        broken_level = _optional_float(
            row.get("broken_level")
        )

        metadata: dict[str, Any] = {}

        optional_metadata_columns = {
            "break_distance": "break_distance",
            "required_break_distance":
                "required_break_distance",
            "trend_before_event": "trend_before",
            "trend_after_event": "trend_after",
            "state_before_event": "state_before",
            "state_after_event": "state_after",
            "mss_confirmation_step":
                "mss_confirmation_step",
            "mss_origin_level": "mss_origin_level",
        }

        for (
            column_name,
            metadata_name,
        ) in optional_metadata_columns.items():
            if column_name not in row.index:
                continue

            value = row[column_name]

            if pd.isna(value):
                continue

            if column_name in {
                "break_distance",
                "required_break_distance",
                "mss_origin_level",
            }:
                metadata[metadata_name] = float(value)
            else:
                metadata[metadata_name] = str(value)

        if event_type == "MSS_INVALIDATED":
            # Decision #6 (§19): the join key back to the originating
            # MSS occurrence, plus that occurrence's own event_id when
            # it was itself built into a MarketEvent (it always is,
            # since MSS is always an accepted event_type here).
            origin_index_value = row.get(
                "mss_invalidated_origin_index"
            )

            if not pd.isna(origin_index_value):
                origin_position = int(origin_index_value)
                metadata["mss_origin_index"] = origin_position

                origin_event_id = mss_event_id_by_origin_position.get(
                    origin_position
                )

                if origin_event_id is not None:
                    metadata["mss_origin_event_id"] = origin_event_id

        # Decision #13 (§29): strength = break_distance /
        # required_break_distance, populated whenever both inputs are
        # available (break_distance and required_break_distance
        # remain present in metadata alongside it, per the decision's
        # own requirement — untouched above). Stays None wherever the
        # ratio is not computable: MSS_INVALIDATED and CHoCH never set
        # break_distance in state_machine.py (it is only ever produced
        # by the close-driven MSS/BOS checks), so this falls through
        # to None for them naturally, without a type-specific branch
        # here. Also guarded against a required_break_distance of
        # exactly 0 (e.g. minimum_break_atr configured to 0), which
        # the specification does not address and which would
        # otherwise raise ZeroDivisionError.
        strength: Optional[float] = None

        break_distance_value = metadata.get("break_distance")
        required_break_distance_value = metadata.get(
            "required_break_distance"
        )

        if (
            break_distance_value is not None
            and required_break_distance_value is not None
            and required_break_distance_value != 0
        ):
            strength = (
                break_distance_value / required_break_distance_value
            )

        descriptions = {
            "BOS": (
                f"{direction.capitalize()} continuation "
                "Break of Structure confirmed."
            ),
            "MSS": (
                f"{direction.capitalize()} Market Structure "
                "Shift detected."
            ),
            "MSS_INVALIDATED": (
                "Market Structure Shift invalidated; trend "
                f"reasserted as {direction}."
            ),
            "CHoCH": (
                f"{direction.capitalize()} Change of Character "
                "confirmed."
            ),
        }

        event_id = f"EV_STR_{event_number:05d}"

        if event_type == "MSS":
            mss_event_id_by_origin_position[position] = event_id

        events.append(
            MarketEvent(
                event_id=event_id,
                event_type=event_type,
                time=event_time,
                index=position,
                direction=direction,
                price=_optional_float(row.get("close")),
                broken_level=broken_level,
                strength=strength,
                source_type="MARKET_STRUCTURE",
                description=descriptions[event_type],
                metadata=metadata,
            )
        )

    return events


def _build_structure_snapshot(
    dataframe: pd.DataFrame,
) -> Optional[StructureSnapshot]:
    """
    Build a snapshot of the latest active market structure.

    Current trend, state, swing levels, and protected levels are taken from
    the final analysed candle. The latest structure event is taken from the
    most recent non-empty BOS, MSS, or CHoCH row, so the snapshot does not
    incorrectly report ``None`` merely because the final candle has no event.
    """
    if dataframe.empty:
        return None

    latest_position = len(dataframe) - 1
    latest_row = dataframe.iloc[latest_position]

    external_trend = _optional_text(
        _first_existing_value(
            latest_row,
            "external_trend",
            "trend_after_event",
        )
    ) or "neutral"

    if external_trend not in {
        "bullish",
        "bearish",
        "neutral",
    }:
        external_trend = "neutral"

    structure_state = _optional_text(
        _first_existing_value(
            latest_row,
            "structure_state",
            "external_state",
        )
    ) or external_trend

    valid_states = {
        "neutral",
        "bullish",
        "bearish",
        "mss_bullish",
        "mss_bearish",
    }

    if structure_state not in valid_states:
        structure_state = external_trend

    latest_event: Optional[str] = None
    latest_event_direction: Optional[str] = None
    latest_event_row: Optional[pd.Series] = None
    latest_event_position: Optional[int] = None

    event_column: Optional[str] = None
    direction_column: Optional[str] = None

    if "structure_event" in dataframe.columns:
        event_column = "structure_event"

    if "event_direction" in dataframe.columns:
        direction_column = "event_direction"

    if event_column is not None:
        for position in range(len(dataframe) - 1, -1, -1):
            candidate_row = dataframe.iloc[position]
            candidate_event = _optional_text(
                candidate_row.get(event_column)
            )

            if candidate_event not in {"BOS", "MSS", "CHoCH"}:
                continue

            latest_event_position = position
            latest_event_row = candidate_row
            latest_event = candidate_event

            if direction_column is not None:
                latest_event_direction = _optional_text(
                    candidate_row.get(direction_column)
                )

            break

    if latest_event_direction not in {
        "bullish",
        "bearish",
        "neutral",
        None,
    }:
        latest_event_direction = None

    metadata: dict[str, Any] = {
        "source": "state_machine",
    }

    metadata_fields = {
        "mss_origin_level": "mss_origin_level",
        "mss_confirmation_step":
            "mss_confirmation_step",
        "broken_level": "latest_broken_level",
        "break_distance": "latest_break_distance",
        "required_break_distance":
            "latest_required_break_distance",
    }

    for column_name, metadata_name in metadata_fields.items():
        value = _first_existing_value(
            latest_row,
            column_name,
        )

        if value is None:
            continue

        if column_name in {
            "mss_origin_level",
            "broken_level",
            "break_distance",
            "required_break_distance",
        }:
            metadata[metadata_name] = float(value)
        else:
            metadata[metadata_name] = str(value)

    if latest_event_row is not None:
        metadata["latest_event_index"] = latest_event_position

        latest_event_time = _optional_datetime(
            latest_event_row.get("time")
        )
        if latest_event_time is not None:
            metadata["latest_event_time"] = (
                latest_event_time.isoformat()
            )

        latest_event_broken_level = _optional_float(
            latest_event_row.get("broken_level")
        )
        if latest_event_broken_level is not None:
            metadata["latest_event_broken_level"] = (
                latest_event_broken_level
            )

        latest_event_price = _optional_float(
            latest_event_row.get("close")
        )
        if latest_event_price is not None:
            metadata["latest_event_price"] = latest_event_price

    return StructureSnapshot(
        external_trend=external_trend,
        structure_state=structure_state,
        latest_swing_high=_optional_float(
            _first_existing_value(
                latest_row,
                "latest_swing_high",
            )
        ),
        latest_swing_low=_optional_float(
            _first_existing_value(
                latest_row,
                "latest_swing_low",
            )
        ),
        protected_high=_optional_float(
            _first_existing_value(
                latest_row,
                "protected_high",
            )
        ),
        protected_low=_optional_float(
            _first_existing_value(
                latest_row,
                "protected_low",
            )
        ),
        latest_event=latest_event,
        latest_event_direction=latest_event_direction,
        updated_time=_optional_datetime(
            _first_existing_value(
                latest_row,
                "time",
            )
        ),
        updated_index=latest_position,
        metadata=metadata,
    )

def _sort_events(
    events: list[MarketEvent],
) -> list[MarketEvent]:
    """
    Return events in deterministic chronological order.
    """
    return sorted(
        events,
        key=lambda event: (
            event.time,
            event.index,
            event.event_id,
        ),
    )


def analyze_market(
    *,
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,
    swing_options: Optional[Mapping[str, Any]] = None,
    structure_options: Optional[Mapping[str, Any]] = None,
    liquidity_options: Optional[Mapping[str, Any]] = None,
    order_block_options: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> AnalysisResult:
    """
    Run the complete institutional market-analysis pipeline.

    Pipeline
    --------

    Raw OHLC candles
        -> Technical indicators
        -> Confirmed swing points
        -> Unified per-cycle HH/HL/LH/LL classification and
           BOS/MSS/CHoCH state machine (SMC_SPECIFICATION.md §7,
           Decision #3: a single forward pass, not two separate steps)
        -> Liquidity detection and registry
        -> Order Block detection and registry
        -> Unified market-event stream
        -> StructureSnapshot
        -> AnalysisResult

    Parameters
    ----------
    symbol:
        Trading symbol, for example EURUSD.

    timeframe:
        Candle timeframe, for example M15.

    candles:
        Raw candle DataFrame containing at least:

        - time
        - open
        - high
        - low
        - close

    swing_options:
        Optional arguments passed to detect_swing_points.

        Example:

        {
            "left_bars": 3,
            "right_bars": 3,
        }

    structure_options:
        Optional arguments passed to detect_structure_state.

        Example:

        {
            "atr_column": "atr14",
            "minimum_break_atr": 0.10,
        }

    liquidity_options:
        Optional arguments passed to detect_liquidity_registry.

    order_block_options:
        Optional arguments passed to detect_order_blocks.

    metadata:
        Optional user-defined metadata added to AnalysisResult.
    """
    _validate_parameters(
        symbol=symbol,
        timeframe=timeframe,
    )

    prepared_candles = validate_and_normalize_candles(candles)

    swing_kwargs = {
        "left_bars": 3,
        "right_bars": 3,
    }

    if swing_options is not None:
        swing_kwargs.update(dict(swing_options))

    structure_kwargs = {
        "atr_column": "atr14",
        "minimum_break_atr": 0.10,
    }

    if structure_options is not None:
        structure_kwargs.update(
            dict(structure_options)
        )

    liquidity_kwargs = dict(
        liquidity_options or {}
    )

    order_block_kwargs = dict(
        order_block_options or {}
    )

    # ---------------------------------------------------------
    # 1. Technical indicators
    # ---------------------------------------------------------

    indicator_dataframe = calculate_indicators(
        prepared_candles
    )

    # ---------------------------------------------------------
    # 2. Confirmed swing points
    # ---------------------------------------------------------

    swing_dataframe = detect_swing_points(
        indicator_dataframe,
        **swing_kwargs,
    )

    # ---------------------------------------------------------
    # 3. Institutional structure state machine
    # ---------------------------------------------------------
    #
    # Decision #3 (SMC_SPECIFICATION.md §7): classification is no
    # longer a separate prior pass. detect_structure_state now
    # performs per-trend-cycle HH/HL/LH/LL classification itself, in
    # the same unified forward pass as state-transition detection —
    # §7 INVARIANT point 5 requires this, and point 6 prohibits a
    # separate classification pass followed by reclassification. The
    # legacy, globally-scoped classify_market_structure remains
    # unchanged and continues to serve only the legacy
    # /analysis/market-structure pipeline (main.py), per §7 point 7.

    structure_dataframe = detect_structure_state(
        swing_dataframe,
        **structure_kwargs,
    )

    # ---------------------------------------------------------
    # 4. Structure event construction
    # ---------------------------------------------------------

    structure_events = _build_structure_events(
        structure_dataframe
    )

    # ---------------------------------------------------------
    # 5. Liquidity detection and registry
    # ---------------------------------------------------------

    (
        liquidity_dataframe,
        liquidity_registry,
        liquidity_events,
    ) = detect_liquidity_registry(
        structure_dataframe,
        **liquidity_kwargs,
    )

    # ---------------------------------------------------------
    # 6. Order Block detection and registry
    # ---------------------------------------------------------

    (
        order_block_dataframe,
        order_block_registry,
        order_block_events,
    ) = detect_order_blocks(
        liquidity_dataframe,
        **order_block_kwargs,
    )

    # ---------------------------------------------------------
    # 7. Unified event stream
    # ---------------------------------------------------------

    all_events = _sort_events(
        [
            *structure_events,
            *liquidity_events,
            *order_block_events,
        ]
    )

    # ---------------------------------------------------------
    # 8. Latest structure snapshot
    # ---------------------------------------------------------

    structure_snapshot = _build_structure_snapshot(
        order_block_dataframe
    )

    # ---------------------------------------------------------
    # 9. Result metadata
    # ---------------------------------------------------------

    result_metadata: dict[str, Any] = {
        "engine": "analysis_engine",
        # SMC_SPECIFICATION.md §33, "APPROVED SPEC — recorded per
        # Decision #12": implementing Decision #12 (§28) "requires a
        # MAJOR pipeline_version increment on implementation... not
        # left as an implementation-time judgment call." Per standard
        # semver (the scheme §33 itself follows), a MAJOR bump resets
        # MINOR/PATCH to zero regardless of what accumulated since the
        # prior "2.0.0" baseline (which was never itself incremented
        # across Phases 1-5) — so the exact required value is 3.0.0,
        # not a running count of the intervening additive changes.
        "pipeline_version": "3.0.0",
        "input_candle_count": len(candles),
        "processed_candle_count": len(
            order_block_dataframe
        ),
        "swing_high_count": int(
            swing_dataframe["swing_high"].sum()
        ),
        "swing_low_count": int(
            swing_dataframe["swing_low"].sum()
        ),
        "structure_event_count": len(
            structure_events
        ),
        "liquidity_event_count": len(
            liquidity_events
        ),
        "order_block_event_count": len(
            order_block_events
        ),
        "total_event_count": len(all_events),
        "active_liquidity_count": len(
            liquidity_registry.active()
        ),
        "active_order_block_count": len(
            order_block_registry.active()
        ),
        "swing_options": swing_kwargs,
        "structure_options": structure_kwargs,
        "liquidity_options": liquidity_kwargs,
        "order_block_options": order_block_kwargs,
    }

    if metadata is not None:
        result_metadata.update(dict(metadata))

    return AnalysisResult(
        symbol=symbol.strip().upper(),
        timeframe=timeframe.strip().upper(),
        candles=prepared_candles,
        structure=order_block_dataframe,
        liquidity_dataframe=liquidity_dataframe,
        events=all_events,
        liquidity=liquidity_registry.all(),
        order_blocks=order_block_registry.all(),
        structure_snapshot=structure_snapshot,
        metadata=result_metadata,
    )