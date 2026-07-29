"""
Golden-file coverage for app.analysis.liquidity.detect_liquidity_registry.

No decision in SMC_SPECIFICATION.md changes this file's code directly;
Decision #3 changes it only indirectly (different structure column
values feeding in, now that Phase 7 has implemented it). This
snapshot exists so that indirect regression becomes visible.
"""

from __future__ import annotations

from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import detect_swing_points
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import dataframe_to_records, objects_to_records


def _run_canonical_pipeline_through_liquidity(candles):
    # Decision #3 (Phase 7): detect_structure_state performs
    # per-cycle classification itself — no separate
    # classify_market_structure pass in the canonical pipeline.
    indicators = calculate_indicators(candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    structure = detect_structure_state(swings, minimum_break_atr=0.10)
    return detect_liquidity_registry(structure)


def test_liquidity_dataframe_matches_golden(eurusd_h4_candles):
    dataframe, registry, events = _run_canonical_pipeline_through_liquidity(
        eurusd_h4_candles
    )

    columns = [
        "time",
        "equal_high",
        "equal_low",
        "liquidity_created",
        "liquidity_type",
        "liquidity_level",
        "liquidity_id",
        "liquidity_swept",
        "liquidity_broken",
        "active_bsl_count",
        "active_ssl_count",
    ]

    assert_matches_golden(
        "liquidity_dataframe_eurusd_h4",
        dataframe_to_records(dataframe[columns]),
    )


def test_liquidity_registry_matches_golden(eurusd_h4_candles):
    _, registry, _ = _run_canonical_pipeline_through_liquidity(
        eurusd_h4_candles
    )

    assert_matches_golden(
        "liquidity_registry_eurusd_h4",
        objects_to_records(registry.all()),
    )


def test_liquidity_events_match_golden(eurusd_h4_candles):
    _, _, events = _run_canonical_pipeline_through_liquidity(
        eurusd_h4_candles
    )

    assert_matches_golden(
        "liquidity_events_eurusd_h4",
        objects_to_records(events),
    )
