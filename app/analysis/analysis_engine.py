from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

import pandas as pd

from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import (
    classify_market_structure,
    detect_swing_points,
)
from app.analysis.models import (
    AnalysisResult,
    MarketEvent,
    StructureSnapshot,
)
from app.analysis.order_blocks import detect_order_blocks
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators


_REQUIRED_OHLC_COLUMNS = {
    "time",
    "open",
    "high",
    "low",
    "close",
}

_NUMERIC_OHLC_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
}


def _validate_input(
    candles: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
) -> None:
    """
    Validate raw candle data supplied to the analysis engine.
    """
    if not isinstance(candles, pd.DataFrame):
        raise TypeError("candles must be a pandas DataFrame.")

    if candles.empty:
        raise ValueError("candles cannot be empty.")

    missing_columns = _REQUIRED_OHLC_COLUMNS.difference(
        candles.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))

        raise ValueError(
            f"Missing required candle columns: {missing}"
        )

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(
            "symbol must be a non-empty string."
        )

    if not isinstance(timeframe, str) or not timeframe.strip():
        raise ValueError(
            "timeframe must be a non-empty string."
        )

    if candles.index.has_duplicates:
        raise ValueError(
            "candles index must not contain duplicate values."
        )


def _prepare_candles(
    candles: pd.DataFrame,
) -> pd.DataFrame:
    """
    Normalise and validate raw OHLC candle data.

    Processing includes:

    - Copying the source DataFrame
    - Converting time values to UTC
    - Converting OHLC values to numeric values
    - Sorting candles chronologically
    - Resetting the index to a positional RangeIndex
    - Validating candle price relationships
    """
    result = candles.copy()

    # MT5 returns Unix timestamps in seconds. Preserve support for
    # already-parsed datetime values and datetime strings as well.
    if pd.api.types.is_numeric_dtype(result["time"]):
        result["time"] = pd.to_datetime(
            result["time"],
            unit="s",
            errors="coerce",
            utc=True,
        )
    else:
        result["time"] = pd.to_datetime(
            result["time"],
            errors="coerce",
            utc=True,
        )

    invalid_time_mask = result["time"].isna()

    if invalid_time_mask.any():
        invalid_rows = result.index[
            invalid_time_mask
        ].tolist()

        raise ValueError(
            "The time column contains invalid datetime values "
            f"at indexes {invalid_rows[:10]}."
        )

    for column_name in _NUMERIC_OHLC_COLUMNS:
        result[column_name] = pd.to_numeric(
            result[column_name],
            errors="coerce",
        )

        invalid_numeric_mask = result[column_name].isna()

        if invalid_numeric_mask.any():
            invalid_rows = result.index[
                invalid_numeric_mask
            ].tolist()

            raise ValueError(
                f"The {column_name!r} column contains invalid "
                f"numeric values at indexes {invalid_rows[:10]}."
            )

    if "volume" in result.columns:
        result["volume"] = pd.to_numeric(
            result["volume"],
            errors="coerce",
        )

    result = (
        result.sort_values("time", kind="stable")
        .reset_index(drop=True)
    )

    if result["time"].duplicated().any():
        duplicate_times = (
            result.loc[
                result["time"].duplicated(keep=False),
                "time",
            ]
            .astype(str)
            .head(10)
            .tolist()
        )

        raise ValueError(
            "candles contains duplicate timestamps: "
            f"{duplicate_times}."
        )

    invalid_high_mask = (
        (result["high"] < result["open"])
        | (result["high"] < result["close"])
        | (result["high"] < result["low"])
    )

    if invalid_high_mask.any():
        invalid_rows = result.index[
            invalid_high_mask
        ].tolist()

        raise ValueError(
            "Candle high must be greater than or equal to "
            "open, close and low. Invalid rows: "
            f"{invalid_rows[:10]}."
        )

    invalid_low_mask = (
        (result["low"] > result["open"])
        | (result["low"] > result["close"])
        | (result["low"] > result["high"])
    )

    if invalid_low_mask.any():
        invalid_rows = result.index[
            invalid_low_mask
        ].tolist()

        raise ValueError(
            "Candle low must be less than or equal to "
            "open, close and high. Invalid rows: "
            f"{invalid_rows[:10]}."
        )

    return result


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

    for position, (_, row) in enumerate(
        structure_dataframe.iterrows()
    ):
        event_type = _optional_text(
            row.get("structure_event")
        )

        if event_type not in {"BOS", "MSS", "CHoCH"}:
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

        descriptions = {
            "BOS": (
                f"{direction.capitalize()} continuation "
                "Break of Structure confirmed."
            ),
            "MSS": (
                f"{direction.capitalize()} Market Structure "
                "Shift detected."
            ),
            "CHoCH": (
                f"{direction.capitalize()} Change of Character "
                "confirmed."
            ),
        }

        events.append(
            MarketEvent(
                event_id=f"EV_STR_{event_number:05d}",
                event_type=event_type,
                time=event_time,
                index=position,
                direction=direction,
                price=_optional_float(row.get("close")),
                broken_level=broken_level,
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
            "advanced_trend",
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
            "advanced_trend",
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
    elif "advanced_event" in dataframe.columns:
        event_column = "advanced_event"

    if "event_direction" in dataframe.columns:
        direction_column = "event_direction"
    elif "advanced_direction" in dataframe.columns:
        direction_column = "advanced_direction"

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
        -> HH, HL, LH and LL classification
        -> BOS, MSS and CHoCH state machine
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
    _validate_input(
        candles,
        symbol=symbol,
        timeframe=timeframe,
    )

    prepared_candles = _prepare_candles(candles)

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
    # 3. Swing structure classification
    # ---------------------------------------------------------

    classified_dataframe = classify_market_structure(
        swing_dataframe
    )

    # ---------------------------------------------------------
    # 4. Institutional structure state machine
    # ---------------------------------------------------------

    structure_dataframe = detect_structure_state(
        classified_dataframe,
        **structure_kwargs,
    )

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
        "pipeline_version": "2.0.0",
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