"""
Baseline coverage for app.analysis.order_blocks.detect_order_blocks.

Includes a golden-file snapshot of the full canonical pipeline over
the real EURUSD H4 fixture. Two tests below originally pinned the
pre-Phase-6 gap (SMC_SPECIFICATION.md §28, Decision #12): MSS was
hard-rejected as a source event type, and OrderBlock had no
confirmation_status field. Both gaps are now closed (Phase 6); the
tests have been updated in place to pin the corresponding fixed
behaviour, per this project's established practice of never silently
deleting a test that describes behaviour the code no longer has.
"""

from __future__ import annotations

from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import detect_swing_points
from app.analysis.order_blocks import detect_order_blocks
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import dataframe_to_records, objects_to_records


def _run_canonical_pipeline_through_order_blocks(candles):
    # Decision #3 (Phase 7): detect_structure_state performs
    # per-cycle classification itself — no separate
    # classify_market_structure pass in the canonical pipeline.
    indicators = calculate_indicators(candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    structure = detect_structure_state(swings, minimum_break_atr=0.10)
    liquidity_dataframe, _, _ = detect_liquidity_registry(structure)
    return detect_order_blocks(liquidity_dataframe)


def test_order_block_dataframe_matches_golden(eurusd_h4_candles):
    dataframe, registry, events = _run_canonical_pipeline_through_order_blocks(
        eurusd_h4_candles
    )

    columns = [
        "time",
        "order_block_created",
        "order_block_id",
        "order_block_type",
        "order_block_proximal",
        "order_block_distal",
        "order_block_mitigated",
        "mitigated_order_block_id",
        "order_block_invalidated",
        "invalidated_order_block_id",
        "active_bullish_order_blocks",
        "active_bearish_order_blocks",
    ]

    assert_matches_golden(
        "order_block_dataframe_eurusd_h4",
        dataframe_to_records(dataframe[columns]),
    )


def test_order_block_registry_matches_golden(eurusd_h4_candles):
    _, registry, _ = _run_canonical_pipeline_through_order_blocks(
        eurusd_h4_candles
    )

    assert_matches_golden(
        "order_block_registry_eurusd_h4",
        objects_to_records(registry.all(sorted_by_time=True)),
    )


def test_order_block_events_match_golden(eurusd_h4_candles):
    _, _, events = _run_canonical_pipeline_through_order_blocks(
        eurusd_h4_candles
    )

    assert_matches_golden(
        "order_block_events_eurusd_h4",
        objects_to_records(events),
    )


def test_mss_is_accepted_as_a_source_event_type(eurusd_h4_candles):
    # §28, Decision #12 (Phase 6): MSS is now an accepted source event
    # type — including on its own, without BOS/CHoCH also enabled.
    indicators = calculate_indicators(eurusd_h4_candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    structure = detect_structure_state(swings, minimum_break_atr=0.10)
    liquidity_dataframe, _, _ = detect_liquidity_registry(structure)

    dataframe, registry, events = detect_order_blocks(
        liquidity_dataframe,
        source_event_types=("MSS",),
    )

    mss_sourced = [
        block
        for block in registry.all()
        if block.source_event_type == "MSS"
    ]
    assert mss_sourced
    assert all(
        block.confirmation_status == "provisional"
        for block in mss_sourced
        if block.status == "active"
    )


def test_mss_is_included_by_default(eurusd_h4_candles):
    # Decision #12 point 8 / §28: "not a configurable option" —
    # default behaviour changes for every existing caller. Blocks now
    # appear where none did before, with no source_event_types
    # override required.
    indicators = calculate_indicators(eurusd_h4_candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    structure = detect_structure_state(swings, minimum_break_atr=0.10)
    liquidity_dataframe, _, _ = detect_liquidity_registry(structure)

    _, registry, _ = detect_order_blocks(liquidity_dataframe)

    assert any(
        block.source_event_type == "MSS" for block in registry.all()
    )


def test_confirmation_status_field_now_exists():
    # Decision #12 (Phase 6): OrderBlock gained confirmation_status,
    # confirming_event_id/type, confirmed_time/index, and
    # invalidation_reason; mark_confirmed() is a new lifecycle method.
    from app.analysis.models import OrderBlock
    from datetime import datetime, timezone

    block = OrderBlock(
        order_block_id="OB_TEST",
        order_block_type="bullish",
        created_time=datetime.now(timezone.utc),
        created_index=0,
        candle_time=datetime.now(timezone.utc),
        candle_index=0,
        high=1.1,
        low=1.0,
        open=1.05,
        close=1.08,
        proximal_level=1.08,
        distal_level=1.0,
    )

    # BOS/CHoCH-sourced blocks (the dataclass default) are confirmed
    # by construction — terminal from the start.
    assert block.confirmation_status == "confirmed"
    assert block.confirming_event_id is None
    assert block.invalidation_reason is None

    block.mark_confirmed(
        time=datetime.now(timezone.utc),
        index=5,
        confirming_event_id="STR_CHoCH_00005",
        confirming_event_type="CHoCH",
    )

    assert block.confirmation_status == "confirmed"
    assert block.confirming_event_id == "STR_CHoCH_00005"
    assert block.confirming_event_type == "CHoCH"
    assert block.confirmed_index == 5
