"""
Post-Audit Hardening Phase, Task 2: RSI zero-average-loss defect fix.

Confirmed audit finding: app.indicators.technical.calculate_indicators
produced rsi14 == NaN (instead of the textbook-correct 100.0) whenever
average_loss reached exactly zero with average_gain > 0 -- reproducible
on real market data during an uninterrupted uptrend, not merely a
theoretical edge case (also confirmed present in the real EURUSD H4
golden fixture: 2 rows).

This file does not touch app.strategies.trend -- its thresholds are
unchanged; these tests prove the corrected RSI values compose
correctly with that existing, unmodified logic.
"""

from __future__ import annotations

import math

import pandas as pd

from app.indicators.technical import calculate_indicators
from app.strategies.trend import analyse_trend


def _candles(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01",
                periods=len(closes),
                freq="1h",
                tz="UTC",
            ),
            "open": closes,
            "high": [close + 0.0005 for close in closes],
            "low": [close - 0.0005 for close in closes],
            "close": closes,
        }
    )


# --- 1. Strictly increasing closing prices --------------------------------


def test_strictly_increasing_closes_yield_rsi_100_not_nan():
    closes = [1.1000 + 0.0010 * i for i in range(20)]
    result = calculate_indicators(_candles(closes))

    # Once average_loss has settled at exactly zero, RSI must be
    # 100.0 -- the defect this task fixes.
    tail = result["rsi14"].tail(5)
    assert (tail == 100.0).all()
    assert not tail.isna().any()


# --- 2. Strictly decreasing closing prices ---------------------------------


def test_strictly_decreasing_closes_yield_rsi_0():
    closes = [1.2000 - 0.0010 * i for i in range(20)]
    result = calculate_indicators(_candles(closes))

    tail = result["rsi14"].tail(5)
    assert (tail == 0.0).all()
    assert not tail.isna().any()


# --- 3. Flat prices ---------------------------------------------------------


def test_flat_prices_preserve_existing_nan_convention():
    # Both average_gain and average_loss are zero -- RSI is genuinely
    # indeterminate (0/0). This case is unaffected by the fix, which
    # only targets the two asymmetric zero-loss/zero-gain cases.
    closes = [1.1000] * 20
    result = calculate_indicators(_candles(closes))

    assert result["rsi14"].tail(5).isna().all()


# --- 4. Mixed gains and losses ---------------------------------------------


def test_mixed_gains_and_losses_produce_finite_bounded_rsi():
    closes = [1.1000]
    deltas = [
        0.0010, -0.0007, 0.0005, -0.0012, 0.0008,
        -0.0003, 0.0015, -0.0009, 0.0002, -0.0006,
        0.0011, -0.0004, 0.0007, -0.0013, 0.0009,
        -0.0002, 0.0006, -0.0008, 0.0010, -0.0005,
    ]
    for delta in deltas:
        closes.append(closes[-1] + delta)

    result = calculate_indicators(_candles(closes))
    tail = result["rsi14"].tail(10)

    assert not tail.isna().any()
    assert ((tail >= 0.0) & (tail <= 100.0)).all()


# --- 5. RSI warm-up rows -----------------------------------------------------


def test_warmup_row_zero_remains_nan():
    # Row 0 has no prior close (diff() is undefined there) -- this
    # warm-up NaN is unrelated to the zero-average-loss defect and
    # must be preserved exactly as before the fix.
    closes = [1.1000 + 0.0010 * i for i in range(10)]
    result = calculate_indicators(_candles(closes))

    assert math.isnan(result["rsi14"].iloc[0])


# --- 6. No new infinities ----------------------------------------------------


def test_no_infinities_across_all_scenarios(eurusd_h4_candles):
    scenarios = [
        [1.1000 + 0.0010 * i for i in range(30)],
        [1.2000 - 0.0010 * i for i in range(30)],
        [1.1000] * 30,
    ]

    for closes in scenarios:
        result = calculate_indicators(_candles(closes))
        finite_values = result["rsi14"].dropna()
        assert not finite_values.isin(
            [float("inf"), float("-inf")]
        ).any()

    # Also verified against the real, larger EURUSD H4 fixture.
    real_result = calculate_indicators(eurusd_h4_candles)
    real_finite = real_result["rsi14"].dropna()
    assert not real_finite.isin([float("inf"), float("-inf")]).any()


# --- 7. Existing trend-strategy behaviour remains compatible ---------------


def test_trend_strategy_thresholds_unchanged_for_normal_input():
    # A non-edge-case, mixed-price fixture: the fix must not alter
    # analyse_trend's behaviour when RSI is nowhere near either zero
    # boundary. Uses a long enough history for EMA200 to be meaningful.
    closes = [1.1000]
    for i in range(220):
        closes.append(closes[-1] + (0.0004 if i % 3 else -0.0002))

    frame = calculate_indicators(_candles(closes))
    analysis = analyse_trend(frame)

    assert analysis["signal"] in {"buy", "sell", "wait"}
    assert 0.0 <= analysis["momentum"]["rsi14"] <= 100.0


def test_trend_strategy_correctly_receives_rsi_100_instead_of_nan():
    # Before the fix: rsi14 was NaN here, so analyse_trend's RSI
    # comparisons all evaluated False (silently dropped). After the
    # fix, it receives the correct value (100.0) via calculate_
    # indicators -> analyse_trend's own, completely unmodified
    # pipeline -- no crash, a well-formed float within [0, 100].
    closes = [1.1000 + 0.0010 * i for i in range(220)]
    frame = calculate_indicators(_candles(closes))

    analysis = analyse_trend(frame)

    assert analysis["momentum"]["rsi14"] == 100.0
    assert isinstance(analysis["signal"], str)


def test_trend_strategy_rsi_ge_70_branch_reachable_with_100():
    # Isolates analyse_trend's own, unmodified rsi14 >= 70 branch
    # (trend.py:121-124) directly against the fixed value 100.0 --
    # proving the corrected RSI composes correctly with existing,
    # untouched threshold logic when that branch is reached (i.e.
    # when the other indicators do not already independently push a
    # strong directional signal that would use bullish_reasons/
    # bearish_reasons instead of neutral_reasons).
    frame = pd.DataFrame(
        {
            "close": [1.1000],
            "ema20": [1.1000],
            "ema50": [1.1000],
            "ema200": [1.1000],
            "rsi14": [100.0],
            "macd": [0.0],
            "macd_signal": [0.0],
        }
    )

    analysis = analyse_trend(frame)

    assert analysis["momentum"]["rsi14"] == 100.0
    assert any(
        "overbought" in reason
        for reason in analysis["reasons"]
    )
