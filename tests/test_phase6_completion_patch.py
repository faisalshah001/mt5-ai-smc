"""
Phase 6 completion patch: two specification-compliance corrections
identified in the initial Phase 6 report.

1. pipeline_version — SMC_SPECIFICATION.md §33, "APPROVED SPEC —
   recorded per Decision #12": implementing Decision #12 "requires a
   MAJOR pipeline_version increment on implementation... not left as
   an implementation-time judgment call." The exact required value is
   "3.0.0" (MAJOR bump from the "2.0.0" baseline, per standard semver
   — which §33's own MAJOR/MINOR/PATCH scheme follows — resetting
   MINOR/PATCH regardless of what accumulated across Phases 1-5).

2. mss_origin_index clearing — §19: "a new mss_origin_index variable,
   parallel to mss_origin_level, cleared at the same two points
   (CHoCH confirmation, MSS invalidation)." The invalidation-side
   clearing was already correct since Phase 4; the CHoCH-confirmation
   side was missing entirely. Fixed in state_machine.py.

Every assertion below was verified empirically against actual output
before being written.
"""

from __future__ import annotations

from unittest.mock import patch

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


# --- Correction 1: pipeline_version -----------------------------------


def test_pipeline_version_matches_specification(eurusd_h4_candles):
    result = analyze_market(
        symbol="EURUSD", timeframe="H4", candles=eurusd_h4_candles
    )

    assert result.metadata["pipeline_version"] == "3.0.0"


def test_canonical_endpoint_exposes_updated_pipeline_version(
    eurusd_h4_candles,
):
    import main

    with patch("main.get_candles", return_value=eurusd_h4_candles):
        response = main.analyze_endpoint(
            main.AnalyzeRequest(
                symbol="EURUSD",
                timeframe="H4",
                count=len(eurusd_h4_candles),
            )
        )

    assert response["metadata"]["pipeline_version"] == "3.0.0"


# --- Correction 2: mss_origin_index clearing ---------------------------


def test_mss_origin_index_cleared_after_choch_confirmation():
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,
            1.2020,
            1.1850,
            1.1900,
            1.1800,
            1.2100,
            1.1950,
            1.2200,
            1.2100,
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 43, "structure_event") == "MSS"
    assert cell(result, 43, "mss_origin_index") == 43

    assert cell(result, 64, "structure_event") == "CHoCH"
    assert cell(result, 64, "mss_origin_level") is None
    assert cell(result, 64, "mss_origin_index") is None

    # Stays cleared afterwards — no stale value lingering post-CHoCH.
    assert cell(result, 70, "mss_origin_index") is None
    assert cell(result, 72, "mss_origin_index") is None


def test_mss_origin_index_cleared_after_mss_invalidation():
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 42, "structure_event") == "MSS"
    assert cell(result, 42, "mss_origin_index") == 42

    assert cell(result, 56, "structure_event") == "MSS_INVALIDATED"
    assert cell(result, 56, "mss_origin_index") is None
    # The join key survives on this exact row — capture happens
    # before the clear, not lost by it.
    assert cell(result, 56, "mss_invalidated_origin_index") == 42

    assert cell(result, 64, "mss_origin_index") is None
    assert cell(result, 64, "mss_invalidated_origin_index") is None


def test_mss_invalidated_event_still_retains_correct_origin():
    # Regression: the CHoCH-side fix must not disturb the
    # already-correct invalidation-side capture-then-clear ordering,
    # nor the analysis_engine.py cross-engine join built on it
    # (Phase 4).
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

    assert invalidated_event.metadata["mss_origin_index"] == mss_event.index
    assert (
        invalidated_event.metadata["mss_origin_event_id"]
        == mss_event.event_id
    )


def test_no_stale_pending_mss_origin_leaks_into_second_cycle():
    # A first MSS/CHoCH cycle resolves (origin index 43); a fully
    # independent second MSS later fires (origin index 119, after a
    # second cycle establishes its own fresh protected_low via a
    # properly-classified HL). The second cycle's own origin must be
    # reported exactly, never the first cycle's stale value, and the
    # gap between the two cycles must show no lingering value at all.
    #
    # NOTE (Decision #3, Phase 7): under per-cycle classification, the
    # first swing low after the row-64 CHoCH boundary (row 72) is
    # correctly unlabeled (§7 point 4 — no baseline yet in the new
    # cycle) rather than "HL" as it would be under the retired global
    # classifier, so protected_low is not replaced until the *second*
    # new-cycle low (row 104). This fixture was extended past its
    # original (pre-Phase-7) length specifically to reach that
    # replacement and a subsequent genuine break of it — verified
    # empirically, not hand-derived.
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,
            1.2020,
            1.1850,
            1.1900,
            1.1800,
            1.2100,
            1.1950,
            1.2200,
            1.2100,
            1.2300,
            1.2000,
            1.2350,
            1.2150,
            1.2450,
            1.2050,
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    # Cycle 1: MSS at 43, resolved via CHoCH at 64.
    assert cell(result, 43, "structure_event") == "MSS"
    assert cell(result, 64, "structure_event") == "CHoCH"
    assert cell(result, 64, "mss_origin_index") is None

    # Gap between cycles: no lingering origin index from cycle 1.
    for position in (68, 72, 76, 80, 84, 96, 104, 112):
        assert cell(result, position, "mss_origin_index") is None

    # Cycle 2: a fresh, independent MSS fires with its own origin.
    assert cell(result, 119, "structure_event") == "MSS"
    second_origin = cell(result, 119, "mss_origin_index")
    assert second_origin is not None
    assert second_origin != 43
    assert second_origin == 119
