import logging
from typing import Final

import MetaTrader5 as mt5
import pandas as pd

from app.mt5.executor import run_mt5


logger = logging.getLogger(__name__)

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

    if not run_mt5(mt5.symbol_select, clean_symbol, True):
        raise ValueError(
            f"Symbol '{clean_symbol}' could not be selected in MT5."
        )

    rates = run_mt5(
        mt5.copy_rates_from_pos,
        clean_symbol,
        TIMEFRAMES[clean_timeframe],
        0,
        count,
    )

    if rates is None:
        error = run_mt5(mt5.last_error)

        logger.error(
            "MT5 candle retrieval failed for %s %s: %s",
            clean_symbol,
            clean_timeframe,
            error,
        )

        raise RuntimeError(
            f"Unable to retrieve candles from MT5: {error}"
        )

    frame = pd.DataFrame(rates)

    if frame.empty:
        logger.warning(
            "MT5 returned no candle data for %s %s.",
            clean_symbol,
            clean_timeframe,
        )

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


_ACCOUNT_TRADE_MODE_NAMES: Final[dict[int, str]] = {
    mt5.ACCOUNT_TRADE_MODE_DEMO: "demo",
    mt5.ACCOUNT_TRADE_MODE_CONTEST: "contest",
    mt5.ACCOUNT_TRADE_MODE_REAL: "real",
}


def get_account_snapshot() -> dict[str, object]:
    """
    Retrieve a minimal account snapshot for risk sizing and
    account-mode gating (balance, equity, currency, and whether the
    account is demo/contest/real).
    """

    account = run_mt5(mt5.account_info)

    if account is None:
        error = run_mt5(mt5.last_error)

        logger.error("MT5 account_info() failed: %s", error)

        raise RuntimeError(
            f"Unable to read account: {error}"
        )

    return {
        "balance": account.balance,
        "equity": account.equity,
        "currency": account.currency,
        "trade_mode": _ACCOUNT_TRADE_MODE_NAMES.get(
            account.trade_mode,
            "unknown",
        ),
    }


def get_open_position_count(symbol: str) -> int:
    """
    Count currently open MT5 positions for one symbol.
    """

    clean_symbol = symbol.strip().upper()

    positions = run_mt5(
        mt5.positions_get,
        symbol=clean_symbol,
    )

    if positions is None:
        return 0

    return len(positions)


def get_symbol_trade_specs(symbol: str) -> dict[str, float]:
    """
    Retrieve the broker's trade specification for one symbol.

    Returns contract size, tick size/value, and volume constraints
    required for position-size calculation
    (app.risk.calculator.calculate_position_size).
    """

    clean_symbol = symbol.strip().upper()

    if not run_mt5(mt5.symbol_select, clean_symbol, True):
        raise ValueError(
            f"Symbol '{clean_symbol}' could not be selected in MT5."
        )

    info = run_mt5(mt5.symbol_info, clean_symbol)

    if info is None:
        error = run_mt5(mt5.last_error)

        logger.error(
            "MT5 symbol_info() failed for %s: %s",
            clean_symbol,
            error,
        )

        raise RuntimeError(
            f"Unable to read symbol specification for "
            f"{clean_symbol}: {error}"
        )

    return {
        "contract_size": info.trade_contract_size,
        "tick_size": info.trade_tick_size,
        "tick_value": info.trade_tick_value,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
    }