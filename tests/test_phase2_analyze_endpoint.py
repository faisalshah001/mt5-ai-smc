"""
Phase 2 coverage for POST /api/v2/analyze
(SMC_SPECIFICATION.md §3, Decision B, Phase 1).

Endpoint functions are called directly (not via FastAPI's TestClient)
so these tests never touch the app's lifespan (which connects to a
live MT5 terminal) — same approach as tests/test_phase1_main_endpoints.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import main
from tests.helpers.candles import build_zigzag_candles
from tests.helpers.golden import assert_matches_golden


def _malformed_candles():
    frame = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1200],
        candles_per_leg=6,
    )
    frame.loc[1, "time"] = frame.loc[0, "time"]  # duplicate timestamp
    return frame


@patch("main.get_candles")
def test_analyze_endpoint_matches_golden(mock_get_candles, eurusd_h4_candles):
    mock_get_candles.return_value = eurusd_h4_candles

    response = main.analyze_endpoint(
        main.AnalyzeRequest(
            symbol="EURUSD",
            timeframe="H4",
            count=len(eurusd_h4_candles),
        )
    )

    metadata = dict(response["metadata"])
    metadata.pop("swing_options", None)
    metadata.pop("structure_options", None)
    metadata.pop("liquidity_options", None)
    metadata.pop("order_block_options", None)

    assert_matches_golden(
        "analyze_endpoint_response_eurusd_h4",
        {
            "symbol": response["symbol"],
            "timeframe": response["timeframe"],
            "structure": response["structure"],
            "liquidity_dataframe": response["liquidity_dataframe"],
            "events": response["events"],
            "liquidity": response["liquidity"],
            "order_blocks": response["order_blocks"],
            "structure_snapshot": response["structure_snapshot"],
            "metadata": metadata,
        },
    )


@patch("main.get_candles")
def test_analyze_endpoint_response_has_expected_top_level_shape(
    mock_get_candles, eurusd_h4_candles
):
    mock_get_candles.return_value = eurusd_h4_candles

    response = main.analyze_endpoint(
        main.AnalyzeRequest(symbol="EURUSD", timeframe="H4", count=100)
    )

    # Decision B point 1: expose the full canonical pipeline output
    # directly — not reshaped toward the legacy swing_points/bos_events/
    # choch_events contract.
    assert set(response.keys()) == {
        "symbol",
        "timeframe",
        "structure",
        "liquidity_dataframe",
        "events",
        "liquidity",
        "order_blocks",
        "structure_snapshot",
        "metadata",
    }
    assert "swing_points" not in response
    assert "bos_events" not in response
    assert "choch_events" not in response

    assert response["symbol"] == "EURUSD"
    assert response["timeframe"] == "H4"
    assert len(response["structure"]) == len(eurusd_h4_candles)
    assert isinstance(response["events"], list)


@patch("main.get_candles")
def test_analyze_endpoint_rejects_malformed_candles(mock_get_candles):
    mock_get_candles.return_value = _malformed_candles()

    with pytest.raises(HTTPException) as excinfo:
        main.analyze_endpoint(
            main.AnalyzeRequest(symbol="EURUSD", timeframe="H4", count=250)
        )

    assert excinfo.value.status_code == 400


def test_analyze_request_rejects_count_out_of_bounds():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        main.AnalyzeRequest(symbol="EURUSD", timeframe="H4", count=10)

    with pytest.raises(pydantic.ValidationError):
        main.AnalyzeRequest(symbol="EURUSD", timeframe="H4", count=5000)


def test_analyze_endpoint_is_registered_as_post_api_v2_analyze():
    matching_routes = [
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/api/v2/analyze"
    ]

    assert len(matching_routes) == 1
    assert "POST" in matching_routes[0].methods
    assert "GET" not in matching_routes[0].methods


def test_no_additional_endpoint_aliases_were_added():
    # Explicit instruction: do not add any additional aliases of the
    # analyze endpoint itself. Scoped to "analyze" only (not "v2"
    # generally) so that unrelated, legitimately-added v2 endpoints
    # (e.g. Phase 3's /api/v2/strategy/smc-signal) are not mistaken
    # for an analyze alias.
    analyze_paths = [
        route.path
        for route in main.app.routes
        if "analyze" in getattr(route, "path", "").lower()
    ]

    assert analyze_paths == ["/api/v2/analyze"]
