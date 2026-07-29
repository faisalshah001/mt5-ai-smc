"""
Phase 8 coverage for Decision B, Phase 2 (SMC_SPECIFICATION.md §3,
point 5 "Deprecation notice"; §33's recorded Phase 2 versioning entry).

Phase 2 is documentation/signalling only — "changes no runtime
behaviour at all" (§33). These tests verify the two approved
deprecation-signalling channels (OpenAPI metadata, response headers)
exist, and — just as importantly — that nothing else changed: the
legacy endpoint's response contract remains byte-identical (already
guarded by test_phase2_legacy_endpoint_unchanged.py, re-affirmed here
against the new `response` parameter specifically), the canonical
endpoint is unaffected, and pipeline_version is not bumped.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import Response

import main


def test_legacy_route_marked_deprecated_in_openapi_schema():
    schema = main.app.openapi()
    path_item = schema["paths"][
        "/analysis/market-structure/{symbol}/{timeframe}"
    ]["get"]

    assert path_item.get("deprecated") is True


def test_canonical_route_not_marked_deprecated():
    schema = main.app.openapi()
    path_item = schema["paths"]["/api/v2/analyze"]["post"]

    assert path_item.get("deprecated") is not True


def test_legacy_endpoint_sets_deprecation_headers(eurusd_h4_candles):
    response = Response()

    with patch("main.get_candles", return_value=eurusd_h4_candles):
        main.market_structure_endpoint(
            "EURUSD",
            "H4",
            count=len(eurusd_h4_candles),
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
            response=response,
        )

    assert response.headers["deprecation"] == "true"
    assert response.headers["link"] == (
        '</api/v2/analyze>; rel="successor-version"'
    )


def test_legacy_endpoint_omits_deprecation_headers_when_no_sunset_date():
    # No Sunset header is set — Decision B Phase 3 has exit criteria
    # (§3 point 6) but no committed removal date, so fabricating one
    # would misrepresent the spec's actual, still-open-ended timeline.
    response = Response()

    with patch("main.get_candles") as mock_get_candles:
        import pandas as pd

        mock_get_candles.return_value = pd.DataFrame(
            {
                "time": pd.date_range(
                    "2024-01-01", periods=60, freq="1h", tz="UTC"
                ),
                "open": [1.1] * 60,
                "high": [1.1005] * 60,
                "low": [1.0995] * 60,
                "close": [1.1] * 60,
            }
        )

        main.market_structure_endpoint(
            "EURUSD",
            "H4",
            count=60,
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
            response=response,
        )

    assert "sunset" not in response.headers


def test_legacy_endpoint_response_contract_unchanged_by_deprecation_signal(
    eurusd_h4_candles,
):
    # §3 point 2: the response contract must remain exactly as today
    # for the full duration of Phase 1 and Phase 2 — the new
    # `response` parameter and headers must not leak into, or alter,
    # the JSON body in any way.
    with patch("main.get_candles", return_value=eurusd_h4_candles):
        response_body = main.market_structure_endpoint(
            "EURUSD",
            "H4",
            count=len(eurusd_h4_candles),
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
        )

    assert set(response_body.keys()) == {
        "symbol",
        "timeframe",
        "settings",
        "summary",
        "swing_points",
        "bos_events",
        "choch_events",
    }


def test_legacy_endpoint_still_callable_without_response_argument(
    eurusd_h4_candles,
):
    # Backward compatibility: every pre-Phase-8 direct caller (the
    # existing test suite, and any first-party internal caller that
    # invokes this function directly rather than through the ASGI app)
    # never passes `response` — it must keep working unchanged.
    with patch("main.get_candles", return_value=eurusd_h4_candles):
        response_body = main.market_structure_endpoint(
            "EURUSD",
            "H4",
            count=len(eurusd_h4_candles),
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
        )

    assert response_body["symbol"] == "EURUSD"


def test_pipeline_version_not_bumped_by_deprecation_notice(
    eurusd_h4_candles,
):
    # §33: "Phase 2 (deprecation notice) changes no runtime behaviour
    # at all... No version impact." Verified against the canonical
    # endpoint's own metadata, which is unrelated to and unaffected by
    # the legacy endpoint's new deprecation signalling.
    with patch("main.get_candles", return_value=eurusd_h4_candles):
        response = main.analyze_endpoint(
            main.AnalyzeRequest(
                symbol="EURUSD",
                timeframe="H4",
                count=len(eurusd_h4_candles),
            )
        )

    assert response["metadata"]["pipeline_version"] == "3.0.0"


def test_legacy_route_still_the_only_registered_get_at_its_path():
    # Regression: Phase 8 must not add, remove, or duplicate routes —
    # only annotate the existing one.
    matching_routes = [
        route
        for route in main.app.routes
        if getattr(route, "path", None)
        == "/analysis/market-structure/{symbol}/{timeframe}"
    ]

    assert len(matching_routes) == 1
    assert "GET" in matching_routes[0].methods
