"""
Phase 7 coverage for Decision #3 (SMC_SPECIFICATION.md §7): per-trend-
cycle HH/HL/LH/LL classification, computed by
state_machine.py::detect_structure_state itself in a single unified
forward pass, replacing the retired global, never-reset comparison
baseline the legacy classifier (market_structure.py::
classify_market_structure) still uses for the legacy pipeline only.

Every assertion below was verified empirically against actual
detect_structure_state/detect_order_blocks/analyze_market output
before being written, consistent with this project's established
practice of distrusting manual OHLC/index arithmetic.

The two-cycle fixture shared by most tests below
(_two_cycle_candles/_two_cycle_result) is the same 16-waypoint
extension already established and verified in
test_phase6_completion_patch.py::
test_no_stale_pending_mss_origin_leaks_into_second_cycle: cycle 1 runs
neutral -> bearish -> MSS(43) -> CHoCH(64, promotes to bullish); cycle
2 runs bullish -> MSS(119, bearish-direction break of the cycle's own
protected_low).
"""

from __future__ import annotations

from unittest.mock import patch

from app.analysis.analysis_engine import analyze_market
from app.analysis.liquidity import detect_liquidity_registry
from app.analysis.market_structure import (
    classify_market_structure,
    detect_swing_points,
)
from app.analysis.order_blocks import detect_order_blocks
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


def _two_cycle_candles():
    return build_zigzag_candles(
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


def _two_cycle_result():
    swings = _detect_swings(_two_cycle_candles())
    return detect_structure_state(swings, minimum_break_atr=0.10)


# --- 1. First structural cycle classification ---------------------------


def test_first_cycle_classification_sequence():
    # Cycle 1 (neutral -> bearish -> MSS -> CHoCH): the first swing of
    # each type is unlabeled (no baseline yet, §7 point 4); every
    # subsequent swing of that type is classified against it.
    result = _two_cycle_result()

    assert cell(result, 8, "structure") is None  # first low, no baseline
    assert cell(result, 16, "structure") is None  # first high, no baseline
    assert cell(result, 24, "structure") == "LL"
    assert cell(result, 32, "structure") == "LH"
    assert cell(result, 40, "structure") == "LL"
    assert cell(result, 48, "structure") == "HH"
    assert cell(result, 56, "structure") == "HL"

    # The CHoCH-confirming swing itself is classified under the
    # baseline of the cycle it completes (§7 point 2), not left
    # unlabeled by the reset it triggers.
    assert cell(result, 64, "structure") == "HH"
    assert cell(result, 64, "structure_event") == "CHoCH"


# --- 2. Transition into a new cycle --------------------------------------


def test_cycle_boundary_only_resets_after_the_choch_row():
    # §7 points 1-2: only a confirmed CHoCH ends a cycle, and the reset
    # takes effect starting with the row after the CHoCH-confirming
    # row, never the row itself.
    result = _two_cycle_result()

    assert cell(result, 64, "structure_event") == "CHoCH"
    assert cell(result, 64, "structure") == "HH"  # old-cycle baseline

    # First low and first high after the boundary: unlabeled, exactly
    # like the whole-series first swings (§7 point 4), because no
    # baseline exists yet in the new cycle.
    assert cell(result, 72, "structure") is None
    assert cell(result, 80, "structure") is None


# --- 3. Bullish-cycle classification --------------------------------------


def test_bullish_cycle_classification_sequence():
    # Cycle 2 (established bullish by the CHoCH at row 64): once its
    # own baseline is seeded (rows 72/80), subsequent swings are
    # classified against it, independent of cycle 1's history.
    result = _two_cycle_result()

    assert cell(result, 88, "structure") == "LL"
    assert cell(result, 96, "structure") == "HH"
    assert cell(result, 104, "structure") == "HL"
    assert cell(result, 112, "structure") == "HH"
    assert cell(result, 104, "structure_state") == "bullish"
    assert cell(result, 112, "structure_state") == "bullish"


# --- 4. Bearish-cycle classification --------------------------------------


def test_bearish_cycle_classification_sequence():
    # Cycle 1 above is itself the bearish-cycle case (LL/LH/LL under
    # structure_state == "bearish"); asserted independently here so
    # this requirement has its own dedicated, clearly-labelled test.
    result = _two_cycle_result()

    assert cell(result, 24, "structure") == "LL"
    assert cell(result, 24, "structure_state") == "bearish"
    assert cell(result, 32, "structure") == "LH"
    assert cell(result, 32, "structure_state") == "bearish"
    assert cell(result, 40, "structure") == "LL"
    assert cell(result, 40, "structure_state") == "bearish"


# --- 5. Swing classification immediately before/after a boundary --------


def test_swing_classification_immediately_around_cycle_boundary():
    result = _two_cycle_result()

    # Immediately before the boundary (56): still governed by cycle
    # 1's baseline, business as usual.
    assert cell(result, 56, "structure") == "HL"

    # The boundary row itself (64): governed by cycle 1's baseline
    # (§7 point 2 — the CHoCH-confirming swing completes the cycle it
    # is classified under, it does not start the new one).
    assert cell(result, 64, "structure") == "HH"

    # Immediately after the boundary (72): the new cycle's first low,
    # unlabeled — never compared against cycle 1's baseline.
    assert cell(result, 72, "structure") is None


# --- 6. Prevention of cross-cycle swing comparison -----------------------


def test_cross_cycle_comparison_is_prevented():
    # Direct, positive proof: under the retired global classifier,
    # rows 72/80 WOULD be labeled (leaking cycle 1's baseline across
    # the boundary) — confirmed by running the legacy classifier on
    # the identical swings. The canonical, per-cycle-reset pipeline
    # must diverge from it at exactly these two rows.
    candles = _two_cycle_candles()
    swings = _detect_swings(candles)

    canonical = detect_structure_state(swings, minimum_break_atr=0.10)
    legacy_classified = classify_market_structure(swings)

    assert cell(canonical, 72, "structure") is None
    assert cell(legacy_classified, 72, "structure") == "HL"

    assert cell(canonical, 80, "structure") is None
    assert cell(legacy_classified, 80, "structure") == "HH"


# --- 7. Equal-high / equal-low handling -----------------------------------


def test_equal_high_classifies_as_lh_not_hh():
    # Legacy behaviour, unchanged by Decision #3 (comparison operator
    # itself is out of scope — only the reset points changed): a swing
    # high exactly equal to the cycle baseline fails the strict ">"
    # check and is classified LH, never HH.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.1000, 1.1050, 1.0950],
        candles_per_leg=8,
    )
    result = detect_structure_state(
        _detect_swings(candles), minimum_break_atr=0.10
    )

    assert cell(result, 8, "structure") is None  # first high, unlabeled
    assert cell(result, 24, "high") == cell(result, 8, "high")
    assert cell(result, 24, "structure") == "LH"


