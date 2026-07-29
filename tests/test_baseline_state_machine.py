"""
Baseline coverage for app.analysis.state_machine.detect_structure_state.

Two kinds of tests live here:

1. A golden-file snapshot of the full canonical pipeline (indicators ->
   swings -> classification -> state machine) over the real EURUSD H4
   fixture, as a broad change-detector.

2. Hand-built, fully-worked scenarios. Originally these pinned four
   specific, spec-confirmed current-behaviour GAPS (SMC_SPECIFICATION.md
   §19, §22/§24, §26, §27) deliberately, as Phase 0 instructed — they
   were not correctness tests. As of Phase 3 (Decisions #10/#11) and
   Phase 4 (Decision #6, and the Decision #8 missing-data-guard fix),
   every one of those gaps has been closed in code; the tests below
   have been updated in place, at each phase, to pin the corresponding
   FIXED behaviour instead — never silently deleted or left describing
   behaviour the code no longer has.
"""

from __future__ import annotations

import pandas as pd

from app.analysis.market_structure import detect_swing_points
from app.analysis.state_machine import detect_structure_state
from app.indicators.technical import calculate_indicators
from tests.helpers.candles import build_zigzag_candles
from tests.helpers.dataframe_compare import cell
from tests.helpers.golden import assert_matches_golden
from tests.helpers.serialize import dataframe_to_records


def _detect_swings(candles):
    # Decision #3 (Phase 7): detect_structure_state now performs
    # per-cycle classification itself — this helper stops at swing
    # detection, matching the canonical pipeline's actual shape.
    indicators = calculate_indicators(candles)
    return detect_swing_points(indicators, left_bars=3, right_bars=3)


def test_state_machine_matches_golden(eurusd_h4_candles):
    swings = _detect_swings(eurusd_h4_candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    columns = [
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

    assert_matches_golden(
        "state_machine_eurusd_h4",
        dataframe_to_records(result[columns]),
    )


def test_initialization_gap_is_closed_by_decision_11_when_prior_swing_exists():
    # §27/Decision #11 (implemented Phase 3): neutral -> bullish via a
    # lone HH (W3@24, second high; no HL confirmed yet) used to leave
    # protected_low unset until the first HL — this is the ORIGINAL,
    # pre-Phase-3 baseline behaviour this test pinned. It no longer
    # holds for this fixture: W2@16 is a genuine, swing-CONFIRMED low
    # (merely unclassified, since it is the first low of the series,
    # §7) — Decision #11's reseed mechanism tracks swing-confirmed
    # highs/lows regardless of classification (see
    # reseed_from_latest_swing/latest_detected_swing_low in
    # state_machine.py), so it correctly seeds protected_low from that
    # already-detected low the moment the lone-HH transition fires, as
    # a Creation (source="latest_swing"), later upgraded to a
    # Replacement (source="hl") at the first properly-classified HL —
    # exactly Decision #15's "provisional-to-permanent upgrade" case
    # (§10 point 2).
    # Waypoints: W1(8)=first high(unlabeled), W2(16)=first low
    # (unlabeled, but swing-CONFIRMED), W3(24)=second high -> "HH" ->
    # neutral->bullish, W4(32)=second low -> "HL".
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 24, "structure") == "HH"
    assert cell(result, 24, "structure_state") == "bullish"

    # Creation, seeded from the already-detected (but unclassified)
    # first swing low.
    assert cell(result, 24, "protected_low") is not None
    assert cell(result, 24, "protected_low_status") == "active"
    assert cell(result, 24, "protected_low_source") == "latest_swing"

    # Stays active/latest_swing through the interior of the HH->HL leg.
    assert cell(result, 30, "protected_low_status") == "active"
    assert cell(result, 30, "protected_low_source") == "latest_swing"

    # Replacement: upgraded to a properly-classified source at the
    # first real HL, not a second Reseed (Decision #15 §10 point 2).
    assert cell(result, 32, "structure") == "HL"
    assert cell(result, 32, "protected_low") is not None
    assert cell(result, 32, "protected_low_status") == "active"
    assert cell(result, 32, "protected_low_source") == "hl"


def test_initialization_residual_gap_still_applies_with_zero_prior_swings():
    # §27's accepted residual gap: if NO swing of the opposite type —
    # not even an unclassified one — has ever been confirmed by the
    # moment of a lone-HH/lone-LL transition, no seed value exists and
    # the protected level genuinely remains unset. Constructed
    # directly (not via the zigzag builder) so that `low` is strictly
    # increasing across the entire series: swing-low detection
    # (current_low < left_lows.min() and < right_lows.min()) can
    # therefore never fire, guaranteeing zero swing lows of any kind
    # exist before the second swing high (index 24) transitions
    # neutral -> bullish.
    highs = (
        [1.060 + 0.005 * i for i in range(9)]  # rising into swing high @8
        + [1.100 - 0.002 * i for i in range(1, 9)]  # falling out of it
        + [1.084 + 0.0055 * i for i in range(1, 9)]  # rising into swing high @24
        + [1.128 - 0.002 * i for i in range(1, 6)]  # falling out of it
    )
    lows = [1.0400 + 0.0010 * i for i in range(len(highs))]

    assert len(highs) == 30
    assert len(lows) == 30

    opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes = list(opens)

    candles = pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=30, freq="1h", tz="UTC"
            ),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )

    swings = _detect_swings(candles)

    assert swings["swing_low"].sum() == 0
    assert swings.at[8, "swing_high"] == True  # noqa: E712
    assert swings.at[24, "swing_high"] == True  # noqa: E712

    result = detect_structure_state(swings, minimum_break_atr=0.10)

    # Classification (Decision #3) is now computed by
    # detect_structure_state itself, not a prior pass — verified here
    # on its output rather than on an intermediate "classified" frame.
    assert cell(result, 24, "structure") == "HH"

    assert cell(result, 24, "structure_state") == "bullish"
    assert cell(result, 24, "protected_low") is None
    assert cell(result, 24, "protected_low_status") is None
    assert cell(result, 24, "protected_low_source") is None


