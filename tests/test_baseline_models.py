"""
Hand-verified baseline coverage for app.analysis.models dataclasses.

Originally pinned pre-implementation dataclass contracts for Decision
#12 (OrderBlock.confirmation_status etc.) and Decision #13
(MarketEvent.strength). Both are now implemented (Phases 5-6); the
relevant tests have been updated in place to pin the corresponding
fixed behaviour, not silently deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.analysis.models import (
    EventType,
    LiquidityPool,
    MarketEvent,
    OrderBlock,
)


def _now():
    return datetime.now(timezone.utc)


def test_market_event_strength_defaults_to_none_and_stays_none():
    # Decision #13 current-behaviour gap: strength is defined but
    # never populated anywhere in the codebase.
    event = MarketEvent(
        event_id="EV_TEST",
        event_type="BOS",
        time=_now(),
        index=0,
    )

    assert event.strength is None
    assert event.to_dict()["strength"] is None


def test_event_type_literal_reflects_implemented_decisions():
    # Decision #6 (Phase 4) added "MSS_INVALIDATED"; Decision #12
    # (Phase 6) added "ORDER_BLOCK_CONFIRMED". Both are additive — no
    # existing value removed or renamed.
    import typing

    allowed_values = typing.get_args(EventType)

    assert "MSS_INVALIDATED" in allowed_values
    assert "ORDER_BLOCK_CONFIRMED" in allowed_values

    # Additive means additive: every pre-Phase-4 value is still there.
    assert {"BOS", "MSS", "CHoCH"}.issubset(set(allowed_values))


def test_order_block_lifecycle_methods_current_contract():
    block = OrderBlock(
        order_block_id="OB_TEST",
        order_block_type="bullish",
        created_time=_now(),
        created_index=0,
        candle_time=_now(),
        candle_index=0,
        high=1.10,
        low=1.00,
        open=1.05,
        close=1.08,
        proximal_level=1.08,
        distal_level=1.00,
    )

    assert block.is_active is True
    assert block.contains_price(1.05) is True
    assert block.contains_price(1.20) is False

    block.mark_mitigated(time=_now(), index=1, price=1.05)

    assert block.status == "mitigated"
    assert block.is_active is False
    assert 0.0 <= block.mitigation_percentage <= 100.0


def test_order_block_mark_invalidated_reason_kwarg():
    # Decision #12 (Phase 6): mark_invalidated() gained an optional
    # keyword-only reason parameter, default "price_penetration".
    import inspect

    signature = inspect.signature(OrderBlock.mark_invalidated)
    assert "reason" in signature.parameters
    assert (
        signature.parameters["reason"].default == "price_penetration"
    )


def test_liquidity_pool_is_active_default():
    pool = LiquidityPool(
        liquidity_id="EQH_1",
        liquidity_type="BSL",
        level=1.10,
        created_time=_now(),
        created_index=0,
    )

    assert pool.is_active is True

    pool.mark_swept(
        time=_now(),
        index=1,
        sweep_price=1.101,
        sweep_close=1.098,
        sweep_distance=0.001,
        sweep_distance_pips=10.0,
        direction="bearish",
    )

    assert pool.is_active is False
    assert pool.status == "swept"
