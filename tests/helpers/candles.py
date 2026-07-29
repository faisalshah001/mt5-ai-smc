"""
Deterministic candle-fixture builders for baseline/regression tests.

Nothing here is random. Every function produces byte-identical output
for identical input, on every run, per CLAUDE.md's determinism mandate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_zigzag_candles(
    waypoints: list[float],
    *,
    candles_per_leg: int = 8,
    start_time: str = "2024-01-01T00:00:00Z",
    freq: str = "1h",
    epsilon: float = 0.00005,
    prominence: float = 0.0005,
) -> pd.DataFrame:
    """
    Build a deterministic candle series that visits each waypoint price
    in order, with a strictly monotonic close-price path between
    consecutive waypoints.

    Each waypoint is guaranteed to be a strict local high or low of
    both ``high`` and ``low`` over any window narrower than
    ``candles_per_leg`` candles on either side, because waypoint
    candles receive a larger wick ("prominence") than the intermediate
    candles on their leg ("epsilon") — this prevents a waypoint's high
    from tying with the immediately adjacent reversal candle's high
    (and symmetrically for lows), which a naive constant-wick design
    would otherwise produce at every reversal point.

    Parameters
    ----------
    waypoints:
        Ordered list of prices the close path must pass through.
        Consecutive values must differ (no zero-length legs).
    candles_per_leg:
        Number of candles between two consecutive waypoints. Must be
        larger than any ``left_bars``/``right_bars`` value under test,
        so a waypoint's swing window never crosses into a neighboring
        leg's territory.
    """

    if len(waypoints) < 2:
        raise ValueError("waypoints must contain at least two prices.")

    for left, right in zip(waypoints, waypoints[1:]):
        if left == right:
            raise ValueError("Consecutive waypoints must differ.")

    closes: list[float] = [float(waypoints[0])]
    is_waypoint: list[bool] = [True]

    for leg_start, leg_end in zip(waypoints, waypoints[1:]):
        leg_values = np.linspace(
            leg_start,
            leg_end,
            candles_per_leg + 1,
        )[1:]

        closes.extend(float(value) for value in leg_values)
        is_waypoint.extend(
            [False] * (candles_per_leg - 1) + [True]
        )

    opens = [closes[0], *closes[:-1]]

    highs = []
    lows = []

    for open_price, close_price, waypoint in zip(
        opens,
        closes,
        is_waypoint,
    ):
        bump = prominence if waypoint else epsilon

        highs.append(max(open_price, close_price) + bump)
        lows.append(min(open_price, close_price) - bump)

    times = pd.date_range(
        start=start_time,
        periods=len(closes),
        freq=freq,
        tz="UTC",
    )

    return pd.DataFrame(
        {
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def load_eurusd_h4_fixture() -> pd.DataFrame:
    """
    Load the committed real-market H4 EURUSD candle fixture.

    This is a fixed, immutable snapshot used only for golden-file
    regression tests — it is never re-fetched from MT5, so its output
    is as deterministic as any synthetic fixture.
    """

    from pathlib import Path

    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "eurusd_h4_candles.csv"
    )

    frame = pd.read_csv(fixture_path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)

    return frame


def reflect_candles(
    candles: pd.DataFrame,
    center: float,
) -> pd.DataFrame:
    """
    Mirror a candle series around `center`, turning a bullish fixture
    into an equivalent bearish one (or vice versa).

    True Range (high - low) is preserved exactly under this
    reflection, so ATR-derived thresholds behave identically to the
    original series -- only the direction of every comparison flips,
    which is why a verified bullish sequence's mirror image is a
    verified bearish sequence, with no separate manual construction
    or re-verification required.
    """

    reflected = candles.copy()

    reflected["open"] = 2 * center - candles["open"]
    reflected["close"] = 2 * center - candles["close"]
    reflected["high"] = 2 * center - candles["low"]
    reflected["low"] = 2 * center - candles["high"]

    return reflected


def build_ict_bias_candles(*, freq: str = "4h") -> pd.DataFrame:
    """
    Deterministic bullish H4/H1-style candle series producing a
    confirmed bullish `external_trend` from the canonical engine's
    default swing parameters (left_bars=right_bars=3).

    Used for the frozen EURUSD manual-signal strategy's H4 bias / H1
    confirmation steps. Reflect with `reflect_candles` for the bearish
    mirror.
    """

    return build_zigzag_candles(
        waypoints=[1.0900, 1.0850, 1.0950, 1.0900, 1.1000, 1.0950, 1.1050],
        candles_per_leg=8,
        freq=freq,
    )


def build_ict_m15_sequence_candles(*, freq: str = "15min") -> pd.DataFrame:
    """
    Deterministic bullish M15 candle series exercising the full
    frozen entry sequence under swing_options={"left_bars": 1,
    "right_bars": 1}:

    - an EQL (sell-side liquidity) pool forms from two near-equal
      classified swing lows,
    - a later candle sweeps it (wicks below, closes back above),
    - price rallies through a bearish-then-weak-bodied MSS candle
      (deliberately too weak-bodied to itself spawn an Order Block),
    - a further pullback (HL) and a strong-bodied breakout (HH)
      confirm a bullish CHoCH,
    - the CHoCH's own displacement creates a confirmed bullish Order
      Block anchored on the pullback candle,
    - a final pullback candle retraces into (mitigates) that Order
      Block.

    Verified against app.analysis.analysis_engine.analyze_market
    directly (not merely asserted) before being committed here.
    Reflect with `reflect_candles` for the bearish mirror.
    """

    base = build_zigzag_candles(
        waypoints=[
            1.09700,
            1.10200,
            1.09900,
            1.10150,
            1.09600,
            1.10050,
            1.09620,
        ],
        candles_per_leg=2,
        freq=freq,
    )

    extra_rows = [
        (1.09620, 1.09720, 1.09600, 1.09700),  # mild transit
        (1.09700, 1.09710, 1.09540, 1.09690),  # liquidity sweep
        (1.09690, 1.09870, 1.09685, 1.09860),  # transit up
        (1.10000, 1.10800, 1.09980, 1.10250),  # MSS (weak body)
        (1.10600, 1.10850, 1.09950, 1.10300),  # pullback HL (OB source candle)
        (1.10300, 1.11200, 1.10290, 1.11000),  # HH confirms CHoCH
        (1.11000, 1.11010, 1.10000, 1.10050),  # retracement into OB
        (1.10050, 1.10600, 1.10040, 1.10550),  # continuation
    ]

    extra_times = pd.date_range(
        base["time"].iloc[-1] + pd.Timedelta(freq),
        periods=len(extra_rows),
        freq=freq,
    )

    extra = pd.DataFrame(
        extra_rows,
        columns=["open", "high", "low", "close"],
    )
    extra.insert(0, "time", extra_times)

    return pd.concat([base, extra], ignore_index=True)


def build_ict_m5_sequence_candles(*, freq: str = "5min") -> pd.DataFrame:
    """
    Deterministic bullish M5 candle series exercising only the M5
    execution-confirmation step under swing_options={"left_bars": 1,
    "right_bars": 1}: a local liquidity sweep followed by a confirmed
    bullish CHoCH, both within the last 5 candles.

    Verified against app.analysis.analysis_engine.analyze_market
    directly before being committed here. Reflect with
    `reflect_candles` for the bearish mirror.
    """

    base = build_zigzag_candles(
        waypoints=[
            1.09700,
            1.10200,
            1.09900,
            1.10150,
            1.09600,
            1.10050,
            1.09620,
        ],
        candles_per_leg=2,
        freq=freq,
    )

    extra_rows = [
        (1.09620, 1.09720, 1.09600, 1.09700),  # mild transit
        (1.09700, 1.09710, 1.09540, 1.09690),  # liquidity sweep
        (1.10000, 1.10800, 1.09980, 1.10250),  # MSS
        (1.10600, 1.10850, 1.09950, 1.10300),  # pullback HL
        (1.10300, 1.11200, 1.10290, 1.11000),  # HH confirms CHoCH
        (1.11000, 1.11010, 1.10990, 1.11005),  # right-neighbour filler
    ]

    extra_times = pd.date_range(
        base["time"].iloc[-1] + pd.Timedelta(freq),
        periods=len(extra_rows),
        freq=freq,
    )

    extra = pd.DataFrame(
        extra_rows,
        columns=["open", "high", "low", "close"],
    )
    extra.insert(0, "time", extra_times)

    return pd.concat([base, extra], ignore_index=True)
