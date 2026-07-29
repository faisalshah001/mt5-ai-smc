"""
Hand-verified baseline coverage for
app.analysis.market_structure.classify_market_structure.

Pins the legacy engine's current, whole-series, never-reset
comparison behaviour (Decision #3, §7, targets this for replacement
in the *canonical* engine only — the legacy engine is explicitly
frozen through Decision B's Phase 1/2, so this behaviour must remain
exactly as tested here until Phase 8 of the roadmap).
"""

from __future__ import annotations

import pandas as pd

from app.analysis.market_structure import (
    classify_market_structure,
    detect_swing_points,
)
from tests.helpers.candles import build_zigzag_candles
from tests.helpers.dataframe_compare import cell


def test_first_swing_of_each_type_is_unlabeled_then_classified():
    candles = build_zigzag_candles(
        [1.1000, 1.1100, 1.0950, 1.1200, 1.1050, 1.1300],
        candles_per_leg=8,
    )

    swings = detect_swing_points(candles, left_bars=3, right_bars=3)
    result = classify_market_structure(swings)

    # First swing high (position 8) and first swing low (position 16)
    # have no prior reference of their own type and must stay
    # unlabeled.
    assert cell(result, 8, "structure") is None
    assert cell(result, 16, "structure") is None

    # Second swing high (position 24, higher than position 8) -> HH.
    assert cell(result, 24, "structure") == "HH"

    # Second swing low (position 32, higher than position 16) -> HL.
    assert cell(result, 32, "structure") == "HL"


def test_equal_high_classifies_as_lh_not_hh():
    # Decision #4: exact-tie swings fold into LH/LL via the existing
    # strict '>' comparison. This is current, approved behaviour.
    frame = pd.DataFrame(
        {
            "high": [1.1000, 1.1000],
            "low": [1.0900, 1.0900],
            "swing_high": [True, True],
            "swing_low": [False, False],
        }
    )

    result = classify_market_structure(frame)

    assert cell(result, 0, "structure") is None
    assert cell(result, 1, "structure") == "LH"


def test_equal_low_classifies_as_ll_not_hl():
    frame = pd.DataFrame(
        {
            "high": [1.1000, 1.1000],
            "low": [1.0900, 1.0900],
            "swing_high": [False, False],
            "swing_low": [True, True],
        }
    )

    result = classify_market_structure(frame)

    assert cell(result, 0, "structure") is None
    assert cell(result, 1, "structure") == "LL"


def test_comparison_baseline_is_never_reset_across_the_series():
    # The legacy classifier tracks previous_high/previous_low
    # globally across the *entire* series with no reset — verified
    # here with an up-down-up-down-up zigzag where a later swing high
    # is compared against the last swing HIGH specifically, never
    # against an intervening swing LOW, and with no cycle-boundary
    # concept resetting the comparison (classify_market_structure has
    # no notion of cycles at all today — Decision #3 introduces one
    # only for the canonical engine).
    candles = build_zigzag_candles(
        [
            1.1000,
            1.1500,  # swing high #1 (unlabeled, first of type)
            1.1400,  # swing low #1 (unlabeled, first of type)
            1.1450,  # swing high #2: 1.1450 < 1.1500 -> LH
            1.1420,  # swing low #2: 1.1420 > 1.1400 -> HL
            1.1600,  # swing high #3: 1.1600 > 1.1450 (prior HIGH,
                     # not the intervening LH) -> HH
            1.1500,  # trailing buffer leg, keeps position 40 inside
                     # detect_swing_points' valid (non-edge) range
        ],
        candles_per_leg=8,
    )

    swings = detect_swing_points(candles, left_bars=3, right_bars=3)
    result = classify_market_structure(swings)

    assert cell(result, 8, "structure") is None
    assert cell(result, 16, "structure") is None
    assert cell(result, 24, "structure") == "LH"
    assert cell(result, 32, "structure") == "HL"
    assert cell(result, 40, "structure") == "HH"
