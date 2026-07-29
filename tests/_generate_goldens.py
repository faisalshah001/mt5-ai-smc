"""
One-time (and future, deliberate-update) golden-file generator.

This script is NOT part of the pytest run. It exists to (re)create the
committed snapshots under tests/golden/ from the current production
code. Run it manually:

    python tests/_generate_goldens.py

Phase 0 usage: run once, against the untouched, pre-Phase-1 codebase,
to capture the current baseline. Commit the resulting tests/golden/*.json
files alongside the test code that depends on them.

Future usage: when a spec-approved phase deliberately changes output
(e.g. Decision #12's Order Block defaults), re-run this script for the
affected golden(s) only, and note in the commit message which decision
justifies the change. Never regenerate a golden to make a failing test
pass without first confirming the change was deliberate and approved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.analysis_engine import analyze_market
from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import (
    classify_market_structure,
    detect_breaks_of_structure,
    detect_change_of_character,
    detect_swing_points,
)
from app.analysis.order_blocks import detect_order_blocks
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators
from tests.helpers.candles import load_eurusd_h4_fixture
from tests.helpers.golden import save_golden
from tests.helpers.serialize import (
    dataframe_to_records,
    events_to_records,
    objects_to_records,
)


def main() -> None:
    candles = load_eurusd_h4_fixture()

    # --- indicators ---
    indicators = calculate_indicators(candles)
    save_golden(
        "indicators_eurusd_h4",
        dataframe_to_records(
            indicators[
                [
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
            ]
        ),
    )

    # --- legacy pipeline ---
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    classified = classify_market_structure(swings)
    bos = detect_breaks_of_structure(classified, minimum_break_atr=0.10)
    legacy = detect_change_of_character(bos)
    save_golden(
        "legacy_bos_choch_eurusd_h4",
        dataframe_to_records(
            legacy[
                [
                    "time",
                    "close",
                    "swing_high",
                    "swing_low",
                    "structure",
                    "bos",
                    "choch",
                    "broken_level",
                    "break_distance",
                    "required_break_distance",
                ]
            ]
        ),
    )

    # --- canonical state machine ---
    #
    # Decision #3 (Phase 7): detect_structure_state performs per-cycle
    # classification itself, in a single unified forward pass — it
    # must run on raw swings, never on the legacy classifier's output
    # (the "classified" DataFrame above exists only for the legacy
    # golden and must not feed the canonical pipeline).
    structure = detect_structure_state(swings, minimum_break_atr=0.10)
    save_golden(
        "state_machine_eurusd_h4",
        dataframe_to_records(
            structure[
                [
                    "time",
                    "close",
                    "structure",
                    "external_trend",
                    "structure_state",
                    "structure_event",
                    "event_direction",
                    "latest_swing_high",
                    "latest_swing_low",
                    "protected_high",
                    "protected_low",
                    "broken_level",
                    "break_distance",
                    "required_break_distance",
                    "mss_confirmation_step",
                    "mss_origin_level",
                    "mss_origin_index",
                    "mss_invalidated_origin_index",
                    "protected_high_status",
                    "protected_high_source",
                    "protected_low_status",
                    "protected_low_source",
                ]
            ]
        ),
    )

    # --- liquidity ---
    liquidity_dataframe, liquidity_registry, liquidity_events = (
        detect_liquidity_registry(structure)
    )
    save_golden(
        "liquidity_dataframe_eurusd_h4",
        dataframe_to_records(
            liquidity_dataframe[
                [
                    "time",
                    "equal_high",
                    "equal_low",
                    "liquidity_created",
                    "liquidity_type",
                    "liquidity_level",
                    "liquidity_id",
                    "liquidity_swept",
                    "liquidity_broken",
                    "active_bsl_count",
                    "active_ssl_count",
                ]
            ]
        ),
    )
    save_golden(
        "liquidity_registry_eurusd_h4",
        objects_to_records(liquidity_registry.all()),
    )
    save_golden(
        "liquidity_events_eurusd_h4",
        objects_to_records(liquidity_events),
    )

    # --- order blocks ---
    order_block_dataframe, order_block_registry, order_block_events = (
        detect_order_blocks(liquidity_dataframe)
    )
    save_golden(
        "order_block_dataframe_eurusd_h4",
        dataframe_to_records(
            order_block_dataframe[
                [
                    "time",
                    "order_block_created",
                    "order_block_id",
                    "order_block_type",
                    "order_block_proximal",
                    "order_block_distal",
                    "order_block_mitigated",
                    "mitigated_order_block_id",
                    "order_block_invalidated",
                    "invalidated_order_block_id",
                    "active_bullish_order_blocks",
                    "active_bearish_order_blocks",
                ]
            ]
        ),
    )
    save_golden(
        "order_block_registry_eurusd_h4",
        objects_to_records(order_block_registry.all(sorted_by_time=True)),
    )
    save_golden(
        "order_block_events_eurusd_h4",
        objects_to_records(order_block_events),
    )

    # --- full analyze_market() end-to-end ---
    result = analyze_market(symbol="EURUSD", timeframe="H4", candles=candles)

    save_golden(
        "analysis_engine_structure_eurusd_h4",
        dataframe_to_records(
            result.structure[
                [
                    "time",
                    "close",
                    "structure",
                    "external_trend",
                    "structure_state",
                    "structure_event",
                    "event_direction",
                    "protected_high",
                    "protected_low",
                    "liquidity_created",
                    "liquidity_type",
                    "order_block_created",
                    "order_block_id",
                ]
            ]
        ),
    )
    save_golden("analysis_engine_events_eurusd_h4", events_to_records(result.events))
    save_golden(
        "analysis_engine_liquidity_eurusd_h4",
        objects_to_records(result.liquidity),
    )
    save_golden(
        "analysis_engine_order_blocks_eurusd_h4",
        objects_to_records(result.order_blocks),
    )
    save_golden(
        "analysis_engine_snapshot_eurusd_h4",
        result.structure_snapshot.to_dict() if result.structure_snapshot else None,
    )

    metadata = dict(result.metadata)
    metadata.pop("swing_options", None)
    metadata.pop("structure_options", None)
    metadata.pop("liquidity_options", None)
    metadata.pop("order_block_options", None)
    save_golden("analysis_engine_metadata_eurusd_h4", metadata)

    # --- Phase 2: canonical POST /api/v2/analyze endpoint ---
    from unittest.mock import patch

    import main as main_module

    with patch("main.get_candles", return_value=candles):
        endpoint_response = main_module.analyze_endpoint(
            main_module.AnalyzeRequest(
                symbol="EURUSD",
                timeframe="H4",
                count=len(candles),
            )
        )

    endpoint_metadata = dict(endpoint_response["metadata"])
    endpoint_metadata.pop("swing_options", None)
    endpoint_metadata.pop("structure_options", None)
    endpoint_metadata.pop("liquidity_options", None)
    endpoint_metadata.pop("order_block_options", None)

    save_golden(
        "analyze_endpoint_response_eurusd_h4",
        {
            "symbol": endpoint_response["symbol"],
            "timeframe": endpoint_response["timeframe"],
            "structure": endpoint_response["structure"],
            "liquidity_dataframe": endpoint_response["liquidity_dataframe"],
            "events": endpoint_response["events"],
            "liquidity": endpoint_response["liquidity"],
            "order_blocks": endpoint_response["order_blocks"],
            "structure_snapshot": endpoint_response["structure_snapshot"],
            "metadata": endpoint_metadata,
        },
    )

    print("Golden files regenerated successfully.")


if __name__ == "__main__":
    main()
