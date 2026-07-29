"""
Phase 1 coverage for main.py's four candle-consuming endpoints
(SMC_SPECIFICATION.md §3, Decision A, point 7 — all four MUST call the
shared validation component immediately after candle retrieval).

Endpoint functions are called directly (not via FastAPI's TestClient)
so these tests never touch the app's lifespan (which connects to a
live MT5 terminal) — this exercises the exact same function body
FastAPI would call, without requiring MT5 to be running.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import main
from tests.helpers.candles import build_zigzag_candles


def _valid_candles():
    return build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1200, 1.1120],
        candles_per_leg=6,
    )


def _malformed_candles():
    frame = _valid_candles()
    frame.loc[1, "time"] = frame.loc[0, "time"]  # duplicate timestamp
    return frame


@patch("main.get_candles")
def test_candles_endpoint_rejects_malformed_candles(mock_get_candles):
    mock_get_candles.return_value = _malformed_candles()

    with pytest.raises(HTTPException) as excinfo:
        main.candles_endpoint("EURUSD", "H4", count=250)

    assert excinfo.value.status_code == 400


@patch("main.get_candles")
def test_candles_endpoint_accepts_well_formed_candles(mock_get_candles):
    mock_get_candles.return_value = _valid_candles()

    result = main.candles_endpoint("EURUSD", "H4", count=250)

    assert result["symbol"] == "EURUSD"
    assert result["count"] > 0


@patch("main.get_candles")
def test_trend_endpoint_rejects_malformed_candles(mock_get_candles):
    mock_get_candles.return_value = _malformed_candles()

    with pytest.raises(HTTPException) as excinfo:
        main.trend_strategy_endpoint("EURUSD", "H4", count=250)

    assert excinfo.value.status_code == 400


@patch("main.get_candles")
def test_trend_endpoint_accepts_well_formed_candles(mock_get_candles):
    mock_get_candles.return_value = _valid_candles()

    result = main.trend_strategy_endpoint("EURUSD", "H4", count=250)

    assert result["symbol"] == "EURUSD"
    assert "analysis" in result


@patch("main.get_candles")
def test_multi_timeframe_endpoint_rejects_malformed_candles(mock_get_candles):
    # Before Phase 1, this endpoint had zero candle validation at all.
    mock_get_candles.return_value = _malformed_candles()

    with pytest.raises(HTTPException) as excinfo:
        main.multi_timeframe_strategy_endpoint("EURUSD", count=250)

    assert excinfo.value.status_code == 400


@patch("main.get_candles")
def test_multi_timeframe_endpoint_accepts_well_formed_candles(mock_get_candles):
    mock_get_candles.return_value = _valid_candles()

    result = main.multi_timeframe_strategy_endpoint("EURUSD", count=250)

    assert result["symbol"] == "EURUSD"
    assert set(result["timeframes"]) == {"H1", "H4", "D1"}


@patch("main.get_candles")
def test_market_structure_endpoint_rejects_malformed_candles(mock_get_candles):
    mock_get_candles.return_value = _malformed_candles()

    with pytest.raises(HTTPException) as excinfo:
        main.market_structure_endpoint(
            "EURUSD",
            "H4",
            count=200,
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
        )

    assert excinfo.value.status_code == 400


@patch("main.get_candles")
def test_market_structure_endpoint_accepts_well_formed_candles(mock_get_candles):
    mock_get_candles.return_value = _valid_candles()

    result = main.market_structure_endpoint(
        "EURUSD",
        "H4",
        count=200,
        left_bars=3,
        right_bars=3,
        minimum_break_atr=0.10,
    )

    assert result["symbol"] == "EURUSD"
    assert "swing_points" in result
