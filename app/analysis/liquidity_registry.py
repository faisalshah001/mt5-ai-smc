from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.analysis.models import LiquidityPool, LiquidityType


@dataclass
class LiquidityRegistry:
    """
    Stores and manages all detected liquidity pools.
    """

    pools: list[LiquidityPool] = field(default_factory=list)

    def add(self, pool: LiquidityPool) -> None:
        """
        Add a new liquidity pool.

        Raises an error if the same liquidity ID already exists.
        """

        if self.get_by_id(pool.liquidity_id) is not None:
            raise ValueError(
                f"Liquidity pool already exists: {pool.liquidity_id}"
            )

        self.pools.append(pool)

    def get_by_id(
        self,
        liquidity_id: str,
    ) -> Optional[LiquidityPool]:
        """
        Find a liquidity pool by ID.
        """

        for pool in self.pools:
            if pool.liquidity_id == liquidity_id:
                return pool

        return None

    def all(self) -> list[LiquidityPool]:
        """
        Return all liquidity pools.
        """

        return list(self.pools)

    def active(
        self,
        liquidity_type: Optional[LiquidityType] = None,
    ) -> list[LiquidityPool]:
        """
        Return active liquidity pools.

        Optionally filter by BSL or SSL.
        """

        active_pools = [
            pool
            for pool in self.pools
            if pool.is_active
        ]

        if liquidity_type is not None:
            active_pools = [
                pool
                for pool in active_pools
                if pool.liquidity_type == liquidity_type
            ]

        return active_pools

    def swept(
        self,
        liquidity_type: Optional[LiquidityType] = None,
    ) -> list[LiquidityPool]:
        """
        Return swept liquidity pools.
        """

        swept_pools = [
            pool
            for pool in self.pools
            if pool.swept
        ]

        if liquidity_type is not None:
            swept_pools = [
                pool
                for pool in swept_pools
                if pool.liquidity_type == liquidity_type
            ]

        return swept_pools

    def broken(
        self,
        liquidity_type: Optional[LiquidityType] = None,
    ) -> list[LiquidityPool]:
        """
        Return genuinely broken liquidity pools.
        """

        broken_pools = [
            pool
            for pool in self.pools
            if pool.broken
        ]

        if liquidity_type is not None:
            broken_pools = [
                pool
                for pool in broken_pools
                if pool.liquidity_type == liquidity_type
            ]

        return broken_pools

    def expire_old_pools(
        self,
        *,
        current_time: datetime,
        current_index: int,
        maximum_age_bars: int,
    ) -> None:
        """
        Expire active pools older than maximum_age_bars.
        """

        if maximum_age_bars <= 0:
            raise ValueError(
                "maximum_age_bars must be greater than zero."
            )

        for pool in self.active():
            age_bars = current_index - pool.created_index

            if age_bars >= maximum_age_bars:
                pool.mark_expired(
                    time=current_time,
                    index=current_index,
                )

    def to_list(self) -> list[dict]:
        """
        Convert all liquidity pools to dictionaries.
        """

        return [
            pool.to_dict()
            for pool in self.pools
        ]

    def __len__(self) -> int:
        return len(self.pools)

    def __iter__(self):
        return iter(self.pools)