from typing import Any, Callable

import pandas as pd


def analyse_multiple_timeframes(
    symbol: str,
    timeframes: list[str],
    candle_loader: Callable[[str, str, int], pd.DataFrame],
    indicator_calculator: Callable[[pd.DataFrame], pd.DataFrame],
    trend_analyser: Callable[[pd.DataFrame], dict[str, Any]],
    count: int = 250,
) -> dict[str, Any]:
    """
    Analyse one trading symbol across multiple timeframes.

    The required functions are passed into this module so it stays
    independent from MT5, indicators, and the individual strategy file.
    """

    clean_symbol = symbol.strip().upper()

    if not timeframes:
        raise ValueError("At least one timeframe is required.")

    results: dict[str, dict[str, Any]] = {}

    bullish_timeframes = 0
    bearish_timeframes = 0
    waiting_timeframes = 0

    for timeframe in timeframes:
        clean_timeframe = timeframe.strip().upper()

        frame = candle_loader(
            clean_symbol,
            clean_timeframe,
            count,
        )

        frame = indicator_calculator(frame)
        analysis = trend_analyser(frame)

        results[clean_timeframe] = analysis

        signal = analysis["signal"]

        if signal == "buy":
            bullish_timeframes += 1
        elif signal == "sell":
            bearish_timeframes += 1
        else:
            waiting_timeframes += 1

    total_timeframes = len(results)

    if bullish_timeframes == total_timeframes:
        overall_signal = "buy"
        alignment = "full bullish alignment"
    elif bearish_timeframes == total_timeframes:
        overall_signal = "sell"
        alignment = "full bearish alignment"
    elif bullish_timeframes > bearish_timeframes:
        overall_signal = "wait"
        alignment = "partial bullish alignment"
    elif bearish_timeframes > bullish_timeframes:
        overall_signal = "wait"
        alignment = "partial bearish alignment"
    else:
        overall_signal = "wait"
        alignment = "mixed alignment"

    agreement_count = max(
        bullish_timeframes,
        bearish_timeframes,
        waiting_timeframes,
    )

    confidence = round(
        agreement_count / total_timeframes * 100,
        2,
    )

    return {
        "symbol": clean_symbol,
        "timeframes": list(results.keys()),
        "overall_signal": overall_signal,
        "alignment": alignment,
        "confidence": confidence,
        "summary": {
            "buy": bullish_timeframes,
            "sell": bearish_timeframes,
            "wait": waiting_timeframes,
        },
        "analysis_by_timeframe": results,
    }