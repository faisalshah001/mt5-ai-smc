"""
Phase 1 coverage for app.analysis.candle_validation
(SMC_SPECIFICATION.md §3, Decision A).

This module is new in Phase 1, so these are not baseline/regression
tests — they verify the new component directly, including the one
genuinely new requirement (±infinity rejection) that did not exist in
the private _validate_input/_prepare_candles pair it replaces.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.candle_validation import validate_and_normalize_candles


def _valid_frame() -> pd.DataFrame:
    opens = [1.10, 1.11, 1.12, 1.11, 1.10, 1.09, 1.10, 1.11, 1.12, 1.13]
    closes = [1.11, 1.12, 1.11, 1.10, 1.09, 1.10, 1.11, 1.12, 1.13, 1.12]

    # Derive high/low from open/close so the OHLC relationship is
    # valid by construction, rather than risking a hand-typed error.
    highs = [max(o, c) + 0.002 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.002 for o, c in zip(opens, closes)]

    return pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=10, freq="1h", tz="UTC"
            ),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": [100, 110, 120, 90, 95, 105, 115, 125, 130, 108],
        }
    )


def test_well_formed_input_passes_through_unchanged():
    frame = _valid_frame()
    result = validate_and_normalize_candles(frame)

    assert list(result["close"]) == list(frame["close"])
    assert list(result["tick_volume"]) == list(frame["tick_volume"])
    assert isinstance(result["time"].dtype, pd.DatetimeTZDtype)
    assert list(result.index) == list(range(len(result)))


def test_numeric_epoch_time_is_coerced_to_utc_datetime():
    frame = _valid_frame()
    frame["time"] = (
        frame["time"].astype("int64") // 10**9
    )

    result = validate_and_normalize_candles(frame)

    assert isinstance(result["time"].dtype, pd.DatetimeTZDtype)
    assert str(result["time"].dt.tz) == "UTC"


def test_numeric_looking_strings_are_coerced():
    frame = _valid_frame()
    frame["close"] = frame["close"].astype(str)

    result = validate_and_normalize_candles(frame)

    assert pd.api.types.is_numeric_dtype(result["close"])


def test_unsorted_input_is_sorted_chronologically():
    frame = _valid_frame().iloc[::-1].reset_index(drop=True)

    result = validate_and_normalize_candles(frame)

    assert list(result["time"]) == sorted(result["time"])


def test_extra_columns_are_preserved_unchanged():
    frame = _valid_frame()
    frame["spread"] = 2
    frame["real_volume"] = 0

    result = validate_and_normalize_candles(frame)

    assert list(result["spread"]) == [2] * len(frame)
    assert list(result["real_volume"]) == [0] * len(frame)


def test_empty_dataframe_is_rejected():
    with pytest.raises(ValueError):
        validate_and_normalize_candles(pd.DataFrame())


def test_missing_required_columns_are_rejected():
    frame = _valid_frame().drop(columns=["close"])

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_nan_in_required_numeric_field_is_rejected():
    frame = _valid_frame()
    frame.loc[3, "close"] = float("nan")

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_positive_infinity_in_required_numeric_field_is_rejected():
    # This is the one genuinely new requirement Decision A adds:
    # nothing in the codebase checked for infinity before Phase 1.
    frame = _valid_frame()
    frame.loc[2, "high"] = float("inf")

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_negative_infinity_in_required_numeric_field_is_rejected():
    frame = _valid_frame()
    frame.loc[2, "low"] = float("-inf")

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_unparseable_timestamp_is_rejected():
    frame = _valid_frame()
    frame["time"] = frame["time"].astype(object)
    frame.loc[1, "time"] = "not a timestamp"

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_duplicate_timestamps_are_rejected():
    frame = _valid_frame()
    frame.loc[1, "time"] = frame.loc[0, "time"]

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_invalid_high_relationship_is_rejected():
    frame = _valid_frame()
    frame.loc[0, "high"] = frame.loc[0, "low"] - 0.001

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_invalid_low_relationship_is_rejected():
    frame = _valid_frame()
    frame.loc[0, "low"] = frame.loc[0, "high"] + 0.001

    with pytest.raises(ValueError):
        validate_and_normalize_candles(frame)


def test_non_dataframe_input_raises_type_error():
    with pytest.raises(TypeError):
        validate_and_normalize_candles([1, 2, 3])


def test_component_has_no_pipeline_imports():
    # [INVARIANT] SMC_SPECIFICATION.md §3, Decision A, point 1: the
    # component must not import from any pipeline module.
    import app.analysis.candle_validation as module

    source = open(module.__file__, encoding="utf-8").read()

    forbidden = [
        "state_machine",
        "market_structure",
        "liquidity",
        "order_blocks",
        "analysis_engine",
    ]

    for name in forbidden:
        assert name not in source
