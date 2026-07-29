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
