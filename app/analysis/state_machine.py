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
    classified: pd.DataFrame,
    atr_column: str = "atr14",
    minimum_break_atr: float = 0.10,
) -> pd.DataFrame:
    """
    Detect external market structure using a two-stage reversal model.

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
        "structure",
        atr_column,
    }

    missing_columns = required_columns.difference(classified.columns)

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing required columns: {missing}")

    if minimum_break_atr < 0:
        raise ValueError("minimum_break_atr cannot be negative.")

    result = classified.copy()

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

    # Break information
    result["broken_level"] = pd.NA
    result["break_distance"] = pd.NA
    result["required_break_distance"] = pd.NA

    # MSS confirmation progress
    result["mss_confirmation_step"] = pd.NA
    result["mss_origin_level"] = pd.NA

    # State-transition information
    result["trend_before_event"] = pd.NA
    result["trend_after_event"] = pd.NA
    result["state_before_event"] = pd.NA
    result["state_after_event"] = pd.NA

    current_trend = "neutral"
    current_state = "neutral"

    latest_swing_high = None
    latest_swing_low = None

    protected_high = None
    protected_low = None

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

        if mss_origin_level is not None:
            result.at[
                row_index,
                "mss_origin_level",
            ] = mss_origin_level

    for index in result.index:
        trend_before = current_trend
        state_before = current_state

        structure_value = result.at[index, "structure"]

        structure_type = None

        if not pd.isna(structure_value):
            structure_type = str(structure_value)

        event = None
        direction = None
        broken_level = None
        break_distance = None
        required_distance = None
        confirmation_step = None

        # ---------------------------------------------------------
        # 1. Update confirmed swing information
        # ---------------------------------------------------------

        if structure_type == "HH":
            swing_high_price = get_swing_price(
                index,
                "swing_high_price",
                "high",
            )

            if swing_high_price is not None:
                latest_swing_high = swing_high_price
                active_bullish_bos_level = swing_high_price

            if current_state == "neutral":
                current_trend = "bullish"
                current_state = "bullish"

            elif current_state == "mss_bullish":
                if bullish_mss_has_hl:
                    event = "CHoCH"
                    direction = "bullish"
                    broken_level = mss_origin_level
                    confirmation_step = "HL_TO_HH_CONFIRMED"

                    current_trend = "bullish"
                    current_state = "bullish"

                    protected_low = candidate_low
                    protected_high = None

                    bullish_mss_has_hl = False
                    bearish_mss_has_lh = False

                    broken_bullish_mss_level = None
                    broken_bearish_mss_level = None
                    mss_origin_level = None

                    active_bullish_bos_level = swing_high_price
                    broken_bullish_bos_level = None

        elif structure_type == "HL":
            swing_low_price = get_swing_price(
                index,
                "swing_low_price",
                "low",
            )

            if swing_low_price is not None:
                latest_swing_low = swing_low_price
                candidate_low = swing_low_price

            if current_state == "neutral":
                current_trend = "bullish"
                current_state = "bullish"

                protected_low = swing_low_price

            elif current_state == "bullish":
                protected_low = swing_low_price
                broken_bearish_mss_level = None

            elif current_state == "mss_bullish":
                bullish_mss_has_hl = True
                confirmation_step = "HL_CONFIRMED"

        elif structure_type == "LL":
            swing_low_price = get_swing_price(
                index,
                "swing_low_price",
                "low",
            )

            if swing_low_price is not None:
                latest_swing_low = swing_low_price
                active_bearish_bos_level = swing_low_price

            if current_state == "neutral":
                current_trend = "bearish"
                current_state = "bearish"

            elif current_state == "mss_bearish":
                if bearish_mss_has_lh:
                    event = "CHoCH"
                    direction = "bearish"
                    broken_level = mss_origin_level
                    confirmation_step = "LH_TO_LL_CONFIRMED"

                    current_trend = "bearish"
                    current_state = "bearish"

                    protected_high = candidate_high
                    protected_low = None

                    bearish_mss_has_lh = False
                    bullish_mss_has_hl = False

                    broken_bearish_mss_level = None
                    broken_bullish_mss_level = None
                    mss_origin_level = None

                    active_bearish_bos_level = swing_low_price
                    broken_bearish_bos_level = None

        elif structure_type == "LH":
            swing_high_price = get_swing_price(
                index,
                "swing_high_price",
                "high",
            )

            if swing_high_price is not None:
                latest_swing_high = swing_high_price
                candidate_high = swing_high_price

            if current_state == "neutral":
                current_trend = "bearish"
                current_state = "bearish"

                protected_high = swing_high_price

            elif current_state == "bearish":
                protected_high = swing_high_price
                broken_bullish_mss_level = None

            elif current_state == "mss_bearish":
                bearish_mss_has_lh = True
                confirmation_step = "LH_CONFIRMED"

        # ---------------------------------------------------------
        # 2. Read candle and ATR values
        # ---------------------------------------------------------

        close_price = safe_float(result.at[index, "close"])
        atr_value = safe_float(result.at[index, atr_column])

        if close_price is None or atr_value is None:
            result.at[index, "trend_before_event"] = trend_before
            result.at[index, "trend_after_event"] = current_trend
            result.at[index, "state_before_event"] = state_before
            result.at[index, "state_after_event"] = current_state

            if confirmation_step is not None:
                result.at[
                    index,
                    "mss_confirmation_step",
                ] = confirmation_step

            store_current_state(index)
            continue

        if atr_value < 0:
            raise ValueError(
                f"ATR value cannot be negative at index {index}."
            )

        required_distance = atr_value * minimum_break_atr

        # ---------------------------------------------------------
        # 3. Confirmed bullish structure
        # ---------------------------------------------------------

        if current_state == "bullish" and event is None:

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

        elif current_state == "bearish" and event is None:

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
