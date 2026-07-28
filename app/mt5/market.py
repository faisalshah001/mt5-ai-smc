from typing import Final

import MetaTrader5 as mt5
import pandas as pd


TIMEFRAMES: Final[dict[str, int]] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def get_candles(
    symbol: str,
    timeframe: str,
    count: int = 250,
) -> pd.DataFrame:
    """
    Retrieve OHLC candle data from MetaTrader 5.

    Parameters:
    - symbol: Trading symbol, for example EURUSD
    - timeframe: M1, M5, M15, M30, H1, H4, or D1
    - count: Number of candles to retrieve
    """

    clean_symbol = symbol.strip().upper()
    clean_timeframe = timeframe.strip().upper()

    if clean_timeframe not in TIMEFRAMES:
        available = ", ".join(TIMEFRAMES.keys())

        raise ValueError(
            f"Unsupported timeframe '{clean_timeframe}'. "
            f"Available timeframes: {available}"
        )

    if count < 1:
        raise ValueError("Candle count must be greater than zero.")

    if not mt5.symbol_select(clean_symbol, True):
        raise ValueError(
            f"Symbol '{clean_symbol}' could not be selected in MT5."
        )

    rates = mt5.copy_rates_from_pos(
        clean_symbol,
        TIMEFRAMES[clean_timeframe],
        0,
        count,
    )

    if rates is None:
        raise RuntimeError(
            f"Unable to retrieve candles from MT5: {mt5.last_error()}"
        )

    frame = pd.DataFrame(rates)

    if frame.empty:
        raise ValueError(
            f"No candle data returned for "
            f"{clean_symbol} {clean_timeframe}."
        )

    frame["time"] = pd.to_datetime(
        frame["time"],
        unit="s",
        utc=True,
    )

    return frame