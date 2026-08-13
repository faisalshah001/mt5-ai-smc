"""
Tests for the MT5 broker-server-time -> true-UTC correction
(app.mt5.market._detect_broker_utc_offset / get_candles).

Root cause and fix are documented in app/mt5/market.py directly; these
tests cover the offset-detection helper in isolation (valid tick,
missing tick, malformed tick, rounding behaviour) and confirm
get_candles() actually applies the detected offset to the returned
candle frame without changing anything else about it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.mt5 import market


# --- _detect_broker_utc_offset -----------------------------------------


def test_detect_offset_returns_rounded_timedelta_for_a_real_offset():
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now + timedelta(hours=3, seconds=2)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(hours=3)


def test_detect_offset_returns_zero_when_offset_is_genuinely_zero():
    fake_tick = MagicMock()
    fake_tick.time = int(datetime.now(timezone.utc).timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


def test_detect_offset_returns_zero_when_tick_is_none():
    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = None

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


def test_detect_offset_returns_zero_and_does_not_raise_on_malformed_tick():
    fake_tick = MagicMock()
    fake_tick.time = "not-a-number"  # malformed: fromtimestamp() will raise

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


def test_detect_offset_rounds_to_nearest_fifteen_minutes():
    # A raw offset of 2h59m50s (10s of measurement jitter under a real
    # 3h broker offset) must still round to exactly 3h, not a
    # fractional value that would misalign candle boundaries.
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now + timedelta(hours=3) - timedelta(seconds=10)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(hours=3)


# --- stale tick / sanity bound -------------------------------------------


def test_detect_offset_rejects_stale_tick_from_weekend_closure():
    # Simulates the exact failure mode under review: the last tick is
    # from ~54 hours ago (a realistic Friday-close-to-Sunday-reopen
    # weekend gap) behind a genuine +3h broker offset. Naively this
    # computes to roughly (3h - 54h) = -51h -- an obviously impossible
    # "UTC offset" that must never be applied to candle timestamps.
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now + timedelta(hours=3) - timedelta(hours=54)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


def test_detect_offset_rejects_stale_tick_immediately_after_initialize():
    # A cached/last-session tick from days ago, as could be returned
    # immediately after mt5.initialize() before any fresh data has
    # arrived for the symbol.
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now - timedelta(days=3)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


def test_detect_offset_accepts_offset_exactly_at_the_plausible_boundary():
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now + timedelta(hours=14)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(hours=14)


def test_detect_offset_rejects_offset_just_beyond_the_plausible_boundary():
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now + timedelta(hours=14, minutes=30)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(0)


# --- negative offsets (broker behind UTC) --------------------------------


def test_detect_offset_handles_broker_behind_utc():
    true_utc_now = datetime.now(timezone.utc)
    server_time = true_utc_now - timedelta(hours=5)

    fake_tick = MagicMock()
    fake_tick.time = int(server_time.timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = fake_tick

        offset = market._detect_broker_utc_offset("EURUSD")

    assert offset == timedelta(hours=-5)


def test_get_candles_adds_time_for_a_negative_broker_offset():
    # A UTC-5 broker: subtracting a negative offset must ADD 5 hours to
    # the naively-labelled candle time, correcting it forward to true
    # UTC (mirrors the sign-logic concern raised in review).
    import numpy as np

    base_epoch = 1_800_000_000
    rates = np.array(
        [
            _fake_rate(base_epoch, 1.1000),
            _fake_rate(base_epoch + 900, 1.1005),
            _fake_rate(base_epoch + 1800, 1.1010),  # dropped: forming candle
        ],
        dtype=_RATE_DTYPE,
    )

    fake_tick = MagicMock()
    fake_tick.time = int(datetime.now(timezone.utc).timestamp()) - 5 * 3600

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.TIMEFRAME_M15 = 15
        mock_mt5.symbol_select.return_value = True
        mock_mt5.copy_rates_from_pos.return_value = rates
        mock_mt5.symbol_info_tick.return_value = fake_tick

        frame = market.get_candles("EURUSD", "M15", 2)

    naive_label = market.pd.to_datetime(
        [base_epoch, base_epoch + 900], unit="s", utc=True
    )
    expected = naive_label + timedelta(hours=5)

    assert list(frame["time"]) == list(expected)


# --- offset changes between calls (DST / broker reconfiguration) --------


def test_detect_offset_is_recomputed_independently_on_each_call():
    # No caching exists: two consecutive calls with two different tick
    # offsets (simulating a DST transition or broker reconfiguration
    # between them) must each return their own correct, independent
    # result -- no restart or state reset required.
    true_utc_now = datetime.now(timezone.utc)

    tick_at_plus_2 = MagicMock()
    tick_at_plus_2.time = int((true_utc_now + timedelta(hours=2)).timestamp())

    tick_at_plus_3 = MagicMock()
    tick_at_plus_3.time = int((true_utc_now + timedelta(hours=3)).timestamp())

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.symbol_info_tick.return_value = tick_at_plus_2
        first_offset = market._detect_broker_utc_offset("EURUSD")

        mock_mt5.symbol_info_tick.return_value = tick_at_plus_3
        second_offset = market._detect_broker_utc_offset("EURUSD")

    assert first_offset == timedelta(hours=2)
    assert second_offset == timedelta(hours=3)


# --- get_candles() applies the detected offset --------------------------


def _fake_rate(epoch_seconds: int, price: float) -> tuple:
    # Matches MetaTrader5's copy_rates_from_pos record shape closely
    # enough for pandas.DataFrame(rates) to expose the columns
    # get_candles() and its callers actually read.
    return (epoch_seconds, price, price, price, price, 100, 0, 0)


_RATE_DTYPE = [
    ("time", "i8"),
    ("open", "f8"),
    ("high", "f8"),
    ("low", "f8"),
    ("close", "f8"),
    ("tick_volume", "i8"),
    ("spread", "i4"),
    ("real_volume", "i8"),
]


def test_get_candles_applies_detected_offset_to_time_column():
    import numpy as np

    base_epoch = 1_800_000_000  # arbitrary, fixed reference point
    rates = np.array(
        [
            _fake_rate(base_epoch, 1.1000),
            _fake_rate(base_epoch + 900, 1.1005),
            _fake_rate(base_epoch + 1800, 1.1010),  # dropped: forming candle
        ],
        dtype=_RATE_DTYPE,
    )

    fake_tick = MagicMock()
    # Server clock exactly 2 hours ahead of true UTC for this test.
    fake_tick.time = int(datetime.now(timezone.utc).timestamp()) + 7200

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.TIMEFRAME_M15 = 15
        mock_mt5.symbol_select.return_value = True
        mock_mt5.copy_rates_from_pos.return_value = rates
        mock_mt5.symbol_info_tick.return_value = fake_tick

        frame = market.get_candles("EURUSD", "M15", 2)

    # Unaffected by the fix: row count, ordering, OHLC values.
    assert len(frame) == 2
    assert list(frame["close"]) == [1.1000, 1.1005]

    # Affected, as intended: the time column is shifted back by the
    # detected 2-hour offset relative to the naive utc=True labelling.
    naive_label = market.pd.to_datetime(
        [base_epoch, base_epoch + 900], unit="s", utc=True
    )
    expected = naive_label - timedelta(hours=2)

    assert list(frame["time"]) == list(expected)


def test_get_candles_leaves_time_column_unchanged_when_offset_detection_fails():
    import numpy as np

    base_epoch = 1_800_000_000
    rates = np.array(
        [
            _fake_rate(base_epoch, 1.1000),
            _fake_rate(base_epoch + 900, 1.1005),
        ],
        dtype=_RATE_DTYPE,
    )

    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.TIMEFRAME_M15 = 15
        mock_mt5.symbol_select.return_value = True
        mock_mt5.copy_rates_from_pos.return_value = rates
        # No forming candle to drop from a 2-row fetch would empty the
        # frame, so request one extra row via count, matching the
        # function's own "count + 1" contract.
        mock_mt5.symbol_info_tick.return_value = None  # detection fails closed

        frame = market.get_candles("EURUSD", "M15", 1)

    expected = market.pd.to_datetime([base_epoch], unit="s", utc=True)

    assert list(frame["time"]) == list(expected)
