from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

import pandas as pd


EventType = Literal[
    "BOS",
    "MSS",
    "CHoCH",
    "LIQUIDITY_CREATED",
    "LIQUIDITY_SWEPT",
    "LIQUIDITY_BROKEN",
    "LIQUIDITY_EXPIRED",
    "ORDER_BLOCK_CREATED",
    "ORDER_BLOCK_MITIGATED",
    "ORDER_BLOCK_INVALIDATED",
    "FVG_CREATED",
    "FVG_FILLED",
]

Direction = Literal[
    "bullish",
    "bearish",
    "neutral",
]

LiquidityType = Literal[
    "BSL",
    "SSL",
]

LiquidityStatus = Literal[
    "active",
    "swept",
    "broken",
    "consumed",
    "expired",
]

OrderBlockType = Literal[
    "bullish",
    "bearish",
]

OrderBlockStatus = Literal[
    "active",
    "mitigated",
    "invalidated",
    "expired",
]


@dataclass
class MarketEvent:
    """
    Represents a market event detected by the analysis engine.

    Examples:
        BOS
        MSS
        CHoCH
        Liquidity sweep
        Order block creation
        FVG fill
    """

    event_id: str
    event_type: EventType
    time: datetime
    index: int

    direction: Direction = "neutral"

    price: Optional[float] = None
    broken_level: Optional[float] = None

    source_id: Optional[str] = None
    source_type: Optional[str] = None

    strength: Optional[float] = None
    description: Optional[str] = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the event into a serialisable dictionary.
        """

        data = asdict(self)

        if isinstance(self.time, datetime):
            data["time"] = self.time.isoformat()

        return data


@dataclass
class LiquidityPool:
    """
    Represents one independent liquidity pool.

    BSL:
        Buy-side liquidity located above equal highs.

    SSL:
        Sell-side liquidity located below equal lows.
    """

    liquidity_id: str
    liquidity_type: LiquidityType
    level: float

    created_time: datetime
    created_index: int

    first_swing_time: Optional[datetime] = None
    first_swing_index: Optional[int] = None
    first_swing_price: Optional[float] = None

    second_swing_time: Optional[datetime] = None
    second_swing_index: Optional[int] = None
    second_swing_price: Optional[float] = None

    status: LiquidityStatus = "active"

    swept: bool = False
    swept_time: Optional[datetime] = None
    swept_index: Optional[int] = None
    sweep_price: Optional[float] = None
    sweep_close: Optional[float] = None
    sweep_distance: Optional[float] = None
    sweep_distance_pips: Optional[float] = None
    sweep_direction: Optional[Direction] = None

    broken: bool = False
    broken_time: Optional[datetime] = None
    broken_index: Optional[int] = None
    break_price: Optional[float] = None
    break_close: Optional[float] = None
    break_distance: Optional[float] = None
    break_distance_pips: Optional[float] = None

    consumed: bool = False
    consumed_time: Optional[datetime] = None
    consumed_index: Optional[int] = None

    expired: bool = False
    expired_time: Optional[datetime] = None
    expired_index: Optional[int] = None

    touches: int = 2

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """
        Return True when this liquidity pool is still available.
        """

        return (
            self.status == "active"
            and not self.swept
            and not self.broken
            and not self.consumed
            and not self.expired
        )

    def mark_swept(
        self,
        *,
        time: datetime,
        index: int,
        sweep_price: float,
        sweep_close: float,
        sweep_distance: float,
        sweep_distance_pips: float,
        direction: Direction,
    ) -> None:
        """
        Mark this liquidity pool as swept.
        """

        self.status = "swept"

        self.swept = True
        self.swept_time = time
        self.swept_index = index

        self.sweep_price = sweep_price
        self.sweep_close = sweep_close

        self.sweep_distance = sweep_distance
        self.sweep_distance_pips = sweep_distance_pips
        self.sweep_direction = direction

    def mark_broken(
        self,
        *,
        time: datetime,
        index: int,
        break_price: float,
        break_close: float,
        break_distance: float,
        break_distance_pips: float,
    ) -> None:
        """
        Mark this liquidity pool as genuinely broken.
        """

        self.status = "broken"

        self.broken = True
        self.broken_time = time
        self.broken_index = index

        self.break_price = break_price
        self.break_close = break_close

        self.break_distance = break_distance
        self.break_distance_pips = break_distance_pips

    def mark_consumed(
        self,
        *,
        time: datetime,
        index: int,
    ) -> None:
        """
        Mark this liquidity pool as consumed.
        """

        self.status = "consumed"

        self.consumed = True
        self.consumed_time = time
        self.consumed_index = index

    def mark_expired(
        self,
        *,
        time: datetime,
        index: int,
    ) -> None:
        """
        Mark this liquidity pool as expired.
        """

        self.status = "expired"

        self.expired = True
        self.expired_time = time
        self.expired_index = index

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the liquidity object into a serialisable dictionary.
        """

        data = asdict(self)

        datetime_fields = [
            "created_time",
            "first_swing_time",
            "second_swing_time",
            "swept_time",
            "broken_time",
            "consumed_time",
            "expired_time",
        ]

        for field_name in datetime_fields:
            value = data.get(field_name)

            if isinstance(value, datetime):
                data[field_name] = value.isoformat()

        data["is_active"] = self.is_active

        return data


