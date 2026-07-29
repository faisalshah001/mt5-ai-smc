"""
Coverage for POST /api/v2/strategy/smc-signal -- the endpoint exposing
app.strategies.smc_manual_signal.generate_eurusd_manual_signal()
(Phase 3: "expose the already-implemented manual ICT/SMC strategy
through a dedicated FastAPI endpoint").

Endpoint functions are called directly (not via FastAPI's TestClient)
so these tests never touch the app's lifespan (which connects to a
live MT5 terminal) -- same approach as
tests/test_phase2_analyze_endpoint.py and
tests/test_phase1_main_endpoints.py.

The underlying orchestration sequence (H4 bias -> ... -> risk
validation) is already exhaustively covered by
tests/test_strategy_smc_manual_signal.py -- these tests only verify
the endpoint's own responsibilities: request validation, exception
mapping, and unchanged passthrough of the orchestrator's output.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import main
from app.mt5.executor import MT5TimeoutError


_SAMPLE_SIGNAL: dict = {
    "status": "SIGNAL_PENDING_APPROVAL",
    "symbol": "EURUSD",
    "direction": "BUY",
    "entry": 1.1050,
    "stop_loss": 1.1000,
    "take_profit": 1.1150,
    "risk_percent": 0.5,
    "risk_reward": 2.0,
    "position_size": 0.05,
    "confidence": 100,
    "evidence": {
        "h4_bias": {"passed": True},
        "h1_confirmation": {"passed": True},
        "liquidity_sweep": {"passed": True},
        "choch": {"passed": True},
        "displacement": {"passed": True},
        "order_block": {"passed": True},
        "retracement": {"passed": True},
        "m5_confirmation": {"passed": True},
    },
    "rejection_reasons": [],
}


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_returns_orchestrator_output_unchanged(mock_generate):
    mock_generate.return_value = _SAMPLE_SIGNAL

    response = main.smc_signal_endpoint(
        main.SmcSignalRequest(symbol="EURUSD")
    )

    assert response == _SAMPLE_SIGNAL
    mock_generate.assert_called_once_with()


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_accepts_lowercase_symbol(mock_generate):
    mock_generate.return_value = _SAMPLE_SIGNAL

    response = main.smc_signal_endpoint(
        main.SmcSignalRequest(symbol="eurusd")
    )

    assert response == _SAMPLE_SIGNAL
    mock_generate.assert_called_once_with()


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_defaults_symbol_to_eurusd(mock_generate):
    mock_generate.return_value = _SAMPLE_SIGNAL

    response = main.smc_signal_endpoint(main.SmcSignalRequest())

    assert response == _SAMPLE_SIGNAL
    mock_generate.assert_called_once_with()


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_rejects_non_eurusd_symbol_with_400(mock_generate):
    with pytest.raises(HTTPException) as excinfo:
        main.smc_signal_endpoint(main.SmcSignalRequest(symbol="GBPUSD"))

    assert excinfo.value.status_code == 400
    assert "EURUSD" in excinfo.value.detail
    # The orchestrator must never run for an unsupported symbol.
    mock_generate.assert_not_called()


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_maps_value_error_to_400(mock_generate):
    mock_generate.side_effect = ValueError("bad input")

    with pytest.raises(HTTPException) as excinfo:
        main.smc_signal_endpoint(main.SmcSignalRequest(symbol="EURUSD"))

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "bad input"


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_maps_mt5_timeout_error_to_503(mock_generate):
    mock_generate.side_effect = MT5TimeoutError("terminal unresponsive")

    with pytest.raises(HTTPException) as excinfo:
        main.smc_signal_endpoint(main.SmcSignalRequest(symbol="EURUSD"))

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "terminal unresponsive"


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_maps_runtime_error_to_500(mock_generate):
    mock_generate.side_effect = RuntimeError("mt5 call failed")

    with pytest.raises(HTTPException) as excinfo:
        main.smc_signal_endpoint(main.SmcSignalRequest(symbol="EURUSD"))

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "mt5 call failed"


@patch("main.generate_eurusd_manual_signal")
def test_endpoint_reraises_unexpected_exception_unwrapped(mock_generate):
    """
    Matches every other endpoint in main.py: an exception type not
    explicitly handled (ValueError/MT5TimeoutError/RuntimeError) is
    logged and re-raised as-is, not silently swallowed or remapped.
    """

    mock_generate.side_effect = KeyError("unexpected")

    with pytest.raises(KeyError):
        main.smc_signal_endpoint(main.SmcSignalRequest(symbol="EURUSD"))


def test_smc_signal_request_defaults_to_eurusd():
    request = main.SmcSignalRequest()
    assert request.symbol == "EURUSD"


def test_endpoint_is_registered_as_post_api_v2_strategy_smc_signal():
    matching_routes = [
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/api/v2/strategy/smc-signal"
    ]

    assert len(matching_routes) == 1
    assert "POST" in matching_routes[0].methods
    assert "GET" not in matching_routes[0].methods


def test_no_additional_smc_signal_endpoint_aliases_were_added():
    matching_paths = [
        route.path
        for route in main.app.routes
        if "smc-signal" in getattr(route, "path", "")
    ]

    assert matching_paths == ["/api/v2/strategy/smc-signal"]


def test_existing_endpoints_are_still_registered_unchanged():
    # Explicit instruction: do not change any existing endpoint. This
    # is a coarse guard confirming every pre-existing route path is
    # still present after adding the new one.
    existing_paths = {
        "/",
        "/health",
        "/account",
        "/positions",
        "/candles/{symbol}/{timeframe}",
        "/strategy/trend/{symbol}/{timeframe}",
        "/strategy/multi-timeframe/{symbol}",
        "/risk/trade-levels",
        "/analysis/market-structure/{symbol}/{timeframe}",
        "/api/v2/analyze",
    }

    registered_paths = {
        route.path
        for route in main.app.routes
        if hasattr(route, "path")
    }

    assert existing_paths.issubset(registered_paths)
