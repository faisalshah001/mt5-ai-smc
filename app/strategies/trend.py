from typing import Any

import pandas as pd


def analyse_trend(frame: pd.DataFrame) -> dict[str, Any]:
    """
    Analyse the latest market indicators and return a trend signal,
    confidence score, and supporting reasons.
    """

    required_columns = {
        "close",
        "ema20",
        "ema50",
        "ema200",
        "rsi14",
        "macd",
        "macd_signal",
    }

    missing_columns = required_columns.difference(frame.columns)

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))

        raise ValueError(
            f"Missing required indicator columns: {missing}"
        )

    if frame.empty:
        raise ValueError("The candle DataFrame is empty.")

    latest = frame.iloc[-1]

    close = float(latest["close"])
    ema20 = float(latest["ema20"])
    ema50 = float(latest["ema50"])
    ema200 = float(latest["ema200"])
    rsi14 = float(latest["rsi14"])
    macd = float(latest["macd"])
    macd_signal = float(latest["macd_signal"])

    bullish_score = 0
    bearish_score = 0

    bullish_reasons: list[str] = []
    bearish_reasons: list[str] = []
    neutral_reasons: list[str] = []

    if close > ema20:
        bullish_score += 1
        bullish_reasons.append(
            "The current price is above EMA20."
        )
    elif close < ema20:
        bearish_score += 1
        bearish_reasons.append(
            "The current price is below EMA20."
        )
    else:
        neutral_reasons.append(
            "The current price is equal to EMA20."
        )

    if ema20 > ema50:
        bullish_score += 1
        bullish_reasons.append(
            "EMA20 is above EMA50."
        )
    elif ema20 < ema50:
        bearish_score += 1
        bearish_reasons.append(
            "EMA20 is below EMA50."
        )
    else:
        neutral_reasons.append(
            "EMA20 is equal to EMA50."
        )

    if ema50 > ema200:
        bullish_score += 1
        bullish_reasons.append(
            "EMA50 is above EMA200."
        )
    elif ema50 < ema200:
        bearish_score += 1
        bearish_reasons.append(
            "EMA50 is below EMA200."
        )
    else:
        neutral_reasons.append(
            "EMA50 is equal to EMA200."
        )

    if macd > macd_signal:
        bullish_score += 1
        bullish_reasons.append(
            "MACD is above its signal line."
        )
    elif macd < macd_signal:
        bearish_score += 1
        bearish_reasons.append(
            "MACD is below its signal line."
        )
    else:
        neutral_reasons.append(
            "MACD is equal to its signal line."
        )

    if 50 < rsi14 < 70:
        bullish_score += 1
        bullish_reasons.append(
            "RSI is between 50 and 70, supporting bullish momentum."
        )
    elif 30 < rsi14 < 50:
        bearish_score += 1
        bearish_reasons.append(
            "RSI is between 30 and 50, supporting bearish momentum."
        )
    elif rsi14 >= 70:
        neutral_reasons.append(
            "RSI is 70 or higher, indicating possible overbought conditions."
        )
    elif rsi14 <= 30:
        neutral_reasons.append(
            "RSI is 30 or lower, indicating possible oversold conditions."
        )
    else:
        neutral_reasons.append(
            "RSI is neutral at approximately 50."
        )

    if close > ema20 > ema50 > ema200:
        trend = "bullish"
    elif close < ema20 < ema50 < ema200:
        trend = "bearish"
    else:
        trend = "sideways"

    if trend == "bullish" and bullish_score >= 4:
        signal = "buy"
        confidence = bullish_score * 20
        signal_reasons = bullish_reasons
    elif trend == "bearish" and bearish_score >= 4:
        signal = "sell"
        confidence = bearish_score * 20
        signal_reasons = bearish_reasons
    else:
        signal = "wait"
        confidence = max(bullish_score, bearish_score) * 20
        signal_reasons = neutral_reasons

        if bullish_score > bearish_score:
            signal_reasons = bullish_reasons + neutral_reasons
        elif bearish_score > bullish_score:
            signal_reasons = bearish_reasons + neutral_reasons
        else:
            signal_reasons = (
                bullish_reasons
                + bearish_reasons
                + neutral_reasons
            )

    return {
        "trend": trend,
        "signal": signal,
        "confidence": confidence,
        "scores": {
            "bullish": bullish_score,
            "bearish": bearish_score,
        },
        "reasons": signal_reasons,
        "close": close,
        "ema_alignment": {
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
        },
        "momentum": {
            "rsi14": rsi14,
            "macd": macd,
            "macd_signal": macd_signal,
        },
    }