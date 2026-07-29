"""
Baseline (golden-file) coverage for app.indicators.technical.

No decision in SMC_SPECIFICATION.md touches this file. These tests
exist purely as a change-detector: if any future phase accidentally
alters indicator math, this fails immediately.
"""

from __future__ import annotations

from app.indicators.technical import calculate_indicators
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import dataframe_to_records


def test_calculate_indicators_matches_golden(eurusd_h4_candles):
    result = calculate_indicators(eurusd_h4_candles)

    columns = [
        "time",
        "close",
        "ema20",
        "ema50",
        "ema200",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "atr14",
    ]

    assert_matches_golden(
        "indicators_eurusd_h4",
        dataframe_to_records(result[columns]),
    )


def test_calculate_indicators_rejects_missing_columns():
    import pandas as pd
    import pytest

    frame = pd.DataFrame({"high": [1.0], "low": [0.9]})

    with pytest.raises(ValueError):
        calculate_indicators(frame)
