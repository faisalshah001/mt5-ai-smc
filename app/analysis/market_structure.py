import pandas as pd


def detect_swing_points(
    candles: pd.DataFrame,
    left_bars: int = 3,
    right_bars: int = 3,
) -> pd.DataFrame:
    """
    Detect confirmed swing highs and swing lows.

    A swing high is higher than the selected number of candles
    on both its left and right sides.

    A swing low is lower than the selected number of candles
    on both its left and right sides.
    """
    required_columns = {"high", "low"}
    missing_columns = required_columns.difference(candles.columns)

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Missing required candle columns: {missing}"
        )

    if left_bars < 1 or right_bars < 1:
        raise ValueError(
            "left_bars and right_bars must both be at least 1."
        )

    minimum_rows = left_bars + right_bars + 1

    if len(candles) < minimum_rows:
        raise ValueError(
            f"At least {minimum_rows} candles are required "
            "to detect swing points."
        )

    result = candles.copy()

    result["swing_high"] = False
    result["swing_low"] = False
    result["swing_high_price"] = pd.NA
    result["swing_low_price"] = pd.NA

    for position in range(
        left_bars,
        len(result) - right_bars,
    ):
        current_high = result.iloc[position]["high"]
        current_low = result.iloc[position]["low"]

        left_highs = result.iloc[
            position - left_bars:position
        ]["high"]

        right_highs = result.iloc[
            position + 1:position + right_bars + 1
        ]["high"]

        left_lows = result.iloc[
            position - left_bars:position
        ]["low"]

        right_lows = result.iloc[
            position + 1:position + right_bars + 1
        ]["low"]

        is_swing_high = (
            current_high > left_highs.max()
            and current_high > right_highs.max()
        )

        is_swing_low = (
            current_low < left_lows.min()
            and current_low < right_lows.min()
        )

        row_index = result.index[position]

        if is_swing_high:
            result.at[row_index, "swing_high"] = True
            result.at[
                row_index,
                "swing_high_price",
            ] = current_high

        if is_swing_low:
            result.at[row_index, "swing_low"] = True
            result.at[
                row_index,
                "swing_low_price",
            ] = current_low

    return result


