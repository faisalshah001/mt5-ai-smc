import pandas as pd


def detect_advanced_market_structure(
    classified: pd.DataFrame,
    atr_column: str = "atr14",
    minimum_break_atr: float = 0.10,
) -> pd.DataFrame:
    """
    Detect external market structure using a state-machine approach.

    The detector tracks:

    - Current external trend
    - Latest confirmed swing high
    - Latest confirmed swing low
    - Protected high
    - Protected low
    - Bullish and bearish BOS
    - Bullish and bearish CHoCH

    Event rules
    -----------

    Bullish BOS:
        The market is bullish and candle close breaks above the latest
        confirmed swing high by the required ATR distance.

    Bearish BOS:
        The market is bearish and candle close breaks below the latest
        confirmed swing low by the required ATR distance.

    Bullish CHoCH:
        The market is bearish and candle close breaks above the protected
        swing high by the required ATR distance.

    Bearish CHoCH:
        The market is bullish and candle close breaks below the protected
        swing low by the required ATR distance.

    Notes
    -----
    - Breaks are confirmed using candle close.
    - ATR is used to reduce weak or insignificant break signals.
    - Each structural level can generate only one confirmed break.
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

    # Main state output
    result["advanced_trend"] = "neutral"
    result["advanced_event"] = pd.NA
    result["advanced_direction"] = pd.NA

    # Swing-state output
    result["latest_swing_high"] = pd.NA
    result["latest_swing_low"] = pd.NA
    result["protected_high"] = pd.NA
    result["protected_low"] = pd.NA
    result["protected_level"] = pd.NA

    # Break information
    result["advanced_broken_level"] = pd.NA
    result["advanced_break_distance"] = pd.NA
    result["advanced_required_distance"] = pd.NA

    # State before and after each candle
    result["trend_before_event"] = pd.NA
    result["trend_after_event"] = pd.NA

    current_trend = "neutral"

    latest_swing_high = None
    latest_swing_low = None

    protected_high = None
    protected_low = None

    # Candidate pullback levels are used when establishing
    # or updating protected structural levels.
    candidate_high = None
    candidate_low = None

    # Prevent the same structural level from producing
    # repeated BOS or CHoCH events.
    bullish_bos_level_broken = True
    bearish_bos_level_broken = True

    bullish_choch_level_broken = True
    bearish_choch_level_broken = True

    def get_numeric_value(
        row_index,
        preferred_column: str,
        fallback_column: str,
    ):
        """
        Get a numeric value safely from the preferred column.

        If the preferred column does not exist or contains NA,
        use the fallback column.
        """
        value = None

        if preferred_column in result.columns:
            preferred_value = result.at[row_index, preferred_column]

            if not pd.isna(preferred_value):
                value = preferred_value

        if value is None:
            fallback_value = result.at[row_index, fallback_column]

            if not pd.isna(fallback_value):
                value = fallback_value

        if value is None:
            return None

        return float(value)

    for index in result.index:
        structure_type = result.at[index, "structure"]

        trend_before = current_trend

        # ---------------------------------------------------------
        # 1. Update confirmed swing-point state
        # ---------------------------------------------------------

        if not pd.isna(structure_type):
            structure_type = str(structure_type)

            if structure_type == "HH":
                swing_high_price = get_numeric_value(
                    index,
                    "swing_high_price",
                    "high",
                )

                if swing_high_price is not None:
                    latest_swing_high = swing_high_price

                    # A newly confirmed high becomes a fresh
                    # bullish continuation level.
                    bullish_bos_level_broken = False

                if current_trend == "neutral":
                    current_trend = "bullish"

                    if candidate_low is not None:
                        protected_low = candidate_low
                        bearish_choch_level_broken = False

            elif structure_type == "HL":
                swing_low_price = get_numeric_value(
                    index,
                    "swing_low_price",
                    "low",
                )

                if swing_low_price is not None:
                    latest_swing_low = swing_low_price
                    candidate_low = swing_low_price

                if current_trend in {"neutral", "bullish"}:
                    current_trend = "bullish"

                    protected_low = swing_low_price
                    bearish_choch_level_broken = False

            elif structure_type == "LL":
                swing_low_price = get_numeric_value(
                    index,
                    "swing_low_price",
                    "low",
                )

                if swing_low_price is not None:
                    latest_swing_low = swing_low_price

                    # A newly confirmed low becomes a fresh
                    # bearish continuation level.
                    bearish_bos_level_broken = False

                if current_trend == "neutral":
                    current_trend = "bearish"

                    if candidate_high is not None:
                        protected_high = candidate_high
                        bullish_choch_level_broken = False

            elif structure_type == "LH":
                swing_high_price = get_numeric_value(
                    index,
                    "swing_high_price",
                    "high",
                )

                if swing_high_price is not None:
                    latest_swing_high = swing_high_price
                    candidate_high = swing_high_price

                if current_trend in {"neutral", "bearish"}:
                    current_trend = "bearish"

                    protected_high = swing_high_price
                    bullish_choch_level_broken = False

        # ---------------------------------------------------------
        # 2. Read candle confirmation values
        # ---------------------------------------------------------

        close_value = result.at[index, "close"]
        atr_value = result.at[index, atr_column]

        if pd.isna(close_value) or pd.isna(atr_value):
            result.at[index, "advanced_trend"] = current_trend
            result.at[index, "trend_before_event"] = trend_before
            result.at[index, "trend_after_event"] = current_trend

            if latest_swing_high is not None:
                result.at[index, "latest_swing_high"] = latest_swing_high

            if latest_swing_low is not None:
                result.at[index, "latest_swing_low"] = latest_swing_low

            if protected_high is not None:
                result.at[index, "protected_high"] = protected_high

            if protected_low is not None:
                result.at[index, "protected_low"] = protected_low

            continue

        close_price = float(close_value)
        atr = float(atr_value)

        if atr < 0:
            raise ValueError(
                f"ATR value cannot be negative at index {index}."
            )

        required_distance = atr * minimum_break_atr

        event = None
        direction = None
        broken_level = None
        protected_level_used = None
        break_distance = None

        # ---------------------------------------------------------
        # 3. Bullish external structure
        # ---------------------------------------------------------

        if current_trend == "bullish":

            # CHoCH is checked before BOS because breaking the
            # protected low invalidates the bullish structure.
            if (
                protected_low is not None
                and not bearish_choch_level_broken
            ):
                bearish_break_distance = (
                    protected_low - close_price
                )

                if bearish_break_distance >= required_distance:
                    event = "CHoCH"
                    direction = "bearish"
                    broken_level = protected_low
                    protected_level_used = protected_low
                    break_distance = bearish_break_distance

                    current_trend = "bearish"

                    # The most recent swing high becomes the
                    # protected high for the new bearish structure.
                    protected_high = latest_swing_high

                    protected_low = None
                    candidate_low = None

                    bearish_choch_level_broken = True

                    if protected_high is not None:
                        bullish_choch_level_broken = False
                    else:
                        bullish_choch_level_broken = True

                    # Do not immediately generate a bearish BOS
                    # from a level already crossed during CHoCH.
                    bearish_bos_level_broken = True
                    bullish_bos_level_broken = True

            # Bullish BOS is only valid while the bullish
            # protected low remains intact.
            if (
                event is None
                and latest_swing_high is not None
                and not bullish_bos_level_broken
            ):
                bullish_break_distance = (
                    close_price - latest_swing_high
                )

                if bullish_break_distance >= required_distance:
                    event = "BOS"
                    direction = "bullish"
                    broken_level = latest_swing_high
                    break_distance = bullish_break_distance

                    bullish_bos_level_broken = True

                    # After continuation, the latest valid
                    # pullback low becomes protected.
                    if candidate_low is not None:
                        protected_low = candidate_low
                        bearish_choch_level_broken = False

        # ---------------------------------------------------------
        # 4. Bearish external structure
        # ---------------------------------------------------------

        elif current_trend == "bearish":

            # CHoCH is checked before BOS because breaking the
            # protected high invalidates the bearish structure.
            if (
                protected_high is not None
                and not bullish_choch_level_broken
            ):
                bullish_break_distance = (
                    close_price - protected_high
                )

                if bullish_break_distance >= required_distance:
                    event = "CHoCH"
                    direction = "bullish"
                    broken_level = protected_high
                    protected_level_used = protected_high
                    break_distance = bullish_break_distance

                    current_trend = "bullish"

                    # The most recent swing low becomes the
                    # protected low for the new bullish structure.
                    protected_low = latest_swing_low

                    protected_high = None
                    candidate_high = None

                    bullish_choch_level_broken = True

                    if protected_low is not None:
                        bearish_choch_level_broken = False
                    else:
                        bearish_choch_level_broken = True

                    # Do not immediately generate a bullish BOS
                    # from a level already crossed during CHoCH.
                    bullish_bos_level_broken = True
                    bearish_bos_level_broken = True

            # Bearish BOS is only valid while the bearish
            # protected high remains intact.
            if (
                event is None
                and latest_swing_low is not None
                and not bearish_bos_level_broken
            ):
                bearish_break_distance = (
                    latest_swing_low - close_price
                )

                if bearish_break_distance >= required_distance:
                    event = "BOS"
                    direction = "bearish"
                    broken_level = latest_swing_low
                    break_distance = bearish_break_distance

                    bearish_bos_level_broken = True

                    # After continuation, the latest valid
                    # pullback high becomes protected.
                    if candidate_high is not None:
                        protected_high = candidate_high
                        bullish_choch_level_broken = False

        # ---------------------------------------------------------
        # 5. Store event information
        # ---------------------------------------------------------

        if event is not None:
            result.at[index, "advanced_event"] = event
            result.at[index, "advanced_direction"] = direction
            result.at[index, "advanced_broken_level"] = broken_level
            result.at[index, "advanced_break_distance"] = (
                break_distance
            )
            result.at[index, "advanced_required_distance"] = (
                required_distance
            )

            if protected_level_used is not None:
                result.at[index, "protected_level"] = (
                    protected_level_used
                )

        # ---------------------------------------------------------
        # 6. Store current state on every candle
        # ---------------------------------------------------------

        result.at[index, "advanced_trend"] = current_trend
        result.at[index, "trend_before_event"] = trend_before
        result.at[index, "trend_after_event"] = current_trend

        if latest_swing_high is not None:
            result.at[index, "latest_swing_high"] = (
                latest_swing_high
            )

        if latest_swing_low is not None:
            result.at[index, "latest_swing_low"] = (
                latest_swing_low
            )

        if protected_high is not None:
            result.at[index, "protected_high"] = protected_high

        if protected_low is not None:
            result.at[index, "protected_low"] = protected_low

    return result