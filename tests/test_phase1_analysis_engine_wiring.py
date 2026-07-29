"""
Phase 1: confirm analyze_market() delegates candle-data validation to
the new standalone component (SMC_SPECIFICATION.md §3, Decision A,
point 1: "analyze_market() calls it in place of its current private
_validate_input/_prepare_candles implementation, rather than keeping
a parallel copy").
"""

from __future__ import annotations

import pytest

from app.analysis.analysis_engine import analyze_market
from tests.helpers.candles import build_zigzag_candles


def test_analyze_market_rejects_infinite_values(eurusd_h4_candles):
    # ±infinity rejection did not exist anywhere in the codebase
    # before Phase 1 (SMC_SPECIFICATION.md §3, Decision A, point 3).
    corrupted = eurusd_h4_candles.copy()
    corrupted.loc[5, "high"] = float("inf")

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="H4", candles=corrupted)


def test_analyze_market_rejects_negative_infinite_values(eurusd_h4_candles):
    corrupted = eurusd_h4_candles.copy()
    corrupted.loc[5, "low"] = float("-inf")

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="H4", candles=corrupted)


def test_analyze_market_still_validates_symbol_and_timeframe():
    # _validate_parameters (the slimmed-down successor to the old
    # _validate_input) still owns this check — Decision A's component
    # is candle-data-only and never receives symbol/timeframe.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080],
        candles_per_leg=8,
    )

    with pytest.raises(ValueError):
        analyze_market(symbol="  ", timeframe="H4", candles=candles)

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="", candles=candles)