def test_equal_low_classifies_as_ll_not_hl():
    candles = build_zigzag_candles(
        [1.1000, 1.0950, 1.1050, 1.0950, 1.1150],
        candles_per_leg=8,
    )
    result = detect_structure_state(
        _detect_swings(candles), minimum_break_atr=0.10
    )

    assert cell(result, 8, "structure") is None  # first low, unlabeled
    assert cell(result, 24, "low") == cell(result, 8, "low")
    assert cell(result, 24, "structure") == "LL"


# --- 8. Missing-data behaviour ---------------------------------------------


def test_missing_atr_does_not_affect_classification():
    # Classification (the "structure" column) reads only
    # swing_high/swing_low/high/low — never close or ATR. A corrupted
    # ATR value on a classification-bearing row must leave its
    # "structure" label completely untouched, even though it can still
    # suppress the close/ATR-dependent MSS/BOS checks on that same row
    # (already covered by test_baseline_state_machine.py's
    # missing-data-guard tests; this test isolates the classification
    # column specifically).
    candles = _two_cycle_candles()
    swings = _detect_swings(candles)

    corrupted = swings.copy()
    corrupted.loc[24, "atr14"] = float("nan")

    result = detect_structure_state(corrupted, minimum_break_atr=0.10)

    assert cell(result, 24, "structure") == "LL"


# --- 9. Determinism across repeated runs ----------------------------------


def test_classification_is_deterministic_across_repeated_runs():
    swings = _detect_swings(_two_cycle_candles())

    first_run = detect_structure_state(swings, minimum_break_atr=0.10)
    second_run = detect_structure_state(swings, minimum_break_atr=0.10)

    columns = [
        "structure",
        "structure_event",
        "event_direction",
        "structure_state",
        "protected_high",
        "protected_low",
        "mss_origin_index",
    ]

    for column in columns:
        assert list(first_run[column].astype(str)) == list(
            second_run[column].astype(str)
        )


# --- 10. Stable row-position and source-index tracking --------------------


