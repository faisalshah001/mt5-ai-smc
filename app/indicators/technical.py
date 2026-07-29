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

    rsi14 = 100 - (100 / (1 + relative_strength))

    # average_loss == 0 (with average_gain > 0) makes the division
    # above NaN via the replace(), even though RSI is well-defined as
    # 100.0 in that case (no losses to average over the smoothing
    # window) -- reachable on real market data during an uninterrupted
    # uptrend, not merely a theoretical edge case.
    zero_loss_only = (average_loss == 0) & (average_gain > 0)
    rsi14 = rsi14.where(~zero_loss_only, 100.0)

    # Mirror case: no gains to average, only losses -> RSI is 0.0. The
    # formula above already yields 0.0 here naturally (relative
    # strength is exactly 0), so this is a defensive no-op that locks
    # the behaviour in explicitly rather than relying on it implicitly.
    zero_gain_only = (average_gain == 0) & (average_loss > 0)
    rsi14 = rsi14.where(~zero_gain_only, 0.0)

    # Both zero (no price movement of either kind, e.g. a flat price
    # series): RSI is genuinely indeterminate (0/0). Preserved as NaN,
    # matching this project's existing convention for that case --
    # unaffected by this fix, which only targets the asymmetric
    # zero-loss/zero-gain cases above.

    result["rsi14"] = rsi14

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