@dataclass
class StructureSnapshot:
    """
    Represents the latest market-structure state.
    """

    external_trend: Direction = "neutral"
    structure_state: str = "neutral"

    latest_swing_high: Optional[float] = None
    latest_swing_low: Optional[float] = None

    protected_high: Optional[float] = None
    protected_low: Optional[float] = None

    latest_event: Optional[str] = None
    latest_event_direction: Optional[Direction] = None

    updated_time: Optional[datetime] = None
    updated_index: Optional[int] = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the snapshot into a serialisable dictionary.
        """

        data = asdict(self)

        if isinstance(self.updated_time, datetime):
            data["updated_time"] = self.updated_time.isoformat()

        return data


@dataclass
class OrderBlock:
    """
    Represents one bullish or bearish Order Block.

    Bullish Order Block:
        Usually the final bearish candle before bullish displacement
        that causes a confirmed structural break.

    Bearish Order Block:
        Usually the final bullish candle before bearish displacement
        that causes a confirmed structural break.
    """

    order_block_id: str
    order_block_type: OrderBlockType

    created_time: datetime
    created_index: int

    candle_time: datetime
    candle_index: int

    high: float
    low: float
    open: float
    close: float

    proximal_level: float
    distal_level: float

    status: OrderBlockStatus = "active"

    source_event_id: Optional[str] = None
    source_event_type: Optional[str] = None
    source_broken_level: Optional[float] = None

    displacement_start_index: Optional[int] = None
    displacement_end_index: Optional[int] = None
    displacement_distance: Optional[float] = None
    displacement_distance_pips: Optional[float] = None

    mitigated: bool = False
    mitigated_time: Optional[datetime] = None
    mitigated_index: Optional[int] = None
    mitigation_price: Optional[float] = None
    mitigation_depth: Optional[float] = None
    mitigation_percentage: Optional[float] = None

    invalidated: bool = False
    invalidated_time: Optional[datetime] = None
    invalidated_index: Optional[int] = None
    invalidation_price: Optional[float] = None

    expired: bool = False
    expired_time: Optional[datetime] = None
    expired_index: Optional[int] = None

    touches: int = 0

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """
        Return True while the Order Block remains tradable.
        """

        return (
            self.status == "active"
            and not self.mitigated
            and not self.invalidated
            and not self.expired
        )

    @property
    def range_size(self) -> float:
        """
        Return the full price range of the Order Block.
        """

        return abs(self.high - self.low)

    def contains_price(self, price: float) -> bool:
        """
        Return True when a price is inside the Order Block range.
        """

        return self.low <= price <= self.high

    def mark_mitigated(
        self,
        *,
        time: datetime,
        index: int,
        price: float,
    ) -> None:
        """
        Mark the Order Block as mitigated.

        Mitigation occurs when price returns into the Order Block.
        """

        self.status = "mitigated"
        self.mitigated = True
        self.mitigated_time = time
        self.mitigated_index = index
        self.mitigation_price = price
        self.touches += 1

        block_range = self.range_size

        if block_range <= 0:
            self.mitigation_depth = 0.0
            self.mitigation_percentage = 0.0
            return

        if self.order_block_type == "bullish":
            depth = self.high - price
        else:
            depth = price - self.low

        depth = max(0.0, min(depth, block_range))

        self.mitigation_depth = depth
        self.mitigation_percentage = (
            depth / block_range
        ) * 100.0

    def mark_invalidated(
        self,
        *,
        time: datetime,
        index: int,
        price: float,
    ) -> None:
        """
        Mark the Order Block as invalidated.
        """

        self.status = "invalidated"
        self.invalidated = True
        self.invalidated_time = time
        self.invalidated_index = index
        self.invalidation_price = price

    def mark_expired(
        self,
        *,
        time: datetime,
        index: int,
    ) -> None:
        """
        Mark the Order Block as expired.
        """

        self.status = "expired"
        self.expired = True
        self.expired_time = time
        self.expired_index = index

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the Order Block into a serialisable dictionary.
        """

        data = asdict(self)

        datetime_fields = [
            "created_time",
            "candle_time",
            "mitigated_time",
            "invalidated_time",
            "expired_time",
        ]

        for field_name in datetime_fields:
            value = data.get(field_name)

            if isinstance(value, datetime):
                data[field_name] = value.isoformat()

        data["is_active"] = self.is_active
        data["range_size"] = self.range_size

        return data


