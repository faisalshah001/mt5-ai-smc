from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from app.analysis.models import MarketEvent, OrderBlock
from app.analysis.order_block_registry import OrderBlockRegistry


SUPPORTED_STRUCTURE_EVENTS = {"BOS", "MSS", "CHoCH"}
SUPPORTED_DIRECTIONS = {"bullish", "bearish"}


def _safe_float(value: Any) -> float | None:
    """Convert a value to float safely."""

    if pd.isna(value):
        return None

    return float(value)


def _safe_datetime(value: Any) -> datetime:
    """Convert pandas or Python datetime values to datetime."""

    return pd.Timestamp(value).to_pydatetime()


def _normalise_event_types(
    source_event_types: Iterable[str],
) -> set[str]:
    """Validate and normalise enabled structure-event types."""

    event_types = {str(value) for value in source_event_types}

    unsupported = event_types.difference(SUPPORTED_STRUCTURE_EVENTS)

    if unsupported:
        raise ValueError(
            "Unsupported source_event_types: "
            f"{', '.join(sorted(unsupported))}. "
            "Supported values are BOS, MSS, and CHoCH."
        )

    if not event_types:
        raise ValueError("source_event_types cannot be empty.")

    return event_types


def _candle_direction(
    open_price: float,
    close_price: float,
) -> str:
    """Return bullish, bearish, or neutral for one candle."""

    if close_price > open_price:
        return "bullish"

    if close_price < open_price:
        return "bearish"

    return "neutral"


def _find_order_block_candle(
    result: pd.DataFrame,
    *,
    event_position: int,
    direction: str,
    lookback_bars: int,
) -> tuple[int, Any] | None:
    """
    Find the final opposite-colour candle before displacement.

    Bullish structure event -> final bearish candle.
    Bearish structure event -> final bullish candle.
    """

    first_position = max(0, event_position - lookback_bars)
    required_candle_direction = (
        "bearish" if direction == "bullish" else "bullish"
    )

    for candle_position in range(event_position - 1, first_position - 1, -1):
        candle_index = result.index[candle_position]

        open_price = _safe_float(result.at[candle_index, "open"])
        close_price = _safe_float(result.at[candle_index, "close"])

        if open_price is None or close_price is None:
            continue

        if _candle_direction(open_price, close_price) == required_candle_direction:
            return candle_position, candle_index

    return None


def _liquidity_sweep_confirmed(
    result: pd.DataFrame,
    *,
    order_block_position: int,
    event_position: int,
    direction: str,
) -> bool:
    """Check for a directionally relevant liquidity sweep in the setup leg."""

    required_columns = {
        "liquidity_swept",
        "sweep_direction",
    }

    if not required_columns.issubset(result.columns):
        return False

    expected_sweep_direction = direction

    for position in range(order_block_position, event_position + 1):
        row_index = result.index[position]
        swept_value = result.at[row_index, "liquidity_swept"]
        sweep_direction = result.at[row_index, "sweep_direction"]

        if (
            not pd.isna(swept_value)
            and bool(swept_value)
            and not pd.isna(sweep_direction)
        ):
            if str(sweep_direction) == expected_sweep_direction:
                return True

    return False


