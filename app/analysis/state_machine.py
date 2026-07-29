import pandas as pd


VALID_TRENDS = {"neutral", "bullish", "bearish"}

VALID_STATES = {
    "neutral",
    "bullish",
    "bearish",
    "mss_bullish",
    "mss_bearish",
}


def detect_structure_state(
    swings: pd.DataFrame,
    atr_column: str = "atr14",
    minimum_break_atr: float = 0.10,
) -> pd.DataFrame:
    """
    Detect external market structure using a two-stage reversal model.

    Decision #3 (SMC_SPECIFICATION.md §7): this function now performs
    HH/HL/LH/LL classification itself, per confirmed trend cycle, in
    the same forward pass as state-transition detection — it no
    longer consumes a pre-classified "structure" column. The
    comparison baseline (this function's own cycle_previous_high/
    cycle_previous_low) resets to unset the moment a CHoCH confirms,
    but only for swings after that row; the CHoCH-confirming swing
    itself is classified under the baseline of the cycle it completes
    (§7 points 1-2). This satisfies §7's own architectural
    invariants: classification and cycle-boundary detection are
    computed through a single, causally forward pass (point 5); no
    separate classification pass followed by reclassification is
    permitted (point 6, "two-pass bootstrap prohibited").

    The legacy, globally-scoped classifier
    (market_structure.py::classify_market_structure) is unaffected
    and continues to serve the legacy /analysis/market-structure
    pipeline only, per §7 point 7 — this function's caller no longer
    invokes it for the canonical pipeline (see analysis_engine.py).

    Structure process
    -----------------

    Bullish continuation:
        Bullish trend + break above latest swing high = bullish BOS.

    Bearish continuation:
        Bearish trend + break below latest swing low = bearish BOS.

    Potential bearish reversal:
        Bullish trend + break below protected low = bearish MSS.

    Confirmed bearish reversal:
        Bearish MSS + confirmed LH followed by LL = bearish CHoCH.

    Potential bullish reversal:
        Bearish trend + break above protected high = bullish MSS.

    Confirmed bullish reversal:
        Bullish MSS + confirmed HL followed by HH = bullish CHoCH.

    Important
    ---------
    An MSS does not immediately reverse the confirmed external trend.
    The trend changes only after the opposite swing sequence confirms
    the CHoCH.
    """

    required_columns = {
        "close",
        "high",
        "low",
        "swing_high",
        "swing_low",
        atr_column,
    }

    missing_columns = required_columns.difference(swings.columns)

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing required columns: {missing}")

    if minimum_break_atr < 0:
        raise ValueError("minimum_break_atr cannot be negative.")

    result = swings.copy()

    # Per-cycle classification output (Decision #3, §7) — computed by
    # this function itself; see the unified 1a/1 step below.
    result["structure"] = pd.NA

    # Confirmed trend and current state
    result["external_trend"] = "neutral"
    result["structure_state"] = "neutral"

    # Structural event information
    result["structure_event"] = pd.NA
    result["event_direction"] = pd.NA

    # Swing-state information
    result["latest_swing_high"] = pd.NA
    result["latest_swing_low"] = pd.NA
    result["protected_high"] = pd.NA
    result["protected_low"] = pd.NA

    # Protected-level status/source (Decision #10, SMC_SPECIFICATION.md
    # §26): status distinguishes validity (active/broken); source
    # distinguishes provenance (hl/lh/latest_swing).
    result["protected_high_status"] = pd.NA
    result["protected_high_source"] = pd.NA
    result["protected_low_status"] = pd.NA
    result["protected_low_source"] = pd.NA

    # Break information
    result["broken_level"] = pd.NA
    result["break_distance"] = pd.NA
    result["required_break_distance"] = pd.NA

    # MSS confirmation progress
    result["mss_confirmation_step"] = pd.NA
    result["mss_origin_level"] = pd.NA

    # MSS invalidation (Decision #6, SMC_SPECIFICATION.md §19).
    # mss_origin_index mirrors mss_origin_level (written every row of
    # the pending phase); mss_invalidated_origin_index is populated
    # only on the invalidation candle itself, as the join key back to
    # the originating MSS occurrence.
    result["mss_origin_index"] = pd.NA
    result["mss_invalidated_origin_index"] = pd.NA

    # State-transition information
    result["trend_before_event"] = pd.NA
    result["trend_after_event"] = pd.NA
    result["state_before_event"] = pd.NA
    result["state_after_event"] = pd.NA

    current_trend = "neutral"
    current_state = "neutral"

    latest_swing_high = None
    latest_swing_low = None

    # Internal-only tracking for Decision #11 (§27): the latest
    # swing-CONFIRMED (detected) high/low, regardless of whether it
    # has been classified (labeled HH/HL/LH/LL). This is deliberately
    # separate from latest_swing_high/latest_swing_low above, whose
    # existing values and output-column semantics (latest CLASSIFIED
    # swing) are preserved unchanged. The distinction matters because
    # the very first swing of each type is always unlabeled (§7) —
    # without this separate tracker, a lone-HH/lone-LL trend
    # initialization could never see an already-detected opposite-type
    # swing that has not yet reached its own second (and therefore
    # labeled) occurrence, making the reseed below unreachable for any
    # input.
    #
    # This tracker is deliberately NOT reset at cycle boundaries
    # (Decision #3, §7): §27's own residual-gap clause is scoped "in
    # the series" as a whole, not per cycle, and both of this
    # tracker's consumers below (lone-HH/LL trend initialization,
    # which only ever happens once, before the first cycle even
    # begins; and Decision #6's post-invalidation reseed, which per §7
    # point 1 always occurs within an already-active, not-yet-reset
    # cycle) never actually require per-cycle scoping.
    latest_detected_swing_high = None
    latest_detected_swing_low = None

    # Per-cycle classification baseline (Decision #3, §7 points 1-4).
    # Resets to None at each confirmed CHoCH — see both CHoCH-
    # confirmation branches below — never at MSS invalidation (§7
    # point 1: "MSS alone never ends a cycle").
    cycle_previous_high = None
    cycle_previous_low = None

    protected_high = None
    protected_low = None

    protected_high_status = None
    protected_high_source = None
    protected_low_status = None
    protected_low_source = None

    candidate_high = None
    candidate_low = None

    # Prevent repeated breaks of the same level.
    active_bullish_bos_level = None
    active_bearish_bos_level = None

    broken_bullish_bos_level = None
    broken_bearish_bos_level = None

    broken_bullish_mss_level = None
    broken_bearish_mss_level = None

    # MSS confirmation flags.
    bearish_mss_has_lh = False
    bullish_mss_has_hl = False

    mss_origin_level = None
    mss_origin_index = None

    def safe_float(value):
        """Convert a value to float unless it is missing."""
        if pd.isna(value):
            return None

        return float(value)

    def get_swing_price(
        row_index,
        preferred_column: str,
        fallback_column: str,
    ):
        """
        Read a swing price from its dedicated column.

        If the dedicated swing-price column is unavailable or empty,
        use the candle high or low as a fallback.
        """
        if preferred_column in result.columns:
            preferred_value = result.at[row_index, preferred_column]

            if not pd.isna(preferred_value):
                return float(preferred_value)

        fallback_value = result.at[row_index, fallback_column]

        if pd.isna(fallback_value):
            return None

        return float(fallback_value)

    def reseed_from_latest_swing(latest_swing_value):
        """
        Seed a protected level from the latest classified
        opposite-type swing when no directly-classified value is
        available yet (Decision #11, SMC_SPECIFICATION.md §27) — the
        single reseed mechanism shared by trend-initialization
        Creation (this phase, called at the lone-HH/lone-LL branches
        below) and, once implemented, Decision #6's post-invalidation
        Reseed transition (§19).

        Returns (value, status, source). If no swing of the opposite
        type has ever been classified, returns (None, None, None) —
        the accepted residual gap (§27: "no seed value exists and the
        opposite protected level remains unset").
        """
        if latest_swing_value is None:
            return None, None, None

        return latest_swing_value, "active", "latest_swing"

    def store_current_state(row_index):
        """Store the active structural state for the current candle."""
        result.at[row_index, "external_trend"] = current_trend
        result.at[row_index, "structure_state"] = current_state

        if latest_swing_high is not None:
            result.at[
                row_index,
                "latest_swing_high",
            ] = latest_swing_high

        if latest_swing_low is not None:
            result.at[
                row_index,
                "latest_swing_low",
            ] = latest_swing_low

        if protected_high is not None:
            result.at[
                row_index,
                "protected_high",
            ] = protected_high

        if protected_low is not None:
            result.at[
                row_index,
                "protected_low",
            ] = protected_low

        if protected_high_status is not None:
            result.at[
                row_index,
                "protected_high_status",
            ] = protected_high_status

        if protected_high_source is not None:
            result.at[
                row_index,
                "protected_high_source",
            ] = protected_high_source

        if protected_low_status is not None:
            result.at[
                row_index,
                "protected_low_status",
            ] = protected_low_status

        if protected_low_source is not None:
            result.at[
                row_index,
                "protected_low_source",
            ] = protected_low_source

        if mss_origin_level is not None:
            result.at[
                row_index,
                "mss_origin_level",
            ] = mss_origin_level

        if mss_origin_index is not None:
            result.at[
                row_index,
                "mss_origin_index",
            ] = mss_origin_index

    for position, index in enumerate(result.index):
        trend_before = current_trend
        state_before = current_state

        event = None
        direction = None
        broken_level = None
        break_distance = None
        required_distance = None
        confirmation_step = None
        mss_invalidated_origin_index = None

        # ---------------------------------------------------------
        # 1a. Per-cycle classification (Decision #3, §7) unified with
        #     swing-confirmed detection tracking (Decision #11, §27)
        #     into the same forward pass — §7 INVARIANT point 5
        #     requires this; a separate classification pass followed
        #     by reclassification is prohibited (point 6).
        #
        #     Classification of THIS row depends only on
        #     cycle_previous_high/cycle_previous_low as they stood at
        #     the end of the PREVIOUS row (never on anything decided
        #     later in this same row) — the cycle-reset performed by
        #     the CHoCH-confirmation branches below always happens
        #     after this block runs, so a CHoCH-confirming row is
        #     classified under the cycle it completes, exactly as §7
        #     point 2 requires, and the reset only ever affects rows
        #     that follow.
        #
        #     Two independent `if` blocks (not `if`/`elif`) mirror the
        #     legacy classifier's own precedence for the (explicitly
        #     out-of-scope, §22 point 1) case of a candle that is
        #     simultaneously a swing high and a swing low: whichever
        #     check runs second — swing_low — silently overwrites
        #     structure_type if both fire on the same row. This is
        #     unchanged, preserved legacy behaviour, not a new rule.
        # ---------------------------------------------------------

        structure_type = None
        swing_high_price = None
        swing_low_price = None

        if bool(result.at[index, "swing_high"]):
            swing_high_price = get_swing_price(
                index,
                "swing_high_price",
                "high",
            )

            if swing_high_price is not None:
                latest_detected_swing_high = swing_high_price

                if cycle_previous_high is not None:
                    structure_type = (
                        "HH"
                        if swing_high_price > cycle_previous_high
                        else "LH"
                    )

                cycle_previous_high = swing_high_price

        if bool(result.at[index, "swing_low"]):
            swing_low_price = get_swing_price(
                index,
                "swing_low_price",
                "low",
            )

            if swing_low_price is not None:
                latest_detected_swing_low = swing_low_price

                if cycle_previous_low is not None:
                    structure_type = (
                        "HL"
                        if swing_low_price > cycle_previous_low
                        else "LL"
                    )

                cycle_previous_low = swing_low_price

        if structure_type is not None:
            result.at[index, "structure"] = structure_type

        # ---------------------------------------------------------
        # 1. Update confirmed swing information
        # ---------------------------------------------------------

        if structure_type == "HH":
            if swing_high_price is not None:
                latest_swing_high = swing_high_price
                active_bullish_bos_level = swing_high_price

            if current_state == "neutral":
                current_trend = "bullish"
                current_state = "bullish"

                # Decision #11 (§27): a lone HH at trend
                # initialization leaves protected_low unset unless a
                # swing low has already been classified to reseed
                # from.
                (
                    protected_low,
                    protected_low_status,
                    protected_low_source,
                ) = reseed_from_latest_swing(
                    latest_detected_swing_low
                )

            elif current_state == "mss_bullish":
                if bullish_mss_has_hl:
                    event = "CHoCH"
                    direction = "bullish"
                    broken_level = mss_origin_level
                    confirmation_step = "HL_TO_HH_CONFIRMED"

                    current_trend = "bullish"
                    current_state = "bullish"

                    # Creation (CHoCH-promotion sub-case, Decision
                    # #15 §10 point 1): candidate_low always
                    # originates from a directly-classified HL.
                    protected_low = candidate_low
                    protected_low_status = "active"
                    protected_low_source = "hl"

                    # Clearing (Decision #15 §10 point 4): the paired
                    # side effect, not itself a Creation/Replacement/
                    # Reseed of protected_high.
                    protected_high = None
                    protected_high_status = None
                    protected_high_source = None

                    bullish_mss_has_hl = False
                    bearish_mss_has_lh = False

                    broken_bullish_mss_level = None
                    broken_bearish_mss_level = None
                    mss_origin_level = None
                    # §19: mss_origin_index is parallel to
                    # mss_origin_level and must be cleared at the same
                    # two points (CHoCH confirmation, MSS invalidation).
                    # No per-row capture is needed here first — unlike
                    # mss_invalidated_origin_index at the invalidation
                    # branch, nothing reads mss_origin_index from the
                    # CHoCH row itself (the price is already preserved
                    # via broken_level, captured above).
                    mss_origin_index = None

                    # Decision #3 (§7 points 1-2): a confirmed CHoCH
                    # ends the active trend cycle. The reset takes
                    # effect starting with the next row's swings only
                    # — this row's own structure_type was already
                    # computed and written above, under the baseline
                    # of the cycle it completes.
                    cycle_previous_high = None
                    cycle_previous_low = None

                    active_bullish_bos_level = swing_high_price
                    broken_bullish_bos_level = None

            elif current_state == "mss_bearish":
                # Decision #6 (§19): a confirmed HH is the
                # same-original-direction (bullish-reasserting) swing
                # that invalidates a pending bearish MSS.
                event = "MSS_INVALIDATED"
                direction = "bullish"  # reasserted, pre-MSS direction
                broken_level = mss_origin_level

                current_state = "bullish"  # current_trend unchanged

                mss_invalidated_origin_index = mss_origin_index

                bearish_mss_has_lh = False
                bullish_mss_has_hl = False

                broken_bearish_mss_level = None
                broken_bullish_mss_level = None
                mss_origin_level = None
                mss_origin_index = None

                # Reseed the invalidated side (protected_low) per the
                # shared reseed mechanism (§27, reused from Decision
                # #11's Phase 3 helper — see reseed_from_latest_swing).
                (
                    protected_low,
                    protected_low_status,
                    protected_low_source,
                ) = reseed_from_latest_swing(
                    latest_detected_swing_low
                )

        elif structure_type == "HL":
            if swing_low_price is not None:
                latest_swing_low = swing_low_price
                candidate_low = swing_low_price

            if current_state == "neutral":
                current_trend = "bullish"
                current_state = "bullish"

                # Creation (direct-HL sub-case, Decision #15 §10
                # point 1).
                protected_low = swing_low_price
                protected_low_status = "active"
                protected_low_source = "hl"

            elif current_state == "bullish":
                # Replacement (Decision #15 §10 point 2) — this same
                # assignment covers both the continuation ratchet and
                # the provisional-to-permanent upgrade (a currently
                # active, latest_swing-sourced level being overwritten
                # by a properly-classified HL): both cases are
                # "already active -> active", differing only in the
                # value being replaced, never in status.
                protected_low = swing_low_price
                protected_low_status = "active"
                protected_low_source = "hl"
                broken_bearish_mss_level = None

            elif current_state == "mss_bullish":
                bullish_mss_has_hl = True
                confirmation_step = "HL_CONFIRMED"

        elif structure_type == "LL":
            if swing_low_price is not None:
                latest_swing_low = swing_low_price
                active_bearish_bos_level = swing_low_price

            if current_state == "neutral":
                current_trend = "bearish"
                current_state = "bearish"

                # Decision #11 (§27): a lone LL at trend
                # initialization leaves protected_high unset unless a
                # swing high has already been classified to reseed
                # from.
                (
                    protected_high,
                    protected_high_status,
                    protected_high_source,
                ) = reseed_from_latest_swing(
                    latest_detected_swing_high
                )

            elif current_state == "mss_bearish":
                if bearish_mss_has_lh:
                    event = "CHoCH"
                    direction = "bearish"
                    broken_level = mss_origin_level
                    confirmation_step = "LH_TO_LL_CONFIRMED"

                    current_trend = "bearish"
                    current_state = "bearish"

                    # Creation (CHoCH-promotion sub-case, Decision
                    # #15 §10 point 1): candidate_high always
                    # originates from a directly-classified LH.
                    protected_high = candidate_high
                    protected_high_status = "active"
                    protected_high_source = "lh"

                    # Clearing (Decision #15 §10 point 4).
                    protected_low = None
                    protected_low_status = None
                    protected_low_source = None

                    bearish_mss_has_lh = False
                    bullish_mss_has_hl = False

                    broken_bearish_mss_level = None
                    broken_bullish_mss_level = None
                    mss_origin_level = None
                    # §19: mirror of the bullish CHoCH branch above.
                    mss_origin_index = None

                    # Decision #3 (§7 points 1-2): mirror of the
                    # bullish CHoCH branch above.
                    cycle_previous_high = None
                    cycle_previous_low = None

                    active_bearish_bos_level = swing_low_price
                    broken_bearish_bos_level = None

            elif current_state == "mss_bullish":
                # Decision #6 (§19): a confirmed LL is the
                # same-original-direction (bearish-reasserting) swing
                # that invalidates a pending bullish MSS.
                event = "MSS_INVALIDATED"
                direction = "bearish"  # reasserted, pre-MSS direction
                broken_level = mss_origin_level

                current_state = "bearish"  # current_trend unchanged

                mss_invalidated_origin_index = mss_origin_index

                bullish_mss_has_hl = False
                bearish_mss_has_lh = False

                broken_bullish_mss_level = None
                broken_bearish_mss_level = None
                mss_origin_level = None
                mss_origin_index = None

                # Reseed the invalidated side (protected_high) per the
                # shared reseed mechanism (§27, reused from Decision
                # #11's Phase 3 helper — see reseed_from_latest_swing).
                (
                    protected_high,
                    protected_high_status,
                    protected_high_source,
                ) = reseed_from_latest_swing(
                    latest_detected_swing_high
                )

        elif structure_type == "LH":
            if swing_high_price is not None:
                latest_swing_high = swing_high_price
                candidate_high = swing_high_price

            if current_state == "neutral":
                current_trend = "bearish"
                current_state = "bearish"

                # Creation (direct-LH sub-case, Decision #15 §10
                # point 1).
                protected_high = swing_high_price
                protected_high_status = "active"
                protected_high_source = "lh"

            elif current_state == "bearish":
                # Replacement (Decision #15 §10 point 2) — mirror of
                # the HL continuation branch above.
                protected_high = swing_high_price
                protected_high_status = "active"
                protected_high_source = "lh"
                broken_bullish_mss_level = None

            elif current_state == "mss_bearish":
                bearish_mss_has_lh = True
                confirmation_step = "LH_CONFIRMED"

        # ---------------------------------------------------------
        # 2. Read candle and ATR values
        # ---------------------------------------------------------
        #
        # [APPROVED SPEC — Decision #8, §22 point 2, steps 4/7,
        # audit clarification] This guard's scope is limited to steps
        # 3-4 below (the close/ATR-dependent MSS and BOS checks): it
        # must never suppress a structure_event already determined at
        # step 1 (CHoCH or MSS_INVALIDATED), which reads neither
        # close nor ATR. Section 5 (the atomic write) always executes
        # for every row, regardless of data availability here.

        close_price = safe_float(result.at[index, "close"])
        atr_value = safe_float(result.at[index, atr_column])

        data_available = (
            close_price is not None and atr_value is not None
        )

        if data_available and atr_value < 0:
            raise ValueError(
                f"ATR value cannot be negative at index {index}."
            )

        if data_available:
            required_distance = atr_value * minimum_break_atr

        # ---------------------------------------------------------
        # 3. Confirmed bullish structure
        # ---------------------------------------------------------

        if data_available and current_state == "bullish" and event is None:

            # A protected-low break starts bearish MSS.
            if protected_low is not None:
                bearish_distance = protected_low - close_price

                if (
                    bearish_distance >= required_distance
                    and broken_bearish_mss_level != protected_low
                ):
                    event = "MSS"
                    direction = "bearish"
                    broken_level = protected_low
                    break_distance = bearish_distance

                    current_state = "mss_bearish"

                    broken_bearish_mss_level = protected_low
                    mss_origin_level = protected_low
                    mss_origin_index = position

                    # Decision #10 (§26): the level itself and its
                    # source are left unchanged, for transparency —
                    # only its validity status changes.
                    protected_low_status = "broken"

                    bearish_mss_has_lh = False
                    bullish_mss_has_hl = False

            # BOS is checked only when no MSS occurred.
            if (
                event is None
                and active_bullish_bos_level is not None
                and broken_bullish_bos_level
                != active_bullish_bos_level
            ):
                bullish_distance = (
                    close_price - active_bullish_bos_level
                )

                if bullish_distance >= required_distance:
                    event = "BOS"
                    direction = "bullish"
                    broken_level = active_bullish_bos_level
                    break_distance = bullish_distance

                    broken_bullish_bos_level = (
                        active_bullish_bos_level
                    )

                    if candidate_low is not None:
                        protected_low = candidate_low

        # ---------------------------------------------------------
        # 4. Confirmed bearish structure
        # ---------------------------------------------------------

        elif data_available and current_state == "bearish" and event is None:

            # A protected-high break starts bullish MSS.
            if protected_high is not None:
                bullish_distance = close_price - protected_high

                if (
                    bullish_distance >= required_distance
                    and broken_bullish_mss_level != protected_high
                ):
                    event = "MSS"
                    direction = "bullish"
                    broken_level = protected_high
                    break_distance = bullish_distance

                    current_state = "mss_bullish"

                    broken_bullish_mss_level = protected_high
                    mss_origin_level = protected_high
                    mss_origin_index = position

                    # Decision #10 (§26): the level itself and its
                    # source are left unchanged, for transparency —
                    # only its validity status changes.
                    protected_high_status = "broken"

                    bullish_mss_has_hl = False
                    bearish_mss_has_lh = False

            # BOS is checked only when no MSS occurred.
            if (
                event is None
                and active_bearish_bos_level is not None
                and broken_bearish_bos_level
                != active_bearish_bos_level
            ):
                bearish_distance = (
                    active_bearish_bos_level - close_price
                )

                if bearish_distance >= required_distance:
                    event = "BOS"
                    direction = "bearish"
                    broken_level = active_bearish_bos_level
                    break_distance = bearish_distance

                    broken_bearish_bos_level = (
                        active_bearish_bos_level
                    )

                    if candidate_high is not None:
                        protected_high = candidate_high

        # ---------------------------------------------------------
        # 5. Store event information
        # ---------------------------------------------------------

        if event is not None:
            result.at[index, "structure_event"] = event
            result.at[index, "event_direction"] = direction

            if broken_level is not None:
                result.at[index, "broken_level"] = broken_level

            if break_distance is not None:
                result.at[index, "break_distance"] = break_distance

            if required_distance is not None:
                result.at[
                    index,
                    "required_break_distance",
                ] = required_distance

            if mss_invalidated_origin_index is not None:
                result.at[
                    index,
                    "mss_invalidated_origin_index",
                ] = mss_invalidated_origin_index

        if confirmation_step is not None:
            result.at[
                index,
                "mss_confirmation_step",
            ] = confirmation_step

        result.at[index, "trend_before_event"] = trend_before
        result.at[index, "trend_after_event"] = current_trend
        result.at[index, "state_before_event"] = state_before
        result.at[index, "state_after_event"] = current_state

        store_current_state(index)

    return result
