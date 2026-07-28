import pandas as pd


def calculate_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate technical indicators for an OHLC candle DataFrame.

    Required columns:
    - high
    - low
    - close
    """

    required_columns = {"high", "low", "close"}
    missing_columns = required_columns.difference(frame.columns)

    if missing_columns:
        raise ValueError(
            f"Missing required candle columns: {sorted(missing_columns)}"
        )

    result = frame.copy()

    # Exponential Moving Averages
    result["ema20"] = result["close"].ewm(
        span=20,
        adjust=False,
    ).mean()

    result["ema50"] = result["close"].ewm(
        span=50,
        adjust=False,
    ).mean()

    result["ema200"] = result["close"].ewm(
        span=200,
        adjust=False,
    ).mean()

    # RSI 14 using Wilder-style smoothing
    price_change = result["close"].diff()

    gains = price_change.clip(lower=0)
    losses = -price_change.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / 14,
        adjust=False,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / 14,
        adjust=False,
    ).mean()

    relative_strength = average_gain / average_loss.replace(
        0,
        float("nan"),
    )

    result["rsi14"] = 100 - (
        100 / (1 + relative_strength)
    )

    # MACD
    ema12 = result["close"].ewm(
        span=12,
        adjust=False,
    ).mean()

    ema26 = result["close"].ewm(
        span=26,
        adjust=False,
    ).mean()

    result["macd"] = ema12 - ema26

    result["macd_signal"] = result["macd"].ewm(
        span=9,
        adjust=False,
    ).mean()

    result["macd_histogram"] = (
        result["macd"] - result["macd_signal"]
    )

    # ATR 14
    previous_close = result["close"].shift(1)

    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result["atr14"] = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
    ).mean()

    return result