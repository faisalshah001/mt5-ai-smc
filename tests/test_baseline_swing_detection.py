"""
Hand-verified baseline coverage for
app.analysis.market_structure.detect_swing_points.

These are not golden-file snapshots: the expected swing positions are
derivable by construction from the zigzag fixture's waypoints, so
correctness of the *current* behaviour is verified directly rather
than merely pinned.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.market_structure import detect_swing_points
from tests.helpers.candles import build_zigzag_candles


def test_swing_highs_and_lows_at_expected_positions():
    # Waypoints alternate rising/falling; interior waypoints (not the
    # very first or very last) must register as swings.
    candles = build_zigzag_candles(
        [1.1000, 1.1100, 1.0950, 1.1200, 1.1050, 1.1300],
        candles_per_leg=8,
    )

    result = detect_swing_points(candles, left_bars=3, right_bars=3)

    swing_high_positions = set(
        result.index[result["swing_high"]].tolist()
    )
    swing_low_positions = set(
        result.index[result["swing_low"]].tolist()
    )

    # Waypoints land at candle positions 0, 8, 16, 24, 32, 40.
    # Position 0 and 40 fall outside detect_swing_points' valid
    # range(left_bars, len(result) - right_bars) and must never be
    # reported as swings, regardless of their price prominence.
    assert swing_high_positions == {8, 24}
    assert swing_low_positions == {16, 32}

    for position in swing_high_positions:
        assert (
            result.at[position, "swing_high_price"]
            == result.at[position, "high"]
        )

    for position in swing_low_positions:
        assert (
            result.at[position, "swing_low_price"]
            == result.at[position, "low"]
        )


def test_tied_high_is_excluded_from_swing_detection():
    # Two candles share the exact same high within the lookback
    # window. Strict '>' comparison means neither qualifies.
    highs = [1.10, 1.10, 1.10, 1.12, 1.12, 1.10, 1.10, 1.10]
    lows = [value - 0.01 for value in highs]

    candles = pd.DataFrame(
        {
            "high": highs,
            "low": lows,
        }
    )

    result = detect_swing_points(candles, left_bars=3, right_bars=3)

    assert result["swing_high"].sum() == 0


def test_asymmetric_left_right_bars_are_respected():
    candles = build_zigzag_candles(
        [1.1000, 1.1100, 1.0950],
        candles_per_leg=8,
    )

    result = detect_swing_points(candles, left_bars=2, right_bars=5)

    # With right_bars=5, the valid range is
    # range(2, len(result) - 5) = range(2, 12); the peak at position 8
    # is still comfortably inside it.
    assert result.at[8, "swing_high"] == True  # noqa: E712


def test_minimum_candle_count_is_enforced():
    candles = pd.DataFrame({"high": [1.0, 1.1, 1.2], "low": [0.9, 1.0, 1.1]})

    with pytest.raises(ValueError):
        detect_swing_points(candles, left_bars=3, right_bars=3)


def test_invalid_left_right_bars_are_rejected():
    candles = build_zigzag_candles([1.10, 1.11], candles_per_leg=8)

    with pytest.raises(ValueError):
        detect_swing_points(candles, left_bars=0, right_bars=3)
