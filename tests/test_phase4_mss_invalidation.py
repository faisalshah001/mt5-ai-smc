"""
Phase 4 coverage for Decision #6 (MSS invalidation, SMC_SPECIFICATION.md
§19) and the Decision #8 missing-data-guard fix (§22 point 2, steps
4/7).

Every assertion below was verified empirically against actual
state_machine.py / analysis_engine.py output before being written.
"""

from __future__ import annotations

import pytest

from app.analysis.analysis_engine import analyze_market
from app.analysis.market_structure import detect_swing_points
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators
from tests.helpers.candles import build_zigzag_candles
from tests.helpers.dataframe_compare import cell


def _detect_swings(candles):
    # Decision #3 (Phase 7): detect_structure_state performs
    # per-cycle classification itself — no separate
    # classify_market_structure pass in the canonical pipeline.
    indicators = calculate_indicators(candles)
    return detect_swing_points(indicators, left_bars=3, right_bars=3)


def test_lh_during_mss_bullish_remains_undefined_no_op():
    # §21's state-transition table explicitly leaves this cell
    # UNDEFINED — not addressed by Decision #6. This test proves it
    # was not silently "fixed" as an unintended side effect of this
    # phase's other changes.
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,
            1.2020,
            1.1850,  # LL -> neutral -> bearish
            1.1900,  # LH -> protected_high set
            1.1800,  # LL (buffer)
            1.2100,  # HH: bullish MSS fires; no-op (no HL flag yet)
            1.2000,  # HL: sets bullish_mss_has_hl
            1.2050,  # LH (lower than the MSS-creating HH@2100) while
                     # still mss_bullish -> must remain a no-op
            1.1950,  # buffer
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 48, "structure_state") == "mss_bullish"
    assert cell(result, 64, "structure") == "LH"
    assert cell(result, 64, "structure_state") == "mss_bullish"
    assert cell(result, 64, "structure_event") is None
    assert "MSS_INVALIDATED" not in set(
        result["structure_event"].dropna().astype(str)
    )


def test_hl_during_mss_bearish_remains_undefined_no_op():
    # Mirror of the previous test.
    candles = build_zigzag_candles(
        [
            1.1000,
            1.1050,
            1.0980,
            1.1150,  # HH -> neutral -> bullish
            1.1080,  # HL -> protected_low set
            1.1120,  # LH (buffer)
            1.0850,  # LL: bearish MSS fires; no-op (no LH flag yet)
            1.0950,  # LH: sets bearish_mss_has_lh
            1.0900,  # HL (higher than the MSS-creating LL@0850) while
                     # still mss_bearish -> must remain a no-op
            1.1000,  # buffer
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 48, "structure_state") == "mss_bearish"
    assert cell(result, 64, "structure") == "HL"
    assert cell(result, 64, "structure_state") == "mss_bearish"
    assert cell(result, 64, "structure_event") is None
    assert "MSS_INVALIDATED" not in set(
        result["structure_event"].dropna().astype(str)
    )


def test_mss_origin_index_is_a_per_row_output_column():
    # Audit clarification (§19): mss_origin_index must mirror
    # mss_origin_level as a per-row output column throughout the
    # pending phase, not only at the moment of invalidation.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 42, "structure_event") == "MSS"
    origin_index_at_mss = cell(result, 42, "mss_origin_index")
    assert origin_index_at_mss == 42

    # Still reported, unchanged, several rows into the pending phase.
    assert cell(result, 47, "structure_state") == "mss_bearish"
    assert cell(result, 47, "mss_origin_index") == origin_index_at_mss


def test_negative_atr_still_raises_when_data_available():
    # Regression for the missing-data-guard restructure: the
    # negative-ATR check must still fire exactly when it did before
    # (data available, value negative), never when data is missing.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    corrupted = swings.copy()
    corrupted.loc[10, "atr14"] = -0.001

    with pytest.raises(ValueError, match="ATR value cannot be negative"):
        detect_structure_state(corrupted, minimum_break_atr=0.10)


def test_missing_atr_alone_does_not_raise_or_fabricate_events():
    # A NaN ATR on an otherwise-ordinary row (no swing-driven event of
    # its own) must be silently skipped for the close-driven checks —
    # not raise, not fabricate a BOS/MSS.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    corrupted = swings.copy()
    corrupted.loc[20, "atr14"] = float("nan")

    result = detect_structure_state(corrupted, minimum_break_atr=0.10)

    assert cell(result, 20, "structure_event") is None


def test_build_structure_events_joins_invalidation_to_origin_mss():
    # analysis_engine.py::_build_structure_events (Decision #6): the
    # MSS_INVALIDATED MarketEvent's metadata must carry both the
    # origin position (mss_origin_index) and the originating MSS
    # MarketEvent's own event_id (mss_origin_event_id) — the
    # cross-engine join key §19 and Appendix B rely on.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    mss_events = [e for e in result.events if e.event_type == "MSS"]
    invalidated_events = [
        e for e in result.events if e.event_type == "MSS_INVALIDATED"
    ]

    assert len(mss_events) == 1
    assert len(invalidated_events) == 1

    mss_event = mss_events[0]
    invalidated_event = invalidated_events[0]

    assert invalidated_event.direction == "bullish"
    assert invalidated_event.metadata["mss_origin_index"] == mss_event.index
    assert (
        invalidated_event.metadata["mss_origin_event_id"]
        == mss_event.event_id
    )
    # No existing event's own identity is disturbed by the join.
    assert mss_event.event_id != invalidated_event.event_id


def test_event_type_literal_accepts_mss_invalidated_in_market_event():
    from datetime import datetime, timezone

    from app.analysis.models import MarketEvent

    event = MarketEvent(
        event_id="EV_TEST",
        event_type="MSS_INVALIDATED",
        time=datetime.now(timezone.utc),
        index=0,
        direction="bullish",
    )

    assert event.event_type == "MSS_INVALIDATED"
    assert event.to_dict()["event_type"] == "MSS_INVALIDATED"


def test_invalidation_is_deterministic_across_repeated_runs():
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)

    first_run = detect_structure_state(swings, minimum_break_atr=0.10)
    second_run = detect_structure_state(swings, minimum_break_atr=0.10)

    columns = [
        "structure_event",
        "event_direction",
        "structure_state",
        "protected_low",
        "protected_low_status",
        "protected_low_source",
        "mss_invalidated_origin_index",
    ]

    for column in columns:
        assert list(first_run[column]) == list(second_run[column])
