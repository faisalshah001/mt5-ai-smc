"""
Golden-file coverage for the legacy market_structure.py BOS/CHoCH
pipeline (detect_breaks_of_structure, detect_change_of_character).

This pipeline is explicitly frozen by Decision B (SMC_SPECIFICATION.md
§3) through the roadmap's Phase 8 — it must produce byte-identical
output for as long as the legacy /analysis/market-structure endpoint
exists. This test is the regression gate for that guarantee.
"""

from __future__ import annotations

from app.analysis.market_structure import (
    classify_market_structure,
    detect_breaks_of_structure,
    detect_change_of_character,
    detect_swing_points,
)
from app.indicators.technical import calculate_indicators
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import dataframe_to_records


def _run_legacy_pipeline(candles):
    indicators = calculate_indicators(candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    classified = classify_market_structure(swings)
    bos = detect_breaks_of_structure(classified, minimum_break_atr=0.10)
    return detect_change_of_character(bos)


def test_legacy_pipeline_matches_golden(eurusd_h4_candles):
    result = _run_legacy_pipeline(eurusd_h4_candles)

    columns = [
        "time",
        "close",
        "swing_high",
        "swing_low",
        "structure",
        "bos",
        "choch",
        "broken_level",
        "break_distance",
        "required_break_distance",
    ]

    assert_matches_golden(
        "legacy_bos_choch_eurusd_h4",
        dataframe_to_records(result[columns]),
    )
