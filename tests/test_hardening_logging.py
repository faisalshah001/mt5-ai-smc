"""
Post-Audit Hardening Phase, Task 3: essential structured logging.

Confirmed audit finding: the codebase had zero usage of Python's
logging module (only three print() calls in app/mt5/connection.py),
contradicting CLAUDE.md's "Logging where appropriate" requirement.

These tests verify that logging *happens* at the required operational
boundaries, and at the correct level/logger, without depending on
exact human-readable message formatting (only that expected
identifying values -- symbols, error text -- appear in the record).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pandas as pd
import pytest

import main
from app.analysis.candle_validation import validate_and_normalize_candles
from app.mt5 import connection, market


# --- MT5 connection lifecycle -----------------------------------------------


def test_connect_mt5_logs_info_on_success(caplog):
    with patch("app.mt5.connection.mt5") as mock_mt5:
        mock_mt5.initialize.return_value = True

        with caplog.at_level(logging.INFO, logger="app.mt5.connection"):
            connection.connect_mt5()

    assert any(
        record.levelno == logging.INFO
        for record in caplog.records
    )


def test_connect_mt5_logs_error_on_failure(caplog):
    with patch("app.mt5.connection.mt5") as mock_mt5:
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = "terminal not found"

        with caplog.at_level(logging.ERROR, logger="app.mt5.connection"):
            with pytest.raises(RuntimeError):
                connection.connect_mt5()

    error_records = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert error_records
    assert "terminal not found" in error_records[0].getMessage()


def test_disconnect_mt5_logs_info(caplog):
    with patch("app.mt5.connection.mt5") as mock_mt5:
        with caplog.at_level(logging.INFO, logger="app.mt5.connection"):
            connection.disconnect_mt5()

        mock_mt5.shutdown.assert_called_once()

    assert any(
        record.levelno == logging.INFO
        for record in caplog.records
    )


# --- Unexpected MT5 data-retrieval failures ---------------------------------


def test_get_candles_logs_error_when_mt5_returns_no_rates(caplog):
    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.TIMEFRAME_H1 = 1
        mock_mt5.symbol_select.return_value = True
        mock_mt5.copy_rates_from_pos.return_value = None
        mock_mt5.last_error.return_value = "no connection"

        with caplog.at_level(logging.ERROR, logger="app.mt5.market"):
            with pytest.raises(RuntimeError):
                market.get_candles("EURUSD", "H1", 100)

    error_records = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert error_records
    assert "EURUSD" in error_records[0].getMessage()


def test_get_candles_logs_warning_on_empty_result(caplog):
    with patch("app.mt5.market.mt5") as mock_mt5:
        mock_mt5.TIMEFRAME_H1 = 1
        mock_mt5.symbol_select.return_value = True
        mock_mt5.copy_rates_from_pos.return_value = []

        with caplog.at_level(logging.WARNING, logger="app.mt5.market"):
            with pytest.raises(ValueError):
                market.get_candles("EURUSD", "H1", 100)

    warning_records = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert warning_records


# --- Validation rejection summaries -----------------------------------------


def test_candle_validation_logs_warning_on_rejection(caplog):
    frame = pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=5, freq="1h", tz="UTC"
            ),
            "open": [1.0, 1.0, 1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0, 1.0, 1.0],
            "low": [1.0, 1.0, float("nan"), 1.0, 1.0],
            "close": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )

    with caplog.at_level(
        logging.WARNING, logger="app.analysis.candle_validation"
    ):
        with pytest.raises(ValueError):
            validate_and_normalize_candles(frame)

    warning_records = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert warning_records
    assert "low" in warning_records[0].getMessage()


def test_candle_validation_no_warning_for_well_formed_input(caplog):
    frame = pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=5, freq="1h", tz="UTC"
            ),
            "open": [1.0, 1.01, 1.02, 1.03, 1.04],
            "high": [1.01, 1.02, 1.03, 1.04, 1.05],
            "low": [0.99, 1.00, 1.01, 1.02, 1.03],
            "close": [1.005, 1.015, 1.025, 1.035, 1.045],
        }
    )

    with caplog.at_level(
        logging.WARNING, logger="app.analysis.candle_validation"
    ):
        validate_and_normalize_candles(frame)

    assert not any(
        record.levelno == logging.WARNING for record in caplog.records
    )


# --- Endpoint-level unexpected exceptions -----------------------------------


def test_candles_endpoint_logs_and_reraises_unexpected_exception(caplog):
    with patch("main.get_candles", side_effect=KeyError("boom")):
        with caplog.at_level(logging.ERROR, logger="main"):
            with pytest.raises(KeyError):
                main.candles_endpoint("EURUSD", "H4", count=100)

    error_records = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert error_records
    # logger.exception() attaches exc_info -- stack-trace visibility
    # confirmed present, not just a bare message.
    assert error_records[0].exc_info is not None


def test_expected_value_error_does_not_trigger_unexpected_exception_log(
    caplog,
):
    # A well-modelled ValueError (e.g. malformed candles) must be
    # translated to HTTP 400 without also being logged as an
    # "unexpected error" -- avoiding duplicate logging of the same
    # exception at multiple layers (it is already logged once, as a
    # warning, at its source in candle_validation.py).
    from fastapi import HTTPException

    bad_frame = pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=5, freq="1h", tz="UTC"
            ),
            "open": [1.0] * 5,
            "high": [1.0] * 5,
            "low": [1.0] * 5,
            "close": [1.0] * 5,
        }
    )
    bad_frame.loc[2, "low"] = float("nan")

    with patch("main.get_candles", return_value=bad_frame):
        with caplog.at_level(logging.ERROR, logger="main"):
            with pytest.raises(HTTPException) as exc_info:
                main.candles_endpoint("EURUSD", "H4", count=100)

    assert exc_info.value.status_code == 400
    assert not any(
        record.levelno == logging.ERROR
        and record.name == "main"
        for record in caplog.records
    )
