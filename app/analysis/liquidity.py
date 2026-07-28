from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.analysis.liquidity_registry import LiquidityRegistry
from app.analysis.models import LiquidityPool, MarketEvent


def _safe_float(value: Any) -> float | None:
    """
    Convert a value to float safely.
    """

    if pd.isna(value):
        return None

    return float(value)


def _safe_datetime(value: Any) -> datetime:
    """
    Convert pandas or Python datetime values to datetime.
    """

    timestamp = pd.Timestamp(value)

    return timestamp.to_pydatetime()


def detect_liquidity_registry(
    classified: pd.DataFrame,
    tolerance_pips: float = 5.0,
    pip_size: float = 0.0001,
    minimum_sweep_pips: float = 0.5,
    break_confirmation_pips: float = 0.5,
    maximum_age_bars: int | None = None,
) -> tuple[pd.DataFrame, LiquidityRegistry, list[MarketEvent]]:
    """
    Detect and track multiple liquidity pools.

    Returns:
        enriched_dataframe
        liquidity_registry
        liquidity_events
    """

    required_columns = {
        "time",
        "high",
        "low",
        "close",
        "structure",
        "swing_high_price",
        "swing_low_price",
    }

    missing = required_columns.difference(classified.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing))}"
        )

    if tolerance_pips < 0:
        raise ValueError(
            "tolerance_pips cannot be negative."
        )

    if pip_size <= 0:
        raise ValueError(
            "pip_size must be greater than zero."
        )

    if minimum_sweep_pips < 0:
        raise ValueError(
            "minimum_sweep_pips cannot be negative."
        )

    if break_confirmation_pips < 0:
        raise ValueError(
            "break_confirmation_pips cannot be negative."
        )

    result = classified.copy()

    result["equal_high"] = False
    result["equal_low"] = False

    result["equal_high_level"] = pd.NA
    result["equal_low_level"] = pd.NA

    result["equal_high_id"] = pd.NA
    result["equal_low_id"] = pd.NA

    result["liquidity_created"] = False
    result["liquidity_type"] = pd.NA
    result["liquidity_level"] = pd.NA
    result["liquidity_id"] = pd.NA

    result["liquidity_swept"] = False
    result["liquidity_broken"] = False

    result["swept_liquidity_type"] = pd.NA
    result["swept_liquidity_id"] = pd.NA
    result["swept_liquidity_level"] = pd.NA

    result["broken_liquidity_type"] = pd.NA
    result["broken_liquidity_id"] = pd.NA
    result["broken_liquidity_level"] = pd.NA

    result["sweep_direction"] = pd.NA
    result["sweep_distance"] = pd.NA
    result["sweep_distance_pips"] = pd.NA

    result["break_direction"] = pd.NA
    result["break_distance"] = pd.NA
    result["break_distance_pips"] = pd.NA

    result["active_bsl_count"] = 0
    result["active_ssl_count"] = 0

    tolerance = tolerance_pips * pip_size
    minimum_sweep_distance = minimum_sweep_pips * pip_size
    break_confirmation_distance = (
        break_confirmation_pips * pip_size
    )

    registry = LiquidityRegistry()
    events: list[MarketEvent] = []

    previous_high: float | None = None
    previous_low: float | None = None

    previous_high_index: Any = None
    previous_low_index: Any = None

    previous_high_position: int | None = None
    previous_low_position: int | None = None

    previous_high_time: datetime | None = None
    previous_low_time: datetime | None = None

    high_group_number = 0
    low_group_number = 0
    event_number = 0

    for position, index in enumerate(result.index):
        current_time = _safe_datetime(
            result.at[index, "time"]
        )

        current_high = _safe_float(
            result.at[index, "high"]
        )

        current_low = _safe_float(
            result.at[index, "low"]
        )

        current_close = _safe_float(
            result.at[index, "close"]
        )

        structure_value = result.at[index, "structure"]

        structure_type = None

        if not pd.isna(structure_value):
            structure_type = str(structure_value)

        # -------------------------------------------------
        # 1. Check every active pool
        # -------------------------------------------------

        for pool in list(registry.active()):
            if (
                current_high is None
                or current_low is None
                or current_close is None
            ):
                continue

            if pool.liquidity_type == "BSL":
                distance_above = current_high - pool.level

                sweep_detected = (
                    current_high > pool.level
                    and current_close < pool.level
                    and distance_above
                    >= minimum_sweep_distance
                )

                break_detected = (
                    current_close
                    > pool.level
                    + break_confirmation_distance
                )

                if sweep_detected:
                    pool.mark_swept(
                        time=current_time,
                        index=position,
                        sweep_price=current_high,
                        sweep_close=current_close,
                        sweep_distance=distance_above,
                        sweep_distance_pips=(
                            distance_above / pip_size
                        ),
                        direction="bearish",
                    )

                    result.at[
                        index,
                        "liquidity_swept",
                    ] = True
                    result.at[
                        index,
                        "swept_liquidity_type",
                    ] = pool.liquidity_type
                    result.at[
                        index,
                        "swept_liquidity_id",
                    ] = pool.liquidity_id
                    result.at[
                        index,
                        "swept_liquidity_level",
                    ] = pool.level
                    result.at[
                        index,
                        "sweep_direction",
                    ] = "bearish"
                    result.at[
                        index,
                        "sweep_distance",
                    ] = distance_above
                    result.at[
                        index,
                        "sweep_distance_pips",
                    ] = distance_above / pip_size

                    event_number += 1

                    events.append(
                        MarketEvent(
                            event_id=(
                                f"EV_LIQ_{event_number:05d}"
                            ),
                            event_type="LIQUIDITY_SWEPT",
                            time=current_time,
                            index=position,
                            direction="bearish",
                            price=current_high,
                            broken_level=pool.level,
                            source_id=pool.liquidity_id,
                            source_type="BSL",
                            description=(
                                "Buy-side liquidity swept "
                                "and candle closed below "
                                "the liquidity level."
                            ),
                            metadata={
                                "sweep_distance_pips":
                                    distance_above / pip_size,
                            },
                        )
                    )

                elif break_detected:
                    break_distance = (
                        current_close - pool.level
                    )

                    pool.mark_broken(
                        time=current_time,
                        index=position,
                        break_price=current_high,
                        break_close=current_close,
                        break_distance=break_distance,
                        break_distance_pips=(
                            break_distance / pip_size
                        ),
                    )

                    result.at[
                        index,
                        "liquidity_broken",
                    ] = True
                    result.at[
                        index,
                        "broken_liquidity_type",
                    ] = pool.liquidity_type
                    result.at[
                        index,
                        "broken_liquidity_id",
                    ] = pool.liquidity_id
                    result.at[
                        index,
                        "broken_liquidity_level",
                    ] = pool.level
                    result.at[
                        index,
                        "break_direction",
                    ] = "bullish"
                    result.at[
                        index,
                        "break_distance",
                    ] = break_distance
                    result.at[
                        index,
                        "break_distance_pips",
                    ] = break_distance / pip_size

                    event_number += 1

                    events.append(
                        MarketEvent(
                            event_id=(
                                f"EV_LIQ_{event_number:05d}"
                            ),
                            event_type="LIQUIDITY_BROKEN",
                            time=current_time,
                            index=position,
                            direction="bullish",
                            price=current_close,
                            broken_level=pool.level,
                            source_id=pool.liquidity_id,
                            source_type="BSL",
                            description=(
                                "Buy-side liquidity was "
                                "genuinely broken."
                            ),
                            metadata={
                                "break_distance_pips":
                                    break_distance / pip_size,
                            },
                        )
                    )

            elif pool.liquidity_type == "SSL":
                distance_below = pool.level - current_low

                sweep_detected = (
                    current_low < pool.level
                    and current_close > pool.level
                    and distance_below
                    >= minimum_sweep_distance
                )

                break_detected = (
                    current_close
                    < pool.level
                    - break_confirmation_distance
                )

                if sweep_detected:
                    pool.mark_swept(
                        time=current_time,
                        index=position,
                        sweep_price=current_low,
                        sweep_close=current_close,
                        sweep_distance=distance_below,
                        sweep_distance_pips=(
                            distance_below / pip_size
                        ),
                        direction="bullish",
                    )

                    result.at[
                        index,
                        "liquidity_swept",
                    ] = True
                    result.at[
                        index,
                        "swept_liquidity_type",
                    ] = pool.liquidity_type
                    result.at[
                        index,
                        "swept_liquidity_id",
                    ] = pool.liquidity_id
                    result.at[
                        index,
                        "swept_liquidity_level",
                    ] = pool.level
                    result.at[
                        index,
                        "sweep_direction",
                    ] = "bullish"
                    result.at[
                        index,
                        "sweep_distance",
                    ] = distance_below
                    result.at[
                        index,
                        "sweep_distance_pips",
                    ] = distance_below / pip_size

                    event_number += 1

                    events.append(
                        MarketEvent(
                            event_id=(
                                f"EV_LIQ_{event_number:05d}"
                            ),
                            event_type="LIQUIDITY_SWEPT",
                            time=current_time,
                            index=position,
                            direction="bullish",
                            price=current_low,
                            broken_level=pool.level,
                            source_id=pool.liquidity_id,
                            source_type="SSL",
                            description=(
                                "Sell-side liquidity swept "
                                "and candle closed above "
                                "the liquidity level."
                            ),
                            metadata={
                                "sweep_distance_pips":
                                    distance_below / pip_size,
                            },
                        )
                    )

                elif break_detected:
                    break_distance = (
                        pool.level - current_close
                    )

                    pool.mark_broken(
                        time=current_time,
                        index=position,
                        break_price=current_low,
                        break_close=current_close,
                        break_distance=break_distance,
                        break_distance_pips=(
                            break_distance / pip_size
                        ),
                    )

                    result.at[
                        index,
                        "liquidity_broken",
                    ] = True
                    result.at[
                        index,
                        "broken_liquidity_type",
                    ] = pool.liquidity_type
                    result.at[
                        index,
                        "broken_liquidity_id",
                    ] = pool.liquidity_id
                    result.at[
                        index,
                        "broken_liquidity_level",
                    ] = pool.level
                    result.at[
                        index,
                        "break_direction",
                    ] = "bearish"
                    result.at[
                        index,
                        "break_distance",
                    ] = break_distance
                    result.at[
                        index,
                        "break_distance_pips",
                    ] = break_distance / pip_size

                    event_number += 1

                    events.append(
                        MarketEvent(
                            event_id=(
                                f"EV_LIQ_{event_number:05d}"
                            ),
                            event_type="LIQUIDITY_BROKEN",
                            time=current_time,
                            index=position,
                            direction="bearish",
                            price=current_close,
                            broken_level=pool.level,
                            source_id=pool.liquidity_id,
                            source_type="SSL",
                            description=(
                                "Sell-side liquidity was "
                                "genuinely broken."
                            ),
                            metadata={
                                "break_distance_pips":
                                    break_distance / pip_size,
                            },
                        )
                    )

        # -------------------------------------------------
        # 2. Expire old active pools
        # -------------------------------------------------

        if maximum_age_bars is not None:
            registry.expire_old_pools(
                current_time=current_time,
                current_index=position,
                maximum_age_bars=maximum_age_bars,
            )

        # -------------------------------------------------
        # 3. Detect EQH and create a new BSL pool
        # -------------------------------------------------

        if structure_type in {"HH", "LH"}:
            swing_high = _safe_float(
                result.at[index, "swing_high_price"]
            )

            if swing_high is not None:
                if previous_high is not None:
                    difference = abs(
                        swing_high - previous_high
                    )

                    if difference <= tolerance:
                        high_group_number += 1

                        liquidity_id = (
                            f"EQH_{high_group_number}"
                        )

                        shared_level = (
                            previous_high + swing_high
                        ) / 2.0

                        pool = LiquidityPool(
                            liquidity_id=liquidity_id,
                            liquidity_type="BSL",
                            level=shared_level,
                            created_time=current_time,
                            created_index=position,
                            first_swing_time=(
                                previous_high_time
                            ),
                            first_swing_index=(
                                previous_high_position
                            ),
                            first_swing_price=previous_high,
                            second_swing_time=current_time,
                            second_swing_index=position,
                            second_swing_price=swing_high,
                            touches=2,
                            metadata={
                                "tolerance_pips":
                                    tolerance_pips,
                                "difference_pips":
                                    difference / pip_size,
                            },
                        )

                        registry.add(pool)

                        result.at[
                            index,
                            "equal_high",
                        ] = True
                        result.at[
                            index,
                            "equal_high_level",
                        ] = shared_level
                        result.at[
                            index,
                            "equal_high_id",
                        ] = liquidity_id

                        result.at[
                            index,
                            "liquidity_created",
                        ] = True
                        result.at[
                            index,
                            "liquidity_type",
                        ] = "BSL"
                        result.at[
                            index,
                            "liquidity_level",
                        ] = shared_level
                        result.at[
                            index,
                            "liquidity_id",
                        ] = liquidity_id

                        if previous_high_index is not None:
                            result.at[
                                previous_high_index,
                                "equal_high",
                            ] = True
                            result.at[
                                previous_high_index,
                                "equal_high_level",
                            ] = shared_level
                            result.at[
                                previous_high_index,
                                "equal_high_id",
                            ] = liquidity_id

                        event_number += 1

                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_LIQ_{event_number:05d}"
                                ),
                                event_type=(
                                    "LIQUIDITY_CREATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bearish",
                                price=shared_level,
                                source_id=liquidity_id,
                                source_type="BSL",
                                description=(
                                    "Equal highs created "
                                    "buy-side liquidity."
                                ),
                                metadata={
                                    "difference_pips":
                                        difference / pip_size,
                                },
                            )
                        )

                previous_high = swing_high
                previous_high_index = index
                previous_high_position = position
                previous_high_time = current_time

        # -------------------------------------------------
        # 4. Detect EQL and create a new SSL pool
        # -------------------------------------------------

        elif structure_type in {"HL", "LL"}:
            swing_low = _safe_float(
                result.at[index, "swing_low_price"]
            )

            if swing_low is not None:
                if previous_low is not None:
                    difference = abs(
                        swing_low - previous_low
                    )

                    if difference <= tolerance:
                        low_group_number += 1

                        liquidity_id = (
                            f"EQL_{low_group_number}"
                        )

                        shared_level = (
                            previous_low + swing_low
                        ) / 2.0

                        pool = LiquidityPool(
                            liquidity_id=liquidity_id,
                            liquidity_type="SSL",
                            level=shared_level,
                            created_time=current_time,
                            created_index=position,
                            first_swing_time=(
                                previous_low_time
                            ),
                            first_swing_index=(
                                previous_low_position
                            ),
                            first_swing_price=previous_low,
                            second_swing_time=current_time,
                            second_swing_index=position,
                            second_swing_price=swing_low,
                            touches=2,
                            metadata={
                                "tolerance_pips":
                                    tolerance_pips,
                                "difference_pips":
                                    difference / pip_size,
                            },
                        )

                        registry.add(pool)

                        result.at[
                            index,
                            "equal_low",
                        ] = True
                        result.at[
                            index,
                            "equal_low_level",
                        ] = shared_level
                        result.at[
                            index,
                            "equal_low_id",
                        ] = liquidity_id

                        result.at[
                            index,
                            "liquidity_created",
                        ] = True
                        result.at[
                            index,
                            "liquidity_type",
                        ] = "SSL"
                        result.at[
                            index,
                            "liquidity_level",
                        ] = shared_level
                        result.at[
                            index,
                            "liquidity_id",
                        ] = liquidity_id

                        if previous_low_index is not None:
                            result.at[
                                previous_low_index,
                                "equal_low",
                            ] = True
                            result.at[
                                previous_low_index,
                                "equal_low_level",
                            ] = shared_level
                            result.at[
                                previous_low_index,
                                "equal_low_id",
                            ] = liquidity_id

                        event_number += 1

                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_LIQ_{event_number:05d}"
                                ),
                                event_type=(
                                    "LIQUIDITY_CREATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bullish",
                                price=shared_level,
                                source_id=liquidity_id,
                                source_type="SSL",
                                description=(
                                    "Equal lows created "
                                    "sell-side liquidity."
                                ),
                                metadata={
                                    "difference_pips":
                                        difference / pip_size,
                                },
                            )
                        )

                previous_low = swing_low
                previous_low_index = index
                previous_low_position = position
                previous_low_time = current_time

        result.at[index, "active_bsl_count"] = len(
            registry.active("BSL")
        )

        result.at[index, "active_ssl_count"] = len(
            registry.active("SSL")
        )

    return result, registry, events


def detect_liquidity(
    classified: pd.DataFrame,
    tolerance_pips: float = 5.0,
    pip_size: float = 0.0001,
    minimum_sweep_pips: float = 0.5,
    break_confirmation_pips: float = 0.5,
    maximum_age_bars: int | None = None,
) -> pd.DataFrame:
    """
    Backwards-compatible DataFrame-only function.
    """

    result, _, _ = detect_liquidity_registry(
        classified=classified,
        tolerance_pips=tolerance_pips,
        pip_size=pip_size,
        minimum_sweep_pips=minimum_sweep_pips,
        break_confirmation_pips=break_confirmation_pips,
        maximum_age_bars=maximum_age_bars,
    )

    return result


def detect_equal_highs_lows(
    classified: pd.DataFrame,
    tolerance_pips: float = 5.0,
    pip_size: float = 0.0001,
) -> pd.DataFrame:
    """
    Backwards-compatible equal-high/equal-low wrapper.
    """

    return detect_liquidity(
        classified=classified,
        tolerance_pips=tolerance_pips,
        pip_size=pip_size,
        minimum_sweep_pips=0.0,
        break_confirmation_pips=0.5,
    )