def classify_market_structure(
    structure: pd.DataFrame,
) -> pd.DataFrame:
    """
    Classify confirmed swing points using a single, whole-series,
    never-reset comparison baseline (previous_high/previous_low
    tracked globally from the first row onward, across every trend
    cycle).

    LEGACY-ONLY (SMC_SPECIFICATION.md §7, Decision #3, point 7): this
    global classifier now serves only the legacy
    /analysis/market-structure pipeline (main.py), for the duration of
    Decision B's Phase 1/2 deprecation window. It is deliberately
    unchanged and must remain byte-identical throughout that window
    (§7 point 9, acceptance criterion 6). The canonical pipeline
    (analysis_engine.py::analyze_market) no longer calls this function
    — app.analysis.state_machine.detect_structure_state performs
    per-trend-cycle classification itself, in the same unified forward
    pass as state-transition detection, per §7 points 1-6. This is not
    an undocumented fallback: it is the explicitly frozen, temporary
    two-engine state Decision B already approves, retired only when
    the legacy endpoint itself is removed (§7 point 8).

    HH = Higher High
    LH = Lower High
    HL = Higher Low
    LL = Lower Low
    """
    required_columns = {
        "high",
        "low",
        "swing_high",
        "swing_low",
    }

    missing_columns = required_columns.difference(
        structure.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    result = structure.copy()
    result["structure"] = pd.NA

    previous_high = None
    previous_low = None

    for index in result.index:
        if result.at[index, "swing_high"]:
            current_high = result.at[index, "high"]

            if previous_high is not None:
                if current_high > previous_high:
                    result.at[index, "structure"] = "HH"
                else:
                    result.at[index, "structure"] = "LH"

            previous_high = current_high

        if result.at[index, "swing_low"]:
            current_low = result.at[index, "low"]

            if previous_low is not None:
                if current_low > previous_low:
                    result.at[index, "structure"] = "HL"
                else:
                    result.at[index, "structure"] = "LL"

            previous_low = current_low

    return result


def detect_breaks_of_structure(
    classified: pd.DataFrame,
    atr_column: str = "atr14",
    minimum_break_atr: float = 0.10,
) -> pd.DataFrame:
    """
    Detect bullish and bearish Breaks of Structure using
    ATR-based confirmation.

    Bullish BOS:
    The candle close exceeds the latest confirmed swing high
    by at least minimum_break_atr multiplied by ATR.

    Bearish BOS:
    The candle close falls below the latest confirmed swing low
    by at least minimum_break_atr multiplied by ATR.
    """
    required_columns = {
        "close",
        "swing_high",
        "swing_low",
        "high",
        "low",
        atr_column,
    }

    missing_columns = required_columns.difference(
        classified.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    if minimum_break_atr < 0:
        raise ValueError(
            "minimum_break_atr cannot be negative."
        )

    result = classified.copy()

    result["bos"] = pd.NA
    result["broken_level"] = pd.NA
    result["break_distance"] = pd.NA
    result["required_break_distance"] = pd.NA

    latest_swing_high = None
    latest_swing_low = None

    bullish_level_broken = False
    bearish_level_broken = False

    for index in result.index:
        close_price = result.at[index, "close"]
        atr_value = result.at[index, atr_column]

        if pd.isna(close_price) or pd.isna(atr_value):
            continue

        close_price = float(close_price)
        required_distance = (
            float(atr_value) * minimum_break_atr
        )

        if (
            latest_swing_high is not None
            and not bullish_level_broken
        ):
            bullish_break_distance = (
                close_price - latest_swing_high
            )

            if bullish_break_distance >= required_distance:
                result.at[index, "bos"] = "bullish"
                result.at[
                    index,
                    "broken_level",
                ] = latest_swing_high
                result.at[
                    index,
                    "break_distance",
                ] = bullish_break_distance
                result.at[
                    index,
                    "required_break_distance",
                ] = required_distance

                bullish_level_broken = True

        if (
            latest_swing_low is not None
            and not bearish_level_broken
        ):
            bearish_break_distance = (
                latest_swing_low - close_price
            )

            if bearish_break_distance >= required_distance:
                result.at[index, "bos"] = "bearish"
                result.at[
                    index,
                    "broken_level",
                ] = latest_swing_low
                result.at[
                    index,
                    "break_distance",
                ] = bearish_break_distance
                result.at[
                    index,
                    "required_break_distance",
                ] = required_distance

                bearish_level_broken = True

        if result.at[index, "swing_high"]:
            latest_swing_high = float(
                result.at[index, "high"]
            )
            bullish_level_broken = False

        if result.at[index, "swing_low"]:
            latest_swing_low = float(
                result.at[index, "low"]
            )
            bearish_level_broken = False

    return result


def detect_change_of_character(
    bos_result: pd.DataFrame,
) -> pd.DataFrame:
    """
    Detect Change of Character from confirmed BOS events.

    Bullish CHoCH:
    A bullish BOS occurs after the most recent BOS direction
    was bearish.

    Bearish CHoCH:
    A bearish BOS occurs after the most recent BOS direction
    was bullish.

    This is an initial direction-change model. It can later be
    enhanced with broader trend context and structural validation.
    """
    required_columns = {"bos"}
    missing_columns = required_columns.difference(
        bos_result.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    result = bos_result.copy()
    result["choch"] = pd.NA

    previous_bos_direction = None

    for index in result.index:
        current_bos = result.at[index, "bos"]

        if pd.isna(current_bos):
            continue

        if (
            previous_bos_direction == "bearish"
            and current_bos == "bullish"
        ):
            result.at[index, "choch"] = "bullish"

        elif (
            previous_bos_direction == "bullish"
            and current_bos == "bearish"
        ):
            result.at[index, "choch"] = "bearish"

        previous_bos_direction = current_bos

    return result