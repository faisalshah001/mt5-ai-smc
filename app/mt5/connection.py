import MetaTrader5 as mt5


def connect_mt5() -> None:
    """
    Connect Python to the MetaTrader 5 terminal.
    """

    print("Connecting API to MetaTrader 5...")

    if not mt5.initialize():
        error = mt5.last_error()

        raise RuntimeError(
            f"MT5 initialization failed: {error}"
        )

    print("MT5 connected successfully.")


def disconnect_mt5() -> None:
    """
    Close the MetaTrader 5 connection.
    """

    mt5.shutdown()

    print("MT5 connection closed.")


def is_mt5_connected() -> bool:
    """
    Check whether the MT5 terminal is connected.
    """

    terminal = mt5.terminal_info()

    return terminal is not None