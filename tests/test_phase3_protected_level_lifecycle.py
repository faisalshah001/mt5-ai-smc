"""
Phase 3 coverage for the Protected High/Low lifecycle
(SMC_SPECIFICATION.md §26 Decision #10, §27 Decision #11, §10/§11
Decision #15).

Every assertion below was verified empirically against the actual
state_machine.py output before being written (not hand-derived),
consistent with this project's established practice of distrusting
manual OHLC/index arithmetic.
"""

from __future__ import annotations

import pandas as pd

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


def test_creation_direct_hl_sets_active_and_hl_source():
    # Creation (direct-HL sub-case, §10 point 1): the very first
    # labeled swing of the series is itself a HL (no prior swing of
    # any kind has reached its second occurrence yet), so
    # neutral -> bullish is entered directly via HL, not via a lone
    # HH — no reseed involved at all.
    candles = build_zigzag_candles(
        [1.1000, 1.0950, 1.1050, 1.0980, 1.1100, 1.1050],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 24, "structure") == "HL"
    assert cell(result, 24, "structure_state") == "bullish"
    assert cell(result, 24, "protected_low") is not None
    assert cell(result, 24, "protected_low_status") == "active"
    assert cell(result, 24, "protected_low_source") == "hl"

    # A subsequent HH is BOS-eligible only (current_state == "bullish",
    # not "mss_bullish") — closed set: it must not touch protected_low.
    assert cell(result, 32, "structure") == "HH"
    assert cell(result, 32, "protected_low_status") == "active"
    assert cell(result, 32, "protected_low_source") == "hl"


def _bearish_to_choch_scenario():
    # One coherent scenario exercising, in row order: Creation via
    # reseed (LL@24), Replacement/upgrade (LH@32), continuation
    # no-ops (LL@40), MSS firing -> status=broken (row 43), closed-set
    # no-ops during mss_bullish (HH@48, HL@56), and Creation via
    # CHoCH-promotion + Clearing (HH@64, confirmed CHoCH).
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
    return detect_structure_state(swings, minimum_break_atr=0.10)


def test_creation_via_reseed_at_bearish_initialization():
    result = _bearish_to_choch_scenario()

    assert cell(result, 24, "structure") == "LL"
    assert cell(result, 24, "structure_state") == "bearish"
    assert cell(result, 24, "protected_high") is not None
    assert cell(result, 24, "protected_high_status") == "active"
    assert cell(result, 24, "protected_high_source") == "latest_swing"


def test_replacement_upgrades_latest_swing_source_to_lh():
    # Decision #15 §10 point 2: a currently-active, latest_swing-
    # sourced level is overwritten by the next properly-classified LH
    # — a Replacement, not a second Reseed (status stays "active"
    # throughout; only source and value change).
    result = _bearish_to_choch_scenario()

    seeded_value = cell(result, 24, "protected_high")

    assert cell(result, 32, "structure") == "LH"
    assert cell(result, 32, "protected_high") != seeded_value
    assert cell(result, 32, "protected_high_status") == "active"
    assert cell(result, 32, "protected_high_source") == "lh"

    # Continuation ratchet (LL@40, bearish continuation) leaves
    # protected_high entirely untouched — closed set.
    upgraded_value = cell(result, 32, "protected_high")
    assert cell(result, 40, "structure") == "LL"
    assert cell(result, 40, "protected_high") == upgraded_value
    assert cell(result, 40, "protected_high_status") == "active"
    assert cell(result, 40, "protected_high_source") == "lh"


def test_status_becomes_broken_when_bullish_mss_fires():
    result = _bearish_to_choch_scenario()

    assert cell(result, 43, "structure_event") == "MSS"
    assert cell(result, 43, "event_direction") == "bullish"

    # Decision #10: value and source untouched, only status changes.
    pre_mss_value = cell(result, 40, "protected_high")
    pre_mss_source = cell(result, 40, "protected_high_source")
    assert cell(result, 43, "protected_high") == pre_mss_value
    assert cell(result, 43, "protected_high_source") == pre_mss_source
    assert cell(result, 43, "protected_high_status") == "broken"


def test_closed_set_mss_bullish_no_ops_never_modify_protected_high():
    # HH-during-mss_bullish (bullish_mss_has_hl still False -> no
    # CHoCH) and HL-during-mss_bullish (sets the confirmation flag
    # only) must never modify protected_high or its status/source —
    # Decision #15 §10 point 5's closed set.
    result = _bearish_to_choch_scenario()

    broken_value = cell(result, 43, "protected_high")
    broken_source = cell(result, 43, "protected_high_source")

    assert cell(result, 48, "structure") == "HH"
    assert cell(result, 48, "structure_state") == "mss_bullish"
    assert cell(result, 48, "protected_high") == broken_value
    assert cell(result, 48, "protected_high_status") == "broken"
    assert cell(result, 48, "protected_high_source") == broken_source

    assert cell(result, 56, "structure") == "HL"
    assert cell(result, 56, "structure_state") == "mss_bullish"
    assert cell(result, 56, "protected_high") == broken_value
    assert cell(result, 56, "protected_high_status") == "broken"
    assert cell(result, 56, "protected_high_source") == broken_source


def test_creation_via_choch_promotion_and_paired_clearing():
    result = _bearish_to_choch_scenario()

    assert cell(result, 64, "structure_event") == "CHoCH"
    assert cell(result, 64, "event_direction") == "bullish"
    assert cell(result, 64, "structure_state") == "bullish"

    # Creation (CHoCH-promotion sub-case): protected_low promoted
    # from candidate_low, which only ever originates from a directly-
    # classified HL (the HL@56 above) -> source is "hl", never
    # "latest_swing", regardless of how protected_high got there.
    assert cell(result, 64, "protected_low") is not None
    assert cell(result, 64, "protected_low_status") == "active"
    assert cell(result, 64, "protected_low_source") == "hl"

    # Clearing: the paired side effect on the opposite level — value,
    # status, and source all reset to None/unset together.
    assert cell(result, 64, "protected_high") is None
    assert cell(result, 64, "protected_high_status") is None
    assert cell(result, 64, "protected_high_source") is None


def test_closed_set_bos_only_sequence_never_touches_protected_levels():
    # A pure bullish-continuation sequence (BOS only, no MSS/CHoCH)
    # must never modify protected_low or its status/source beyond the
    # Replacement transition itself (each new HL).
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1250, 1.1150, 1.1350],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 24, "structure_state") == "bullish"
    assert cell(result, 32, "structure") == "HL"

    replaced_value = cell(result, 32, "protected_low")
    replaced_status = cell(result, 32, "protected_low_status")
    replaced_source = cell(result, 32, "protected_low_source")
    assert replaced_status == "active"
    assert replaced_source == "hl"

    # HH@40 (BOS-eligible continuation, current_state stays "bullish")
    # must be a no-op for protected_low.
    assert cell(result, 40, "structure") == "HH"
    assert cell(result, 40, "structure_event") in (None, "BOS")
    assert cell(result, 40, "protected_low") == replaced_value
    assert cell(result, 40, "protected_low_status") == replaced_status
    assert cell(result, 40, "protected_low_source") == replaced_source


def test_protected_level_columns_present_in_state_machine_output():
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    for column in (
        "protected_high_status",
        "protected_high_source",
        "protected_low_status",
        "protected_low_source",
    ):
        assert column in result.columns
