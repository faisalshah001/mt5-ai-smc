from __future__ import annotations

import logging

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

REQUIRED_CANDLE_COLUMNS = {
    "time",
    "open",
    "high",
    "low",
    "close",
}

REQUIRED_NUMERIC_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
}


def validate_and_normalize_candles(
    candles: pd.DataFrame,
) -> pd.DataFrame:
    """
    Validate and normalise raw OHLC candle data.

    This is the single, pipeline-independent candle-data-hygiene
    entry point for this codebase (SMC_SPECIFICATION.md, §3,
    Decision A). It must be called immediately after candle retrieval
    and before any indicator or structure computation, by every
    candle-consuming call site.

    Normalisation performed:
    - UTC time coercion (numeric epoch-seconds or datetime-like
      values parsed into tz-aware UTC datetimes).
    - Numeric coercion for the required OHLC fields, including
      numeric-looking strings.
    - Stable chronological sort by time.
    - RangeIndex reset (0..n-1) after sorting.
    - A "volume" column, if present, is coerced to numeric the same
      way as the required OHLC fields (invalid values become NaN, not
      rejected -- volume is not a required field).
    - Every other extra column (e.g. tick_volume, spread, real_volume)
      is left completely untouched.

    Rejected as ValueError (no safe automatic resolution exists):
    - Missing required columns.
    - Any required numeric field value that is not finite (NaN or
      +/-infinity).
    - Unparseable timestamps.
    - Duplicate timestamps.
    - Invalid OHLC relationships.
    - Empty DataFrames.

    Out of scope: minimum candle-history checks remain engine-specific
    (e.g. detect_swing_points' left_bars + right_bars + 1
    requirement) and are not enforced here.
    """

    if not isinstance(candles, pd.DataFrame):
        raise TypeError("candles must be a pandas DataFrame.")

    if candles.empty:
        raise ValueError("candles cannot be empty.")

    missing_columns = REQUIRED_CANDLE_COLUMNS.difference(
        candles.columns
    )

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))

        logger.warning(
            "Candle validation rejected input: missing columns %s.",
            missing,
        )

        raise ValueError(
            f"Missing required candle columns: {missing}"
        )

    result = candles.copy()

    # MT5 returns Unix timestamps in seconds. Preserve support for
    # already-parsed datetime values and datetime strings as well.
    if pd.api.types.is_numeric_dtype(result["time"]):
        result["time"] = pd.to_datetime(
            result["time"],
            unit="s",
            errors="coerce",
            utc=True,
        )
    else:
        result["time"] = pd.to_datetime(
            result["time"],
            errors="coerce",
            utc=True,
        )

    invalid_time_mask = result["time"].isna()

    if invalid_time_mask.any():
        invalid_rows = result.index[
            invalid_time_mask
        ].tolist()

        logger.warning(
            "Candle validation rejected input: %d row(s) with "
            "invalid time values.",
            len(invalid_rows),
        )

        raise ValueError(
            "The time column contains invalid datetime values "
            f"at indexes {invalid_rows[:10]}."
        )

    for column_name in REQUIRED_NUMERIC_COLUMNS:
        result[column_name] = pd.to_numeric(
            result[column_name],
            errors="coerce",
        )

        invalid_numeric_mask = (
            result[column_name].isna()
            | np.isinf(result[column_name])
        )

        if invalid_numeric_mask.any():
            invalid_rows = result.index[
                invalid_numeric_mask
            ].tolist()

            logger.warning(
                "Candle validation rejected input: %d row(s) with "
                "invalid %r values.",
                len(invalid_rows),
                column_name,
            )

            raise ValueError(
                f"The {column_name!r} column contains invalid "
                f"numeric values at indexes {invalid_rows[:10]}."
            )

    if "volume" in result.columns:
        result["volume"] = pd.to_numeric(
            result["volume"],
            errors="coerce",
        )

    result = (
        result.sort_values("time", kind="stable")
        .reset_index(drop=True)
    )

    duplicate_mask = result["time"].duplicated(keep=False)

    if duplicate_mask.any():
        duplicate_times = (
            result.loc[duplicate_mask, "time"]
            .astype(str)
            .head(10)
            .tolist()
        )

        logger.warning(
            "Candle validation rejected input: %d row(s) involved "
            "in duplicate timestamps.",
            int(duplicate_mask.sum()),
        )

        raise ValueError(
            "candles contains duplicate timestamps: "
            f"{duplicate_times}."
        )

    invalid_high_mask = (
        (result["high"] < result["open"])
        | (result["high"] < result["close"])
        | (result["high"] < result["low"])
    )

    if invalid_high_mask.any():
        invalid_rows = result.index[
            invalid_high_mask
        ].tolist()

        logger.warning(
            "Candle validation rejected input: %d row(s) with an "
            "invalid high/open/close/low relationship.",
            len(invalid_rows),
        )

        raise ValueError(
            "Candle high must be greater than or equal to "
            "open, close and low. Invalid rows: "
            f"{invalid_rows[:10]}."
        )

    invalid_low_mask = (
        (result["low"] > result["open"])
        | (result["low"] > result["close"])
        | (result["low"] > result["high"])
    )

    if invalid_low_mask.any():
        invalid_rows = result.index[
            invalid_low_mask
        ].tolist()

        logger.warning(
            "Candle validation rejected input: %d row(s) with an "
            "invalid low/open/close/high relationship.",
            len(invalid_rows),
        )

        raise ValueError(
            "Candle low must be less than or equal to "
            "open, close and high. Invalid rows: "
            f"{invalid_rows[:10]}."
        )

    return result
