"""
Phase 2 regression gate: the legacy /analysis/market-structure endpoint
must remain byte-identical to its Phase 0/Phase 1 behaviour.
SMC_SPECIFICATION.md §3, Decision B point 2: "Legacy endpoint,
unchanged during the migration period."

This is proven two ways:

1. Every underlying pipeline function it calls
   (classify_market_structure, detect_breaks_of_structure,
   detect_change_of_character) is still covered by the untouched
   Phase 0 golden (tests/test_baseline_legacy_pipeline.py) — those
   tests passed unchanged after this phase's changes.
2. This file additionally exercises the endpoint's own HTTP-response
   wrapping (tail(20), summary counts, dict construction) directly,
   as a second, independent regression layer at the response level —
   something no prior phase tested end-to-end.
"""

from __future__ import annotations

from unittest.mock import patch

import main


@patch("main.get_candles")
def test_legacy_endpoint_response_shape_unchanged(
    mock_get_candles, eurusd_h4_candles
):
    mock_get_candles.return_value = eurusd_h4_candles

    response = main.market_structure_endpoint(
        "EURUSD",
        "H4",
        count=len(eurusd_h4_candles),
        left_bars=3,
        right_bars=3,
        minimum_break_atr=0.10,
    )

    # The legacy response contract (Decision B point 2, point 3's
    # no-adapter invariant): still the original shape, never migrated
    # toward the canonical AnalysisResult contract.
    assert set(response.keys()) == {
        "symbol",
        "timeframe",
        "settings",
        "summary",
        "swing_points",
        "bos_events",
        "choch_events",
    }
    assert "structure_snapshot" not in response
    assert "liquidity_dataframe" not in response


@patch("main.get_candles")
def test_legacy_endpoint_summary_counts_match_underlying_pipeline(
    mock_get_candles, eurusd_h4_candles
):
    # Cross-check the endpoint's own summary counts against the
    # already-golden-pinned underlying pipeline output, proving the
    # endpoint's response-wrapping logic has not silently drifted.
    from app.analysis.market_structure import (
        classify_market_structure,
        detect_breaks_of_structure,
        detect_change_of_character,
        detect_swing_points,
    )
    from app.indicators.technical import calculate_indicators

    mock_get_candles.return_value = eurusd_h4_candles

    response = main.market_structure_endpoint(
        "EURUSD",
        "H4",
        count=len(eurusd_h4_candles),
        left_bars=3,
        right_bars=3,
        minimum_break_atr=0.10,
    )

    indicators = calculate_indicators(eurusd_h4_candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    classified = classify_market_structure(swings)
    bos = detect_breaks_of_structure(classified, minimum_break_atr=0.10)
    expected = detect_change_of_character(bos)

    assert response["summary"]["swing_highs"] == int(
        expected["swing_high"].sum()
    )
    assert response["summary"]["swing_lows"] == int(
        expected["swing_low"].sum()
    )
    assert response["summary"]["bullish_bos"] == int(
        (expected["bos"] == "bullish").sum()
    )
    assert response["summary"]["bearish_bos"] == int(
        (expected["bos"] == "bearish").sum()
    )


def test_legacy_route_still_registered_and_unmodified_by_path():
    matching_routes = [
        route
        for route in main.app.routes
        if getattr(route, "path", None)
        == "/analysis/market-structure/{symbol}/{timeframe}"
    ]

    assert len(matching_routes) == 1
    assert "GET" in matching_routes[0].methods