@dataclass
class AnalysisResult:
    """
    Final structured output returned by the analysis engine.
    """

    symbol: str
    timeframe: str

    candles: pd.DataFrame

    structure: Optional[pd.DataFrame] = None
    liquidity_dataframe: Optional[pd.DataFrame] = None

    events: list[MarketEvent] = field(default_factory=list)
    liquidity: list[LiquidityPool] = field(default_factory=list)
    order_blocks: list[OrderBlock] = field(default_factory=list)

    structure_snapshot: Optional[StructureSnapshot] = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def active_liquidity(
        self,
        liquidity_type: Optional[LiquidityType] = None,
    ) -> list[LiquidityPool]:
        """
        Return all active liquidity pools.

        Optionally filter by BSL or SSL.
        """

        pools = [
            pool
            for pool in self.liquidity
            if pool.is_active
        ]

        if liquidity_type is not None:
            pools = [
                pool
                for pool in pools
                if pool.liquidity_type == liquidity_type
            ]

        return pools

    def swept_liquidity(
        self,
        liquidity_type: Optional[LiquidityType] = None,
    ) -> list[LiquidityPool]:
        """
        Return all swept liquidity pools.
        """

        pools = [
            pool
            for pool in self.liquidity
            if pool.swept
        ]

        if liquidity_type is not None:
            pools = [
                pool
                for pool in pools
                if pool.liquidity_type == liquidity_type
            ]

        return pools

    def active_order_blocks(
        self,
        order_block_type: Optional[OrderBlockType] = None,
    ) -> list[OrderBlock]:
        """
        Return all active Order Blocks.

        Optionally filter by bullish or bearish.
        """

        blocks = [
            block
            for block in self.order_blocks
            if block.is_active
        ]

        if order_block_type is not None:
            blocks = [
                block
                for block in blocks
                if block.order_block_type == order_block_type
            ]

        return blocks

    def mitigated_order_blocks(
        self,
        order_block_type: Optional[OrderBlockType] = None,
    ) -> list[OrderBlock]:
        """
        Return all mitigated Order Blocks.
        """

        blocks = [
            block
            for block in self.order_blocks
            if block.mitigated
        ]

        if order_block_type is not None:
            blocks = [
                block
                for block in blocks
                if block.order_block_type == order_block_type
            ]

        return blocks

    def order_blocks_to_dataframe(self) -> pd.DataFrame:
        """
        Convert the Order Block registry into a DataFrame.
        """

        if not self.order_blocks:
            return pd.DataFrame()

        return pd.DataFrame(
            [
                block.to_dict()
                for block in self.order_blocks
            ]
        )

    def events_by_type(
        self,
        event_type: EventType,
    ) -> list[MarketEvent]:
        """
        Return events matching a particular event type.
        """

        return [
            event
            for event in self.events
            if event.event_type == event_type
        ]

    def liquidity_to_dataframe(self) -> pd.DataFrame:
        """
        Convert the liquidity registry into a DataFrame.
        """

        if not self.liquidity:
            return pd.DataFrame()

        return pd.DataFrame(
            [
                pool.to_dict()
                for pool in self.liquidity
            ]
        )

    def events_to_dataframe(self) -> pd.DataFrame:
        """
        Convert the event registry into a DataFrame.
        """

        if not self.events:
            return pd.DataFrame()

        return pd.DataFrame(
            [
                event.to_dict()
                for event in self.events
            ]
        )

    def summary(self) -> dict[str, Any]:
        """
        Return a compact analysis summary suitable for FastAPI.
        """

        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candle_count": len(self.candles),
            "event_count": len(self.events),
            "liquidity_count": len(self.liquidity),
            "active_liquidity_count": len(
                self.active_liquidity()
            ),
            "swept_liquidity_count": len(
                self.swept_liquidity()
            ),
            "order_block_count": len(self.order_blocks),
            "active_order_block_count": len(
                self.active_order_blocks()
            ),
            "mitigated_order_block_count": len(
                self.mitigated_order_blocks()
            ),
            "structure_snapshot": (
                self.structure_snapshot.to_dict()
                if self.structure_snapshot is not None
                else None
            ),
            "metadata": self.metadata,
        }