def detect_order_blocks(
    classified: pd.DataFrame,
    *,
    atr_column: str = "atr14",
    source_event_types: tuple[str, ...] = ("BOS", "MSS", "CHoCH"),
    lookback_bars: int = 12,
    minimum_displacement_atr: float = 1.0,
    minimum_event_body_ratio: float = 0.55,
    require_liquidity_sweep: bool = False,
    invalidation_confirmation_pips: float = 0.0,
    pip_size: float = 0.0001,
    maximum_age_bars: int | None = None,
) -> tuple[pd.DataFrame, OrderBlockRegistry, list[MarketEvent]]:
    """
    Detect institutional-style Order Blocks and manage their lifecycle.

    Creation rules
    --------------
    1. A confirmed BOS, MSS, or CHoCH must exist.
    2. The event direction must be bullish or bearish.
    3. The event candle must have a sufficiently strong real body.
    4. Price displacement from the source candle must meet the ATR filter.
    5. The final opposite-colour candle inside lookback_bars becomes the block.
    6. A liquidity sweep can optionally be required.

    Lifecycle rules
    ---------------
    Bullish block:
        Mitigated when a later candle trades into its range.
        Invalidated when a later candle closes below its distal level.

    Bearish block:
        Mitigated when a later candle trades into its range.
        Invalidated when a later candle closes above its distal level.

    MSS-sourced blocks (Decision #12, SMC_SPECIFICATION.md §28,
    Appendix B) additionally follow a provisional/confirmed lifecycle:
    created with confirmation_status="provisional"; invalidated via
    the MSS_INVALIDATED cascade if the originating MSS invalidates
    (independent of price action); promoted in place to "confirmed"
    (never duplicated) if the originating MSS instead resolves into a
    CHoCH anchored on the same source candle. BOS- and CHoCH-sourced
    blocks are always created "confirmed" and are never touched by the
    cascade.

    Returns
    -------
    enriched_dataframe, order_block_registry, order_block_events
    """

    required_columns = {
        "time",
        "open",
        "high",
        "low",
        "close",
        atr_column,
        "structure_event",
        "event_direction",
        "broken_level",
        "mss_invalidated_origin_index",
    }

    missing = required_columns.difference(classified.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing))}"
        )

    enabled_event_types = _normalise_event_types(source_event_types)

    if lookback_bars < 1:
        raise ValueError("lookback_bars must be at least 1.")

    if minimum_displacement_atr < 0:
        raise ValueError("minimum_displacement_atr cannot be negative.")

    if not 0.0 <= minimum_event_body_ratio <= 1.0:
        raise ValueError(
            "minimum_event_body_ratio must be between 0.0 and 1.0."
        )

    if invalidation_confirmation_pips < 0:
        raise ValueError(
            "invalidation_confirmation_pips cannot be negative."
        )

    if pip_size <= 0:
        raise ValueError("pip_size must be greater than zero.")

    if maximum_age_bars is not None and maximum_age_bars < 1:
        raise ValueError("maximum_age_bars must be at least 1.")

    if require_liquidity_sweep:
        liquidity_columns = {
            "liquidity_swept",
            "sweep_direction",
        }
        missing_liquidity = liquidity_columns.difference(
            classified.columns
        )

        if missing_liquidity:
            raise ValueError(
                "require_liquidity_sweep=True requires columns: "
                f"{', '.join(sorted(missing_liquidity))}"
            )

    result = classified.copy()

    result["order_block_created"] = False
    result["order_block_id"] = pd.NA
    result["order_block_type"] = pd.NA
    result["order_block_high"] = pd.NA
    result["order_block_low"] = pd.NA
    result["order_block_proximal"] = pd.NA
    result["order_block_distal"] = pd.NA
    result["order_block_candle_index"] = pd.NA
    result["order_block_candle_time"] = pd.Series(
    pd.NaT,
    index=result.index,
    dtype="datetime64[ns, UTC]",
)

    result["order_block_mitigated"] = False
    result["mitigated_order_block_id"] = pd.NA
    result["mitigation_price"] = pd.NA
    result["mitigation_percentage"] = pd.NA

    result["order_block_invalidated"] = False
    result["invalidated_order_block_id"] = pd.NA
    result["invalidation_price"] = pd.NA

    # Decision #12 (§28 point 4): populated on the candle where a
    # provisional (MSS-sourced) block is promoted to confirmed.
    result["order_block_confirmed"] = False
    result["confirmed_order_block_id"] = pd.NA

    result["order_block_expired"] = False
    result["expired_order_block_id"] = pd.NA

    result["active_bullish_order_blocks"] = 0
    result["active_bearish_order_blocks"] = 0

    registry = OrderBlockRegistry()
    events: list[MarketEvent] = []

    order_block_number = 0
    event_number = 0

    invalidation_distance = (
        invalidation_confirmation_pips * pip_size
    )

    # Decision #12 (§28 point 4): the pending MSS's own source_event_id,
    # tracked locally by this engine as it observes the same
    # structure_event stream state_machine.py produces — mirrors that
    # module's own mss_origin_level/mss_origin_index tracking, but
    # scoped here. Only ever set when an MSS-sourced block was actually
    # created (nothing to promote otherwise); cleared the moment that
    # MSS resolves, one way or the other. state_machine.py's own
    # single-pending-MSS invariant (Sections 3/4 gated on
    # current_state in {"bullish", "bearish"} only) guarantees no other
    # BOS/MSS event can occur while one is already pending, so this
    # requires no additional bookkeeping.
    pending_mss_source_event_id: str | None = None

    for position, index in enumerate(result.index):
        current_time = _safe_datetime(result.at[index, "time"])
        current_open = _safe_float(result.at[index, "open"])
        current_high = _safe_float(result.at[index, "high"])
        current_low = _safe_float(result.at[index, "low"])
        current_close = _safe_float(result.at[index, "close"])

        structure_event_value = result.at[index, "structure_event"]
        event_direction_value = result.at[index, "event_direction"]

        structure_event = None
        event_direction = None

        if not pd.isna(structure_event_value):
            structure_event = str(structure_event_value)

        if not pd.isna(event_direction_value):
            event_direction = str(event_direction_value)

        skip_creation_this_row = False

        # ---------------------------------------------------------
        # 1. Update existing active Order Blocks
        # ---------------------------------------------------------

        if (
            current_high is not None
            and current_low is not None
            and current_close is not None
        ):
            for order_block in list(registry.active()):
                # Never mitigate or invalidate a block before or on
                # the candle where the structural event created it.
                if position <= order_block.created_index:
                    continue

                if order_block.order_block_type == "bullish":
                    invalidated = (
                        current_close
                        < order_block.distal_level
                        - invalidation_distance
                    )

                    touches_block = (
                        current_low <= order_block.high
                        and current_high >= order_block.low
                    )

                    if invalidated:
                        order_block.mark_invalidated(
                            time=current_time,
                            index=position,
                            price=current_close,
                        )

                        result.at[
                            index,
                            "order_block_invalidated",
                        ] = True
                        result.at[
                            index,
                            "invalidated_order_block_id",
                        ] = order_block.order_block_id
                        result.at[
                            index,
                            "invalidation_price",
                        ] = current_close

                        event_number += 1
                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_OB_{event_number:05d}"
                                ),
                                event_type=(
                                    "ORDER_BLOCK_INVALIDATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bearish",
                                price=current_close,
                                source_id=(
                                    order_block.order_block_id
                                ),
                                source_type="bullish",
                                description=(
                                    "Bullish Order Block invalidated "
                                    "by a close below its distal level."
                                ),
                                metadata={
                                    "distal_level": (
                                        order_block.distal_level
                                    ),
                                },
                            )
                        )

                    elif touches_block:
                        mitigation_price = min(
                            max(current_low, order_block.low),
                            order_block.high,
                        )

                        order_block.mark_mitigated(
                            time=current_time,
                            index=position,
                            price=mitigation_price,
                        )

                        result.at[
                            index,
                            "order_block_mitigated",
                        ] = True
                        result.at[
                            index,
                            "mitigated_order_block_id",
                        ] = order_block.order_block_id
                        result.at[
                            index,
                            "mitigation_price",
                        ] = mitigation_price
                        result.at[
                            index,
                            "mitigation_percentage",
                        ] = order_block.mitigation_percentage

                        event_number += 1
                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_OB_{event_number:05d}"
                                ),
                                event_type=(
                                    "ORDER_BLOCK_MITIGATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bullish",
                                price=mitigation_price,
                                source_id=(
                                    order_block.order_block_id
                                ),
                                source_type="bullish",
                                description=(
                                    "Price returned into the bullish "
                                    "Order Block."
                                ),
                                metadata={
                                    "mitigation_percentage": (
                                        order_block.
                                        mitigation_percentage
                                    ),
                                },
                            )
                        )

                else:
                    invalidated = (
                        current_close
                        > order_block.distal_level
                        + invalidation_distance
                    )

                    touches_block = (
                        current_high >= order_block.low
                        and current_low <= order_block.high
                    )

                    if invalidated:
                        order_block.mark_invalidated(
                            time=current_time,
                            index=position,
                            price=current_close,
                        )

                        result.at[
                            index,
                            "order_block_invalidated",
                        ] = True
                        result.at[
                            index,
                            "invalidated_order_block_id",
                        ] = order_block.order_block_id
                        result.at[
                            index,
                            "invalidation_price",
                        ] = current_close

                        event_number += 1
                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_OB_{event_number:05d}"
                                ),
                                event_type=(
                                    "ORDER_BLOCK_INVALIDATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bullish",
                                price=current_close,
                                source_id=(
                                    order_block.order_block_id
                                ),
                                source_type="bearish",
                                description=(
                                    "Bearish Order Block invalidated "
                                    "by a close above its distal level."
                                ),
                                metadata={
                                    "distal_level": (
                                        order_block.distal_level
                                    ),
                                },
                            )
                        )

                    elif touches_block:
                        mitigation_price = max(
                            min(current_high, order_block.high),
                            order_block.low,
                        )

                        order_block.mark_mitigated(
                            time=current_time,
                            index=position,
                            price=mitigation_price,
                        )

                        result.at[
                            index,
                            "order_block_mitigated",
                        ] = True
                        result.at[
                            index,
                            "mitigated_order_block_id",
                        ] = order_block.order_block_id
                        result.at[
                            index,
                            "mitigation_price",
                        ] = mitigation_price
                        result.at[
                            index,
                            "mitigation_percentage",
                        ] = order_block.mitigation_percentage

                        event_number += 1
                        events.append(
                            MarketEvent(
                                event_id=(
                                    f"EV_OB_{event_number:05d}"
                                ),
                                event_type=(
                                    "ORDER_BLOCK_MITIGATED"
                                ),
                                time=current_time,
                                index=position,
                                direction="bearish",
                                price=mitigation_price,
                                source_id=(
                                    order_block.order_block_id
                                ),
                                source_type="bearish",
                                description=(
                                    "Price returned into the bearish "
                                    "Order Block."
                                ),
                                metadata={
                                    "mitigation_percentage": (
                                        order_block.
                                        mitigation_percentage
                                    ),
                                },
                            )
                        )

        # ---------------------------------------------------------
        # 1b. MSS invalidation cascade (Decision #12, §28 point 3;
        #     mirrors state_machine.py's MSS_INVALIDATED signal, §19).
        # ---------------------------------------------------------

        if structure_event == "MSS_INVALIDATED":
            origin_index_value = result.at[
                index, "mss_invalidated_origin_index"
            ]

            if not pd.isna(origin_index_value):
                origin_position = int(origin_index_value)
                origin_source_event_id = (
                    f"STR_MSS_{origin_position:05d}"
                )

                for order_block in registry.by_source_event_id(
                    origin_source_event_id
                ):
                    # Status transitions are one-way and already
                    # terminal — the cascade only acts on still-active
                    # blocks (§28 point 3).
                    if order_block.status != "active":
                        continue

                    order_block.mark_invalidated(
                        time=current_time,
                        index=position,
                        price=current_close,
                        reason="mss_invalidated",
                    )

                    result.at[
                        index, "order_block_invalidated"
                    ] = True
                    result.at[
                        index, "invalidated_order_block_id"
                    ] = order_block.order_block_id
                    result.at[
                        index, "invalidation_price"
                    ] = current_close

                    event_number += 1
                    events.append(
                        MarketEvent(
                            event_id=(
                                f"EV_OB_{event_number:05d}"
                            ),
                            event_type=(
                                "ORDER_BLOCK_INVALIDATED"
                            ),
                            time=current_time,
                            index=position,
                            direction=(
                                "bearish"
                                if order_block.order_block_type
                                == "bullish"
                                else "bullish"
                            ),
                            price=current_close,
                            source_id=(
                                order_block.order_block_id
                            ),
                            source_type=(
                                order_block.order_block_type
                            ),
                            description=(
                                f"{order_block.order_block_type.title()} "
                                "Order Block invalidated because its "
                                "originating MSS invalidated."
                            ),
                            metadata={
                                "distal_level": (
                                    order_block.distal_level
                                ),
                                "invalidation_reason": (
                                    "mss_invalidated"
                                ),
                                "mss_origin_index": origin_position,
                            },
                        )
                    )

            pending_mss_source_event_id = None

        # ---------------------------------------------------------
        # 1c. MSS -> CHoCH promotion (Decision #12, §28 point 4).
        # ---------------------------------------------------------

        if (
            structure_event == "CHoCH"
            and pending_mss_source_event_id is not None
        ):
            mss_sourced_block = None

            for candidate in registry.by_source_event_id(
                pending_mss_source_event_id
            ):
                if candidate.confirmation_status == "provisional":
                    mss_sourced_block = candidate
                    break

            if mss_sourced_block is not None:
                confirming_event_id = f"STR_CHoCH_{position:05d}"

                mss_sourced_block.mark_confirmed(
                    time=current_time,
                    index=position,
                    confirming_event_id=confirming_event_id,
                    confirming_event_type="CHoCH",
                )

                result.at[index, "order_block_confirmed"] = True
                result.at[
                    index, "confirmed_order_block_id"
                ] = mss_sourced_block.order_block_id

                event_number += 1
                events.append(
                    MarketEvent(
                        event_id=f"EV_OB_{event_number:05d}",
                        event_type="ORDER_BLOCK_CONFIRMED",
                        time=current_time,
                        index=position,
                        direction=(
                            mss_sourced_block.order_block_type
                        ),
                        price=mss_sourced_block.proximal_level,
                        source_id=(
                            mss_sourced_block.order_block_id
                        ),
                        source_type=(
                            mss_sourced_block.order_block_type
                        ),
                        description=(
                            f"{mss_sourced_block.order_block_type.title()} "
                            "Order Block confirmed: its originating "
                            "MSS resolved into a confirmed CHoCH."
                        ),
                        metadata={
                            "source_event_id": (
                                mss_sourced_block.source_event_id
                            ),
                            "confirming_event_id": (
                                confirming_event_id
                            ),
                        },
                    )
                )

                # Dedup key = candle_index alone (§28 point 4). A
                # match means this same CHoCH would otherwise create a
                # duplicate footprint on the identical anchor candle —
                # skip Section 3's creation for this row. A mismatch
                # means CHoCH resolved to a different anchor; Section
                # 3 proceeds and creates an independent, separately-
                # tracked CHoCH-sourced block, while the promotion
                # above still stands on its own.
                choch_source_candle = None

                if event_direction is not None:
                    choch_source_candle = _find_order_block_candle(
                        result,
                        event_position=position,
                        direction=event_direction,
                        lookback_bars=lookback_bars,
                    )

                if (
                    choch_source_candle is not None
                    and choch_source_candle[0]
                    == mss_sourced_block.candle_index
                ):
                    skip_creation_this_row = True

            pending_mss_source_event_id = None

        # ---------------------------------------------------------
        # 2. Expire old active Order Blocks
        # ---------------------------------------------------------

        if maximum_age_bars is not None:
            expired_blocks = registry.expire_old_order_blocks(
                current_time=current_time,
                current_index=position,
                maximum_age_bars=maximum_age_bars,
            )

            for order_block in expired_blocks:
                result.at[
                    index,
                    "order_block_expired",
                ] = True
                result.at[
                    index,
                    "expired_order_block_id",
                ] = order_block.order_block_id

        # ---------------------------------------------------------
        # 3. Detect a new institutional Order Block
        # ---------------------------------------------------------

        eligible_event = (
            structure_event in enabled_event_types
            and event_direction in SUPPORTED_DIRECTIONS
            and not skip_creation_this_row
        )

        if eligible_event and position > 0:
            atr_value = _safe_float(result.at[index, atr_column])
            broken_level = _safe_float(
                result.at[index, "broken_level"]
            )

            if (
                current_open is not None
                and current_high is not None
                and current_low is not None
                and current_close is not None
                and atr_value is not None
                and atr_value > 0
            ):
                event_range = current_high - current_low
                event_body = abs(current_close - current_open)

                if event_range > 0:
                    event_body_ratio = event_body / event_range
                else:
                    event_body_ratio = 0.0

                direction_matches_candle = (
                    event_direction == "bullish"
                    and current_close > current_open
                ) or (
                    event_direction == "bearish"
                    and current_close < current_open
                )

                source_candle = _find_order_block_candle(
                    result,
                    event_position=position,
                    direction=event_direction,
                    lookback_bars=lookback_bars,
                )

                if (
                    direction_matches_candle
                    and event_body_ratio
                    >= minimum_event_body_ratio
                    and source_candle is not None
                ):
                    source_position, source_index = source_candle

                    source_time = _safe_datetime(
                        result.at[source_index, "time"]
                    )
                    source_open = _safe_float(
                        result.at[source_index, "open"]
                    )
                    source_high = _safe_float(
                        result.at[source_index, "high"]
                    )
                    source_low = _safe_float(
                        result.at[source_index, "low"]
                    )
                    source_close = _safe_float(
                        result.at[source_index, "close"]
                    )

                    if (
                        source_open is not None
                        and source_high is not None
                        and source_low is not None
                        and source_close is not None
                    ):
                        if event_direction == "bullish":
                            displacement_distance = (
                                current_close - source_low
                            )
                            proximal_level = source_high
                            distal_level = source_low
                        else:
                            displacement_distance = (
                                source_high - current_close
                            )
                            proximal_level = source_low
                            distal_level = source_high

                        required_displacement = (
                            atr_value * minimum_displacement_atr
                        )

                        liquidity_confirmed = (
                            _liquidity_sweep_confirmed(
                                result,
                                order_block_position=(
                                    source_position
                                ),
                                event_position=position,
                                direction=event_direction,
                            )
                        )

                        passes_liquidity_filter = (
                            not require_liquidity_sweep
                            or liquidity_confirmed
                        )

                        if (
                            displacement_distance
                            >= required_displacement
                            and passes_liquidity_filter
                        ):
                            order_block_number += 1
                            order_block_id = (
                                f"OB_{order_block_number:05d}"
                            )
                            source_event_id = (
                                f"STR_{structure_event}_"
                                f"{position:05d}"
                            )

                            order_block = OrderBlock(
                                order_block_id=order_block_id,
                                order_block_type=(
                                    event_direction
                                ),
                                created_time=current_time,
                                created_index=position,
                                candle_time=source_time,
                                candle_index=source_position,
                                high=source_high,
                                low=source_low,
                                open=source_open,
                                close=source_close,
                                proximal_level=proximal_level,
                                distal_level=distal_level,
                                # Decision #12 (§28 point 2): MSS-sourced
                                # blocks start provisional; BOS/CHoCH-
                                # sourced blocks are terminal by
                                # construction and use the dataclass's
                                # own "confirmed" default.
                                confirmation_status=(
                                    "provisional"
                                    if structure_event == "MSS"
                                    else "confirmed"
                                ),
                                source_event_id=source_event_id,
                                source_event_type=(
                                    structure_event
                                ),
                                source_broken_level=(
                                    broken_level
                                ),
                                displacement_start_index=(
                                    source_position
                                ),
                                displacement_end_index=position,
                                displacement_distance=(
                                    displacement_distance
                                ),
                                displacement_distance_pips=(
                                    displacement_distance
                                    / pip_size
                                ),
                                metadata={
                                    "atr": atr_value,
                                    "required_displacement": (
                                        required_displacement
                                    ),
                                    "minimum_displacement_atr": (
                                        minimum_displacement_atr
                                    ),
                                    "event_body_ratio": (
                                        event_body_ratio
                                    ),
                                    "minimum_event_body_ratio": (
                                        minimum_event_body_ratio
                                    ),
                                    "liquidity_sweep_confirmed": (
                                        liquidity_confirmed
                                    ),
                                    "lookback_bars": lookback_bars,
                                },
                            )

                            registry.add(order_block)

                            if structure_event == "MSS":
                                # Decision #12 (§28 point 4): remember
                                # this MSS occurrence's own block for
                                # the promotion check (Section 1c)
                                # whenever its eventual CHoCH (if any)
                                # confirms on a later row.
                                pending_mss_source_event_id = (
                                    source_event_id
                                )

                            result.at[
                                index,
                                "order_block_created",
                            ] = True
                            result.at[
                                index,
                                "order_block_id",
                            ] = order_block_id
                            result.at[
                                index,
                                "order_block_type",
                            ] = event_direction
                            result.at[
                                index,
                                "order_block_high",
                            ] = source_high
                            result.at[
                                index,
                                "order_block_low",
                            ] = source_low
                            result.at[
                                index,
                                "order_block_proximal",
                            ] = proximal_level
                            result.at[
                                index,
                                "order_block_distal",
                            ] = distal_level
                            result.at[
                                index,
                                "order_block_candle_index",
                            ] = source_position
                            result.at[
                                index,
                                "order_block_candle_time",
                            ] = source_time

                            event_number += 1
                            events.append(
                                MarketEvent(
                                    event_id=(
                                        f"EV_OB_{event_number:05d}"
                                    ),
                                    event_type=(
                                        "ORDER_BLOCK_CREATED"
                                    ),
                                    time=current_time,
                                    index=position,
                                    direction=event_direction,
                                    price=proximal_level,
                                    broken_level=broken_level,
                                    source_id=order_block_id,
                                    source_type=structure_event,
                                    description=(
                                        f"{event_direction.title()} "
                                        "Order Block created after "
                                        # "confirmed" is deliberately
                                        # omitted: MSS is tentative
                                        # (§2), not confirmed, so this
                                        # wording stays accurate for
                                        # BOS/MSS/CHoCH alike.
                                        f"{structure_event}."
                                    ),
                                    metadata={
                                        "source_event_id": (
                                            source_event_id
                                        ),
                                        "source_candle_index": (
                                            source_position
                                        ),
                                        "distal_level": distal_level,
                                        "proximal_level": (
                                            proximal_level
                                        ),
                                        "displacement_atr": (
                                            displacement_distance
                                            / atr_value
                                        ),
                                        "event_body_ratio": (
                                            event_body_ratio
                                        ),
                                        "liquidity_sweep_confirmed": (
                                            liquidity_confirmed
                                        ),
                                    },
                                )
                            )

        result.at[
            index,
            "active_bullish_order_blocks",
        ] = len(registry.active("bullish"))
        result.at[
            index,
            "active_bearish_order_blocks",
        ] = len(registry.active("bearish"))

    return result, registry, events
