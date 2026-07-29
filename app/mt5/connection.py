import logging

import MetaTrader5 as mt5

from app.mt5.executor import run_mt5


logger = logging.getLogger(__name__)


def connect_mt5() -> None:
    """
    Connect Python to the MetaTrader 5 terminal.
    """

    logger.info("Connecting API to MetaTrader 5...")

    if not run_mt5(mt5.initialize):
        error = run_mt5(mt5.last_error)

        logger.error("MT5 initialization failed: %s", error)

        raise RuntimeError(
            f"MT5 initialization failed: {error}"
        )

    logger.info("MT5 connected successfully.")


def disconnect_mt5() -> None:
    """
    Close the MetaTrader 5 connection.
    """

    run_mt5(mt5.shutdown)

    logger.info("MT5 connection closed.")


def is_mt5_connected() -> bool:
    """
    Check whether the MT5 terminal is connected.
    """

    terminal = run_mt5(mt5.terminal_info)

    return terminal is not None