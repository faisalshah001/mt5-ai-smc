import MetaTrader5 as mt5
import pandas as pd

SYMBOL = "EURUSD"
TIMEFRAME = mt5.TIMEFRAME_H4
NUMBER_OF_CANDLES = 100

print("Connecting to MT5...")

if not mt5.initialize():
    print("Failed to connect to MT5")
    print(mt5.last_error())
    quit()

if not mt5.symbol_select(SYMBOL, True):
    print(f"Cannot select symbol: {SYMBOL}")
    mt5.shutdown()
    quit()

rates = mt5.copy_rates_from_pos(
    SYMBOL,
    TIMEFRAME,
    0,
    NUMBER_OF_CANDLES
)

if rates is None:
    print("Failed to retrieve candles")
    print(mt5.last_error())
    mt5.shutdown()
    quit()

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")

print("\nLast 10 Candles:\n")
print(df.tail(10))

df.to_csv("eurusd_h4_candles.csv", index=False)

print("\nCSV file saved successfully!")

mt5.shutdown()
print("MT5 connection closed.")