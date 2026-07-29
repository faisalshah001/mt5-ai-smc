"""
Baseline coverage for app.analysis.analysis_engine.

Includes a full end-to-end golden-file snapshot of analyze_market()
over the real EURUSD H4 fixture, plus hand-verified pins of the
current _validate_input/_prepare_candles behaviour (SMC_SPECIFICATION.md
§3, Decision A replaces this private, non-reusable validation with a
standalone component — these tests capture exactly what the private
implementation does today, before that replacement).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.analysis_engine import analyze_market
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import (
    dataframe_to_records,
    events_to_records,
    objects_to_records,
)


def test_analyze_market_end_to_end_matches_golden(eurusd_h4_candles):
    result = analyze_market(
        symbol="EURUSD",
        timeframe="H4",
        candles=eurusd_h4_candles,
    )

    structure_columns = [
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

    assert_matches_golden(
        "analysis_engine_structure_eurusd_h4",
        dataframe_to_records(result.structure[structure_columns]),
    )

    assert_matches_golden(
        "analysis_engine_events_eurusd_h4",
        events_to_records(result.events),
    )

    assert_matches_golden(
        "analysis_engine_liquidity_eurusd_h4",
        objects_to_records(result.liquidity),
    )

    assert_matches_golden(
        "analysis_engine_order_blocks_eurusd_h4",
        objects_to_records(result.order_blocks),
    )

    snapshot = result.structure_snapshot.to_dict() if result.structure_snapshot else None
    assert_matches_golden(
        "analysis_engine_snapshot_eurusd_h4",
        snapshot,
    )

    metadata = dict(result.metadata)
    metadata.pop("swing_options", None)
    metadata.pop("structure_options", None)
    metadata.pop("liquidity_options", None)
    metadata.pop("order_block_options", None)
    assert_matches_golden(
        "analysis_engine_metadata_eurusd_h4",
        metadata,
    )


def test_analyze_market_rejects_empty_candles():
    with pytest.raises(ValueError):
        analyze_market(
            symbol="EURUSD",
            timeframe="H4",
            candles=pd.DataFrame(),
        )


def test_analyze_market_rejects_missing_columns():
    frame = pd.DataFrame(
        {
            "time": [1, 2, 3],
            "open": [1.0, 1.0, 1.0],
            "high": [1.1, 1.1, 1.1],
            "low": [0.9, 0.9, 0.9],
            # 'close' intentionally missing
        }
    )

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="H4", candles=frame)


def test_analyze_market_rejects_duplicate_timestamps(eurusd_h4_candles):
    duplicated = eurusd_h4_candles.copy()
    duplicated.loc[1, "time"] = duplicated.loc[0, "time"]

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="H4", candles=duplicated)


def test_analyze_market_rejects_invalid_ohlc_relationship(eurusd_h4_candles):
    invalid = eurusd_h4_candles.copy()
    # high below open/close/low is structurally invalid.
    invalid.loc[0, "high"] = invalid.loc[0, "low"] - 0.01

    with pytest.raises(ValueError):
        analyze_market(symbol="EURUSD", timeframe="H4", candles=invalid)


def test_analyze_market_sorts_unsorted_input_chronologically(eurusd_h4_candles):
    shuffled = eurusd_h4_candles.sample(frac=1, random_state=0).reset_index(
        drop=True
    )

    result = analyze_market(
        symbol="EURUSD",
        timeframe="H4",
        candles=shuffled,
    )

    times = result.candles["time"]
    assert list(times) == list(times.sort_values())


def test_analyze_market_accepts_numeric_epoch_time():
    # _prepare_candles currently accepts numeric epoch-seconds time
    # values (as MT5's own get_candles supplies) in addition to
    # datetime-like values.
    frame = pd.DataFrame(
        {
            "time": [1_700_000_000 + i * 3600 for i in range(80)],
            "open": [1.10 + i * 0.0001 for i in range(80)],
            "high": [1.1005 + i * 0.0001 for i in range(80)],
            "low": [1.0995 + i * 0.0001 for i in range(80)],
            "close": [1.1002 + i * 0.0001 for i in range(80)],
        }
    )

    result = analyze_market(symbol="EURUSD", timeframe="H1", candles=frame)

    assert pd.api.types.is_datetime64_any_dtype(result.candles["time"])
