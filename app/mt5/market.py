import logging
from datetime import datetime, timedelta, timezone
from typing import Final

import MetaTrader5 as mt5
import pandas as pd

from app.mt5.executor import run_mt5


logger = logging.getLogger(__name__)

# Real-world UTC offsets are always whole 15-minute multiples -- used to
# round out API round-trip jitter in _detect_broker_utc_offset() without
# ever hard-coding a fixed offset (see that function's docstring).
_OFFSET_ROUNDING_SECONDS = 15 * 60

# Real-world UTC offsets range from UTC-12 to UTC+14 -- a symmetric
# +/-14h bound is a deliberately simple, generous superset of that full
# range. Any computed offset outside it is rejected as implausible; see
# _detect_broker_utc_offset's docstring for why this is the primary
# defence against a stale tick, not just a cosmetic sanity check.
_MAX_PLAUSIBLE_OFFSET = timedelta(hours=14)


def _detect_broker_utc_offset(symbol: str) -> timedelta:
    """
    Detect how far this MT5 broker's server clock currently sits from
    true UTC, so candle timestamps (which MT5 reports in server-local
    time, not UTC) can be corrected before anything else consumes them.

    Computed fresh on every call by comparing the broker's current tick
    time against this machine's own UTC clock -- deliberately not a
    hard-coded constant, since the true offset can change with DST or a
    broker reconfiguration. Rounded to the nearest 15 minutes (every
    real-world UTC offset is a 15-minute multiple), which absorbs the
    sub-second latency between reading the two clocks.

    Stale-tick defence (deliberate, not incidental)
    -------------------------------------------------
    mt5.symbol_info_tick(symbol).time is the timestamp of the *last
    received tick* -- not a live server clock reading. During a weekend
    closure, a trading halt, or immediately after mt5.initialize()
    before the terminal has received fresh data for the symbol, that
    tick can be hours or days old. Naively treating a stale tick as
    "now" would compute (real offset - staleness), which for a
    multi-hour/day-stale tick lands far outside any real UTC offset --
    e.g. a 48-hour-stale tick behind a genuine +3h broker offset
    computes to roughly -45h. _MAX_PLAUSIBLE_OFFSET rejects exactly
    that: any candidate offset outside the real-world +/-14h range is
    discarded in favour of no correction at all, which is always safer
    than an arbitrary multi-hour/day shift applied to every candle.

    Returns timedelta(0) -- i.e. no correction, identical to this
    function not existing -- if the current tick is unavailable, the
    computed offset is implausible, or anything about this detection
    fails for any reason. This is a deliberate fail-closed default: a
    problem detecting the offset must never block or alter candle
    retrieval itself.
    """

    try:
        tick = run_mt5(mt5.symbol_info_tick, symbol)

        if tick is None:
            return timedelta(0)

        server_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)
        true_utc_now = datetime.now(timezone.utc)

        raw_offset_seconds = (server_time - true_utc_now).total_seconds()

        rounded_seconds = (
            round(raw_offset_seconds / _OFFSET_ROUNDING_SECONDS)
            * _OFFSET_ROUNDING_SECONDS
        )
        candidate_offset = timedelta(seconds=rounded_seconds)

        if abs(candidate_offset) > _MAX_PLAUSIBLE_OFFSET:
            logger.warning(
                "Rejecting implausible broker/UTC offset for %s: %s "
                "(tick time %s vs UTC now %s). Likely a stale tick "
                "(market closed/halted, or no fresh data yet since "
                "connecting) rather than a real timezone offset -- "
                "proceeding with no offset correction.",
                symbol,
                candidate_offset,
                server_time.isoformat(),
                true_utc_now.isoformat(),
            )

            return timedelta(0)

        return candidate_offset

    except Exception:
        logger.warning(
            "Could not detect broker/UTC clock offset for %s; "
            "proceeding with no offset correction.",
            symbol,
            exc_info=True,
        )

        return timedelta(0)


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
    - count: Number of closed candles to retrieve

    Only fully closed candles are ever returned. MetaTrader5's
    mt5.copy_rates_from_pos(..., start_pos=0, ...) documents position 0
    as the current, still-forming bar -- empirically confirmed against
    a live terminal (its OHLC/tick_volume kept mutating across repeated
    fetches of the same bar timestamp). One extra bar is therefore
    requested and the most recent (still-forming) row is always dropped
    before returning, so the analysis engine never receives a candle
    whose values can still change.
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
        count + 1,
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

    # Drop the current, still-forming bar (see docstring) -- it is
    # always the most recent row, regardless of how many rows MT5
    # actually returned.
    frame = frame.iloc[:-1].reset_index(drop=True)

    if frame.empty:
        logger.warning(
            "MT5 returned only the current, still-forming candle for "
            "%s %s; no closed candle data is available yet.",
            clean_symbol,
            clean_timeframe,
        )

        raise ValueError(
            f"No closed candle data available for "
            f"{clean_symbol} {clean_timeframe}."
        )

    frame["time"] = pd.to_datetime(
        frame["time"],
        unit="s",
        utc=True,
    )

    # MT5 reports candle time in the broker server's local clock, not
    # true UTC (confirmed live: this account's server currently runs
    # ~3 hours ahead of UTC) -- the line above only labels the raw
    # value as UTC without converting it. Correct that here, in the
    # one place every candle-time value in this codebase originates
    # from, so every downstream consumer (structure/liquidity/Order
    # Block timestamps, strategy evidence, forward-test log fields)
    # inherits a true-UTC value automatically.
    broker_offset = _detect_broker_utc_offset(clean_symbol)

    if broker_offset:
        frame["time"] = frame["time"] - broker_offset

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