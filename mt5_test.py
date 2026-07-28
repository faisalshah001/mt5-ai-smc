import MetaTrader5 as mt5


def main() -> None:
    print("Connecting to MetaTrader 5...")

    if not mt5.initialize():
        print("Connection failed.")
        print("MT5 error:", mt5.last_error())
        return

    try:
        account = mt5.account_info()

        if account is None:
            print("Connected to MT5, but account information was unavailable.")
            print("MT5 error:", mt5.last_error())
            return

        print("\nConnected successfully.")
        print("Login:", account.login)
        print("Server:", account.server)
        print("Account currency:", account.currency)
        print("Balance:", account.balance)
        print("Equity:", account.equity)
        print("Trade allowed:", account.trade_allowed)

    finally:
        mt5.shutdown()
        print("\nMT5 connection closed.")


if __name__ == "__main__":
    main()