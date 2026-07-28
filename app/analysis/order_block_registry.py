from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

from app.analysis.models import (
    OrderBlock,
    OrderBlockStatus,
    OrderBlockType,
)


@dataclass
class OrderBlockRegistry:
    """
    Stores and manages all Order Block objects created by the analysis engine.
    """

    order_blocks: list[OrderBlock] = field(default_factory=list)

    def add(self, order_block: OrderBlock) -> None:
        """
        Add one Order Block to the registry.

        Order Block IDs must be unique.
        """

        if self.get_by_id(order_block.order_block_id) is not None:
            raise ValueError(
                f"Order Block already exists: "
                f"{order_block.order_block_id}"
            )

        self.order_blocks.append(order_block)

    def extend(
        self,
        order_blocks: Iterable[OrderBlock],
    ) -> None:
        """
        Add multiple Order Blocks.
        """

        for order_block in order_blocks:
            self.add(order_block)

    def get_by_id(
        self,
        order_block_id: str,
    ) -> Optional[OrderBlock]:
        """
        Return one Order Block by ID.
        """

        for order_block in self.order_blocks:
            if order_block.order_block_id == order_block_id:
                return order_block

        return None

    def all(
        self,
        *,
        sorted_by_time: bool = False,
    ) -> list[OrderBlock]:
        """
        Return all Order Blocks.

        Set sorted_by_time=True to sort chronologically.
        """

        order_blocks = list(self.order_blocks)

        if sorted_by_time:
            order_blocks.sort(
                key=lambda order_block: (
                    order_block.created_time,
                    order_block.created_index,
                    order_block.order_block_id,
                )
            )

        return order_blocks

    def active(
        self,
        order_block_type: OrderBlockType | None = None,
    ) -> list[OrderBlock]:
        """
        Return active Order Blocks.

        Optionally filter by bullish or bearish.
        """

        order_blocks = [
            order_block
            for order_block in self.order_blocks
            if order_block.is_active
        ]

        if order_block_type is not None:
            order_blocks = [
                order_block
                for order_block in order_blocks
                if order_block.order_block_type
                == order_block_type
            ]

        return order_blocks

    def mitigated(
        self,
        order_block_type: OrderBlockType | None = None,
    ) -> list[OrderBlock]:
        """
        Return mitigated Order Blocks.
        """

        order_blocks = [
            order_block
            for order_block in self.order_blocks
            if order_block.mitigated
        ]

        if order_block_type is not None:
            order_blocks = [
                order_block
                for order_block in order_blocks
                if order_block.order_block_type
                == order_block_type
            ]

        return order_blocks

    def invalidated(
        self,
        order_block_type: OrderBlockType | None = None,
    ) -> list[OrderBlock]:
        """
        Return invalidated Order Blocks.
        """

        order_blocks = [
            order_block
            for order_block in self.order_blocks
            if order_block.invalidated
        ]

        if order_block_type is not None:
            order_blocks = [
                order_block
                for order_block in order_blocks
                if order_block.order_block_type
                == order_block_type
            ]

        return order_blocks

    def expired(
        self,
        order_block_type: OrderBlockType | None = None,
    ) -> list[OrderBlock]:
        """
        Return expired Order Blocks.
        """

        order_blocks = [
            order_block
            for order_block in self.order_blocks
            if order_block.expired
        ]

        if order_block_type is not None:
            order_blocks = [
                order_block
                for order_block in order_blocks
                if order_block.order_block_type
                == order_block_type
            ]

        return order_blocks

    def by_type(
        self,
        order_block_type: OrderBlockType,
    ) -> list[OrderBlock]:
        """
        Return all bullish or bearish Order Blocks.
        """

        return [
            order_block
            for order_block in self.order_blocks
            if order_block.order_block_type
            == order_block_type
        ]

    def by_status(
        self,
        status: OrderBlockStatus,
    ) -> list[OrderBlock]:
        """
        Return Order Blocks matching one status.
        """

        return [
            order_block
            for order_block in self.order_blocks
            if order_block.status == status
        ]

    def by_source_event_id(
        self,
        source_event_id: str,
    ) -> list[OrderBlock]:
        """
        Return Order Blocks created from one market event.
        """

        return [
            order_block
            for order_block in self.order_blocks
            if order_block.source_event_id
            == source_event_id
        ]

    def latest(
        self,
        order_block_type: OrderBlockType | None = None,
        status: OrderBlockStatus | None = None,
    ) -> Optional[OrderBlock]:
        """
        Return the latest matching Order Block.
        """

        candidates = self.order_blocks

        if order_block_type is not None:
            candidates = [
                order_block
                for order_block in candidates
                if order_block.order_block_type
                == order_block_type
            ]

        if status is not None:
            candidates = [
                order_block
                for order_block in candidates
                if order_block.status == status
            ]

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda order_block: (
                order_block.created_time,
                order_block.created_index,
                order_block.order_block_id,
            ),
        )

    def expire_old_order_blocks(
        self,
        *,
        current_time: datetime,
        current_index: int,
        maximum_age_bars: int,
    ) -> list[OrderBlock]:
        """
        Expire active Order Blocks older than maximum_age_bars.

        Returns the Order Blocks that were expired.
        """

        if maximum_age_bars < 1:
            raise ValueError(
                "maximum_age_bars must be at least 1."
            )

        expired_order_blocks: list[OrderBlock] = []

        for order_block in self.active():
            age_bars = (
                current_index
                - order_block.created_index
            )

            if age_bars >= maximum_age_bars:
                order_block.mark_expired(
                    time=current_time,
                    index=current_index,
                )

                expired_order_blocks.append(order_block)

        return expired_order_blocks

    def count(
        self,
        order_block_type: OrderBlockType | None = None,
        status: OrderBlockStatus | None = None,
    ) -> int:
        """
        Count Order Blocks using optional filters.
        """

        candidates = self.order_blocks

        if order_block_type is not None:
            candidates = [
                order_block
                for order_block in candidates
                if order_block.order_block_type
                == order_block_type
            ]

        if status is not None:
            candidates = [
                order_block
                for order_block in candidates
                if order_block.status == status
            ]

        return len(candidates)

    def to_list(
        self,
        *,
        sorted_by_time: bool = True,
    ) -> list[dict]:
        """
        Convert Order Blocks into serialisable dictionaries.
        """

        return [
            order_block.to_dict()
            for order_block in self.all(
                sorted_by_time=sorted_by_time
            )
        ]

    def to_dataframe(
        self,
        *,
        sorted_by_time: bool = True,
    ) -> pd.DataFrame:
        """
        Convert the registry into a DataFrame.
        """

        data = self.to_list(
            sorted_by_time=sorted_by_time
        )

        if not data:
            return pd.DataFrame()

        return pd.DataFrame(data)

    def __len__(self) -> int:
        return len(self.order_blocks)

    def __iter__(self):
        return iter(self.order_blocks)