def test_mss_invalidation_hh_during_mss_bearish():
    # §19, Decision #6 (implemented Phase 4): a confirmed HH during a
    # pending bearish MSS (the same-original-direction, bullish-
    # reasserting swing) now invalidates it, as a formal state
    # transition. This test previously pinned the pre-Phase-4 gap
    # (current_state never reverted); it now pins the fix.
    #
    # Scenario: bullish trend established (HH@24, HL@32 sets
    # protected_low) -> a small bounce (LH@40, buffer only) -> a sharp
    # drop through protected_low mid-leg fires a bearish MSS
    # (current_state -> "mss_bearish", at row 42) -> LL@48 (confirming-
    # type swing, no-op while bearish_mss_has_lh is False) -> HH@56,
    # the invalidating swing.
    candles = build_zigzag_candles(
        [
            1.1000,
            1.1050,  # first high (unlabeled)
            1.0980,  # first low (unlabeled, but swing-CONFIRMED)
            1.1150,  # HH -> neutral -> bullish
            1.1080,  # HL -> protected_low set
            1.1120,  # LH (buffer only, keeps W4 a valid trough)
            1.0850,  # LL: sharp drop, breaks protected_low mid-leg
                     # -> bearish MSS fires -> current_state = mss_bearish
            1.1250,  # HH: same-original-direction swing -> invalidates
            1.1150,  # buffer trough
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 24, "structure_state") == "bullish"
    assert cell(result, 32, "protected_low") is not None

    assert cell(result, 42, "structure_event") == "MSS"
    assert cell(result, 42, "structure_state") == "mss_bearish"
    mss_origin_index = cell(result, 42, "mss_origin_index")
    assert mss_origin_index == 42

    # Bearish MSS is still pending by the time the LL waypoint
    # (position 48) is reached — a no-op, confirming-type swing.
    assert cell(result, 48, "structure_state") == "mss_bearish"
    assert cell(result, 48, "structure") == "LL"
    assert cell(result, 48, "structure_event") is None

    # The invalidating HH at position 56.
    assert cell(result, 56, "structure") == "HH"
    assert cell(result, 56, "structure_event") == "MSS_INVALIDATED"
    assert cell(result, 56, "event_direction") == "bullish"
    assert cell(result, 56, "structure_state") == "bullish"
    # current_trend, unlike structure_state, never changed throughout
    # the pending-MSS phase — invalidation reverts current_state to
    # the trend that was already, and remains, "bullish".
    assert cell(result, 56, "external_trend") == "bullish"

    # broken_level carries the invalidated level's price, for
    # transparency; mss_invalidated_origin_index joins back to the
    # exact row the MSS was created on.
    assert cell(result, 56, "broken_level") == cell(
        result, 42, "protected_low"
    )
    assert cell(result, 56, "mss_invalidated_origin_index") == 42

    # Reseed (Decision #15's broken -> active transition, resolved by
    # Decision #6): protected_low is re-established from the latest
    # swing-confirmed low, not left stale.
    assert cell(result, 56, "protected_low") is not None
    assert cell(result, 56, "protected_low") != cell(result, 42, "protected_low")
    assert cell(result, 56, "protected_low_status") == "active"
    assert cell(result, 56, "protected_low_source") == "latest_swing"

    # All pending-MSS bookkeeping is cleared — no stale MSS data.
    assert cell(result, 56, "mss_origin_level") is None
    assert cell(result, 56, "mss_confirmation_step") is None


def test_mss_invalidation_ll_during_mss_bullish():
    # Mirror of the previous test: bearish trend, bullish MSS
    # pending, a same-original-direction (bearish) LL now invalidates
    # it too.
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,  # first low (unlabeled)
            1.2020,  # first high (unlabeled, but swing-CONFIRMED)
            1.1850,  # LL -> neutral -> bearish
            1.1900,  # LH -> protected_high set
            1.1800,  # LL (buffer only, keeps W4 a valid peak)
            1.2100,  # HH: sharp rise, breaks protected_high mid-leg
                     # -> bullish MSS fires -> current_state = mss_bullish
            1.1750,  # LL: same-original-direction swing -> invalidates
            1.1850,  # buffer peak
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    assert cell(result, 24, "structure_state") == "bearish"
    assert cell(result, 32, "protected_high") is not None

    assert cell(result, 48, "structure_state") == "mss_bullish"
    assert cell(result, 48, "structure") == "HH"

    assert cell(result, 56, "structure") == "LL"
    assert cell(result, 56, "structure_event") == "MSS_INVALIDATED"
    assert cell(result, 56, "event_direction") == "bearish"
    assert cell(result, 56, "structure_state") == "bearish"
    assert cell(result, 56, "external_trend") == "bearish"

    assert cell(result, 56, "protected_high") is not None
    assert cell(result, 56, "protected_high_status") == "active"
    assert cell(result, 56, "protected_high_source") == "latest_swing"

    assert cell(result, 56, "mss_origin_level") is None
    assert cell(result, 56, "mss_confirmation_step") is None


def test_stale_protected_level_is_now_resolved_by_invalidation():
    # §26 gap, fully closed as of Phase 4 for any window that reaches
    # an invalidating swing: Decision #10 (Phase 3) made the
    # staleness VISIBLE (protected_low_status == "broken"); Decision
    # #6 (Phase 4) now RESOLVES it — the level is reseeded the moment
    # a same-original-direction swing invalidates the pending MSS.
    # Within the pending window itself (between the MSS firing and
    # its eventual invalidation), the level genuinely is still stale
    # — that part of the original behaviour is correctly preserved,
    # not a regression. Reuses the mss_bearish scenario above.
    candles = build_zigzag_candles(
        [
            1.1000,
            1.1050,
            1.0980,
            1.1150,
            1.1080,
            1.1120,
            1.0850,
            1.1250,
            1.1150,
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)
    result = detect_structure_state(swings, minimum_break_atr=0.10)

    protected_low_at_mss = cell(result, 42, "protected_low")
    assert protected_low_at_mss is not None
    assert cell(result, 42, "structure_state") == "mss_bearish"
    assert cell(result, 42, "structure_event") == "MSS"

    # Decision #10: status flips to "broken" the instant MSS fires,
    # source is left untouched (still "hl", the level's true
    # provenance — status and source are independent axes).
    assert cell(result, 42, "protected_low_status") == "broken"
    assert cell(result, 42, "protected_low_source") == "hl"

    # Still stale within the pending window (before invalidation) —
    # the same (already-broken) value is still being reported.
    assert cell(result, 48, "protected_low") == protected_low_at_mss
    assert cell(result, 48, "protected_low_status") == "broken"

    # Decision #6: the HH at row 56 invalidates the pending MSS,
    # reseeding protected_low — no longer the stale, broken value.
    assert cell(result, 56, "structure_event") == "MSS_INVALIDATED"
    assert cell(result, 56, "protected_low") != protected_low_at_mss
    assert cell(result, 56, "protected_low_status") == "active"


def test_missing_data_guard_preserves_step3_event():
    # Audit-confirmed bug (SMC_SPECIFICATION.md §22 point 2, step 4/7;
    # §24), fixed in Phase 4: a structure_event already determined by
    # swing classification alone (here, a bullish CHoCH) must survive
    # even when close/ATR is simultaneously NaN on the same row, since
    # it never depended on either value. This test previously pinned
    # the pre-fix buggy behaviour (the event marker being silently
    # dropped); it now pins the fix.
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,  # first low (unlabeled)
            1.2020,  # first high (unlabeled)
            1.1850,  # LL -> neutral -> bearish
            1.1900,  # LH -> protected_high set
            1.1800,  # LL (buffer, keeps W4 a valid peak)
            1.2100,  # HH: breaks protected_high mid-leg ->
                     # bullish MSS fires
            1.1950,  # HL: sets bullish_mss_has_hl = True
            1.2200,  # HH: confirms bullish CHoCH (row 64)
            1.2100,  # buffer trough
        ],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)

    # Verify, on an unmodified run, that row 64 really is the CHoCH
    # row this test depends on before corrupting the fixture.
    control = detect_structure_state(swings, minimum_break_atr=0.10)
    assert cell(control, 64, "structure_event") == "CHoCH"
    assert cell(control, 64, "event_direction") == "bullish"

    corrupted = swings.copy()
    corrupted.loc[64, "atr14"] = float("nan")

    result = detect_structure_state(corrupted, minimum_break_atr=0.10)

    # The fix: the event marker survives the missing-data guard,
    # exactly matching the unmodified control run...
    assert cell(result, 64, "structure_event") == "CHoCH"
    assert cell(result, 64, "event_direction") == "bullish"
    assert cell(result, 64, "broken_level") == cell(
        control, 64, "broken_level"
    )

    # ...consistent with the state transition, which already happened
    # internally on this same row regardless (store_current_state has
    # always run before the missing-data guard's early exit/skip).
    assert cell(result, 64, "external_trend") == "bullish"
    assert cell(result, 64, "structure_state") == "bullish"


def test_missing_data_guard_still_skips_close_dependent_checks():
    # The guard's scope is correctly narrowed, not removed: a row
    # with missing close/ATR must still skip the close-driven MSS/BOS
    # checks (Decision #8, §22 point 2, step 4) — only step-3 events
    # (CHoCH/MSS_INVALIDATED) are exempted from being dropped.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1250],
        candles_per_leg=8,
    )
    swings = _detect_swings(candles)

    corrupted = swings.copy()
    # Row 32 (HL, sets protected_low) has no swing-driven event of its
    # own; corrupting its ATR must not fabricate one, and must not
    # raise (the negative-ATR check is itself gated on data
    # availability).
    corrupted.loc[32, "atr14"] = float("nan")

    result = detect_structure_state(corrupted, minimum_break_atr=0.10)

    assert cell(result, 32, "structure") == "HL"
    assert cell(result, 32, "structure_event") is None
    assert cell(result, 32, "protected_low") is not None
    assert cell(result, 32, "protected_low_status") == "active"
