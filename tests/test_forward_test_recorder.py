"""
Tests for scripts/forward_test_recorder.py (Demo Forward-Testing
Phase 1).

Only the pure, network-free parts of the recorder are exercised here
(record building, append-only writing, interval validation, strategy
-version resolution, CLI defaults) -- no live server and no MT5
terminal are required, matching this project's existing
dependency-injection-friendly testing style. The recorder's own HTTP
call (call_smc_signal_endpoint) is a thin, standard-library-only
wrapper with no branching logic of its own beyond exception mapping,
and is intentionally not exercised against a live server here (same
reason examples/ scripts are excluded from the automated suite).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.forward_test_recorder import (
    MINIMUM_INTERVAL_SECONDS,
    RECORD_FIELDS_FROM_RESPONSE,
    _parse_args,
    _validate_interval,
    append_record,
    build_record,
    get_strategy_version,
)


RECORDER_SOURCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "forward_test_recorder.py"
)


# ---------------------------------------------------------------
# get_strategy_version
# ---------------------------------------------------------------


class TestGetStrategyVersion:
    def test_returns_real_git_commit_hash(self):
        expected = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=RECORDER_SOURCE_PATH.parent.parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        assert get_strategy_version() == expected
        assert len(expected) == 40

    def test_returns_unknown_when_git_executable_missing(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert get_strategy_version() == "unknown"

    def test_returns_unknown_when_git_command_fails(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert get_strategy_version() == "unknown"

    def test_returns_unknown_when_git_times_out(self, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert get_strategy_version() == "unknown"


# ---------------------------------------------------------------
# build_record
# ---------------------------------------------------------------


class TestBuildRecord:
    def test_success_response_copies_every_field_verbatim(self):
        response_json = {
            "status": "SIGNAL_PENDING_APPROVAL",
            "symbol": "EURUSD",
            "direction": "BUY",
            "entry": 1.10505,
            "stop_loss": 1.0995,
            "take_profit": 1.1461,
            "risk_percent": 0.5,
            "risk_reward": 2.0,
            "position_size": 0.03,
            "confidence": 100,
            "evidence": {"h4_bias": {"passed": True}},
            "rejection_reasons": [],
        }

        record = build_record(
            timestamp_utc="2026-08-01T09:15:03.412871+00:00",
            symbol="EURUSD",
            strategy_version="deadbeef",
            http_status_code=200,
            response_json=response_json,
            api_error=None,
        )

        assert record["timestamp_utc"] == "2026-08-01T09:15:03.412871+00:00"
        assert record["symbol"] == "EURUSD"
        assert record["strategy_version"] == "deadbeef"
        assert record["http_status_code"] == 200
        assert record["api_error"] is None

        for field in RECORD_FIELDS_FROM_RESPONSE:
            assert record[field] == response_json[field]

    def test_no_setup_response_preserves_rejection_reasons(self):
        response_json = {
            "status": "NO_SETUP",
            "direction": None,
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "risk_percent": 0.5,
            "risk_reward": None,
            "position_size": None,
            "confidence": 0,
            "evidence": {"h4_bias": {"passed": True}, "h1_confirmation": {}},
            "rejection_reasons": ["H1 does not confirm the H4 directional bias."],
        }

        record = build_record(
            timestamp_utc="2026-08-01T09:20:04+00:00",
            symbol="EURUSD",
            strategy_version="deadbeef",
            http_status_code=200,
            response_json=response_json,
            api_error=None,
        )

        assert record["status"] == "NO_SETUP"
        assert record["rejection_reasons"] == [
            "H1 does not confirm the H4 directional bias."
        ]
        assert record["evidence"]["h1_confirmation"] == {}

    def test_api_error_sets_every_trading_field_to_none(self):
        api_error = {
            "kind": "http_error",
            "http_status_code": 503,
            "detail": "MetaTrader5 call timed out after 10.0s.",
        }

        record = build_record(
            timestamp_utc="2026-08-01T09:25:01.887012+00:00",
            symbol="EURUSD",
            strategy_version="deadbeef",
            http_status_code=503,
            response_json=None,
            api_error=api_error,
        )

        for field in RECORD_FIELDS_FROM_RESPONSE:
            assert record[field] is None

        assert record["http_status_code"] == 503
        assert record["api_error"] == api_error

    def test_missing_strategy_version_still_recorded_as_unknown_string(self):
        record = build_record(
            timestamp_utc="2026-08-01T00:00:00+00:00",
            symbol="EURUSD",
            strategy_version="unknown",
            http_status_code=200,
            response_json={"status": "BLOCKED"},
            api_error=None,
        )

        assert record["strategy_version"] == "unknown"


# ---------------------------------------------------------------
# append_record
# ---------------------------------------------------------------


class TestAppendRecord:
    def test_creates_missing_parent_directories(self, tmp_path):
        log_path = tmp_path / "nested" / "forward_test" / "smc_signal.jsonl"

        append_record({"a": 1}, log_path)

        assert log_path.exists()
        assert json.loads(log_path.read_text().splitlines()[0]) == {"a": 1}

    def test_never_overwrites_prior_records(self, tmp_path):
        log_path = tmp_path / "smc_signal.jsonl"

        append_record({"seq": 1}, log_path)
        append_record({"seq": 2}, log_path)

        lines = log_path.read_text(encoding="utf-8").splitlines()

        assert len(lines) == 2
        assert json.loads(lines[0]) == {"seq": 1}
        assert json.loads(lines[1]) == {"seq": 2}

    def test_each_line_is_independently_valid_json(self, tmp_path):
        log_path = tmp_path / "smc_signal.jsonl"

        for i in range(5):
            append_record({"seq": i}, log_path)

        lines = log_path.read_text(encoding="utf-8").splitlines()

        assert len(lines) == 5
        assert [json.loads(line)["seq"] for line in lines] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------
# _validate_interval
# ---------------------------------------------------------------


class TestValidateInterval:
    def test_default_interval_is_accepted(self):
        _validate_interval(300.0)  # must not raise

    def test_exact_floor_is_accepted(self):
        _validate_interval(MINIMUM_INTERVAL_SECONDS)  # must not raise

    def test_below_floor_is_rejected(self):
        with pytest.raises(ValueError):
            _validate_interval(MINIMUM_INTERVAL_SECONDS - 1)

    def test_five_seconds_is_rejected(self):
        with pytest.raises(ValueError):
            _validate_interval(5.0)


# ---------------------------------------------------------------
# CLI defaults
# ---------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        args = _parse_args([])

        assert args.symbol == "EURUSD"
        assert args.loop is False
        assert args.iterations is None
        assert args.interval_seconds == 300.0

    def test_loop_flag_and_overrides(self):
        args = _parse_args(
            ["--loop", "--interval-seconds", "60", "--iterations", "3"]
        )

        assert args.loop is True
        assert args.interval_seconds == 60.0
        assert args.iterations == 3


# ---------------------------------------------------------------
# Safety: no execution capability of any kind
# ---------------------------------------------------------------


def test_recorder_never_places_an_order():
    source = RECORDER_SOURCE_PATH.read_text(encoding="utf-8")

    assert "order_send(" not in source
    assert "MetaTrader5" not in source
    assert "import MetaTrader5" not in source