def test_row_position_tracking_is_stable_across_cycles():
    # mss_origin_index must reflect the enumerate() row POSITION (not
    # a pandas index label) independently in each cycle, and must
    # match the position order_blocks.py assigns as created_index for
    # the same MSS-sourced block (their shared convention).
    result = _two_cycle_result()

    assert cell(result, 43, "mss_origin_index") == 43
    assert cell(result, 119, "mss_origin_index") == 119

    liquidity_dataframe, _, _ = detect_liquidity_registry(result)
    _, registry, _ = detect_order_blocks(liquidity_dataframe)

    mss_sourced = sorted(
        (b for b in registry.all() if b.source_event_type == "MSS"),
        key=lambda block: block.created_index,
    )
    created_indices = [block.created_index for block in mss_sourced]

    assert 43 in created_indices
    assert 119 in created_indices


# --- 11. Compatibility with MSS/CHoCH/BOS/MSS-invalidation logic ---------


def test_full_event_sequence_across_two_cycles():
    # BOS (continuation), MSS (reversal attempt) and CHoCH (confirmed
    # reversal) all still fire correctly, interleaved, across both
    # cycles under the unified per-cycle-classification pass.
    result = _two_cycle_result()

    events = list(result["structure_event"].dropna().astype(str))
    assert events == ["BOS", "MSS", "CHoCH", "BOS", "BOS", "MSS"]
    assert events.count("MSS") == 2
    assert events.count("CHoCH") == 1


def test_mss_invalidation_still_functions_under_unified_pass():
    # Regression: MSS invalidation (Decision #6, §19) must keep working
    # exactly as before, now that classification is folded into the
    # same forward pass rather than a separate prior one.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    result = detect_structure_state(
        _detect_swings(candles), minimum_break_atr=0.10
    )

    assert cell(result, 42, "structure_event") == "MSS"
    assert cell(result, 56, "structure_event") == "MSS_INVALIDATED"
    assert cell(result, 56, "mss_invalidated_origin_index") == 42


# --- 12. Compatibility with Order Block sourcing and promotion ----------


def test_order_blocks_sourced_and_promoted_independently_per_cycle():
    result = _two_cycle_result()

    liquidity_dataframe, _, _ = detect_liquidity_registry(result)
    _, registry, _ = detect_order_blocks(liquidity_dataframe)

    mss_sourced = {
        block.created_index: block
        for block in registry.all()
        if block.source_event_type == "MSS"
    }

    assert 43 in mss_sourced
    assert 119 in mss_sourced

    # Cycle 1's MSS-sourced block is promoted (confirmed) by cycle 1's
    # own CHoCH.
    cycle_one_block = mss_sourced[43]
    assert cycle_one_block.confirmation_status == "confirmed"
    assert cycle_one_block.confirming_event_type == "CHoCH"

    # Cycle 2's MSS-sourced block has no CHoCH yet in this fixture —
    # still provisional, and never confused with cycle 1's block.
    cycle_two_block = mss_sourced[119]
    assert cycle_two_block.confirmation_status == "provisional"
    assert cycle_two_block.confirming_event_id is None
    assert cycle_two_block.order_block_id != cycle_one_block.order_block_id


# --- 13. Full canonical pipeline output ------------------------------------


def test_canonical_pipeline_end_to_end_reflects_per_cycle_classification():
    candles = _two_cycle_candles()
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    event_types = [event.event_type for event in result.events]
    assert event_types.count("MSS") == 2
    assert event_types.count("CHoCH") == 1

    # §33: no explicit Decision #3 entry in the versioning table —
    # pipeline_version is not bumped by this phase.
    assert result.metadata["pipeline_version"] == "3.0.0"

    structure_values = list(
        result.structure["structure"].dropna().astype(str)
    )
    assert "HH" in structure_values
    assert "LL" in structure_values


# --- 14. Legacy endpoint regression behaviour ------------------------------


def test_legacy_endpoint_still_uses_global_classification_unaffected():
    # Positive proof of isolation (§7 point 7): the legacy endpoint,
    # unchanged by Decision #3, must still report row 72 as "HL" —
    # the OLD, global-classification label that the canonical pipeline
    # no longer produces for this same row (see
    # test_cross_cycle_comparison_is_prevented above).
    import main

    candles = _two_cycle_candles()

    with patch("main.get_candles", return_value=candles):
        response = main.market_structure_endpoint(
            "EURUSD",
            "H1",
            count=len(candles),
            left_bars=3,
            right_bars=3,
            minimum_break_atr=0.10,
        )

    swing_points = response["swing_points"]
    matching_rows = [
        row
        for row in swing_points
        if row["time"] == str(candles.iloc[72]["time"])
    ]

    assert matching_rows, (
        "Expected row 72 to be present in the legacy endpoint's "
        "tail(20) swing_points window."
    )
    assert matching_rows[0]["structure"] == "HL"
