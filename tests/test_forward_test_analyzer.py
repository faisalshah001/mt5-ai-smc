"""
Tests for scripts/forward_test_analyzer.py (Demo Forward-Testing
Phase 2).

Every computation function is pure (no I/O), so it is exercised here
with hand-built record lists -- no live server, no MT5 terminal, no
log file required, matching this project's existing testing style.
load_records is the only function that touches disk; its tests use
tmp_path and explicitly prove the log is never modified.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import forward_test_recorder
from scripts.forward_test_analyzer import (
    PENDING_SIGNAL_FIELDS,
    compute_api_error_counts,
    compute_hourly_histogram,
    compute_http_status_counts,
    compute_rejection_reason_counts,
    compute_status_counts,
    compute_strategy_version_summary,
    compute_summary_stats,
    extract_pending_signals,
    generate_report,
    load_records,
)
from scripts.forward_test_analyzer import DEFAULT_LOG_PATH as ANALYZER_DEFAULT_LOG_PATH


def test_default_log_path_matches_the_recorders_own_default():
    """
    The analyzer intentionally redefines DEFAULT_LOG_PATH standalone
    (see forward_test_analyzer.py's comment) rather than importing it
    from the recorder, so it can run directly without the repository
    root already being on sys.path. This test guards against the two
    copies silently drifting apart.
    """

    assert ANALYZER_DEFAULT_LOG_PATH == forward_test_recorder.DEFAULT_LOG_PATH


def _record(**overrides) -> dict:
    """A minimal, valid forward-test record with sensible defaults."""

    base = {
        "timestamp_utc": "2026-08-01T09:00:00+00:00",
        "symbol": "EURUSD",
        "strategy_version": "abc1234",
        "http_status_code": 200,
        "status": "NO_SETUP",
        "direction": None,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_percent": 0.5,
        "risk_reward": None,
        "position_size": None,
        "confidence": 0,
        "evidence": {},
        "rejection_reasons": ["No H4 directional bias (external_trend is neutral)."],
        "api_error": None,
    }
    base.update(overrides)
    return base


def _pending_record(**overrides) -> dict:
    defaults = {
        "status": "SIGNAL_PENDING_APPROVAL",
        "direction": "BUY",
        "entry": 1.10505,
        "stop_loss": 1.0995,
        "take_profit": 1.1461,
        "risk_reward": 2.0,
        "confidence": 100,
        "rejection_reasons": [],
    }
    defaults.update(overrides)
    return _record(**defaults)


def _error_record(**overrides) -> dict:
    defaults = {
        "status": None,
        "http_status_code": 503,
        "risk_percent": None,
        "rejection_reasons": None,
        "api_error": {
            "kind": "http_error",
            "http_status_code": 503,
            "detail": "timed out",
        },
    }
    defaults.update(overrides)
    return _record(**defaults)


# ---------------------------------------------------------------
# load_records
# ---------------------------------------------------------------


class TestLoadRecords:
    def test_reads_valid_jsonl_file(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        log_path.write_text(
            json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n",
            encoding="utf-8",
        )

        records, malformed = load_records(log_path)

        assert records == [{"a": 1}, {"a": 2}]
        assert malformed == 0

    def test_skips_blank_lines(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        log_path.write_text(
            json.dumps({"a": 1}) + "\n\n\n" + json.dumps({"a": 2}) + "\n",
            encoding="utf-8",
        )

        records, malformed = load_records(log_path)

        assert records == [{"a": 1}, {"a": 2}]
        assert malformed == 0

    def test_skips_and_counts_malformed_lines(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        log_path.write_text(
            json.dumps({"a": 1}) + "\n"
            "{not valid json\n"
            + json.dumps({"a": 2}) + "\n",
            encoding="utf-8",
        )

        records, malformed = load_records(log_path)

        assert records == [{"a": 1}, {"a": 2}]
        assert malformed == 1

    def test_missing_file_returns_empty_without_error(self, tmp_path):
        records, malformed = load_records(tmp_path / "does_not_exist.jsonl")

        assert records == []
        assert malformed == 0

    def test_never_modifies_the_log_file(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        original_content = json.dumps({"a": 1}) + "\n"
        log_path.write_text(original_content, encoding="utf-8")

        load_records(log_path)
        load_records(log_path)
        load_records(log_path)

        assert log_path.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------
# compute_summary_stats
# ---------------------------------------------------------------


class TestComputeSummaryStats:
    def test_empty_records(self):
        stats = compute_summary_stats([])

        assert stats["total_evaluations"] == 0
        assert stats["first_timestamp"] is None
        assert stats["last_timestamp"] is None
        assert stats["duration_seconds"] == 0.0
        assert stats["evaluations_per_hour"] is None

    def test_single_record_has_undefined_rate_not_infinite(self):
        stats = compute_summary_stats([_record()])

        assert stats["total_evaluations"] == 1
        assert stats["duration_seconds"] == 0.0
        assert stats["evaluations_per_hour"] is None

    def test_multiple_records_compute_duration_and_rate(self):
        records = [
            _record(timestamp_utc="2026-08-01T09:00:00+00:00"),
            _record(timestamp_utc="2026-08-01T10:00:00+00:00"),
            _record(timestamp_utc="2026-08-01T11:00:00+00:00"),
        ]

        stats = compute_summary_stats(records)

        assert stats["total_evaluations"] == 3
        assert stats["first_timestamp"] == "2026-08-01T09:00:00+00:00"
        assert stats["last_timestamp"] == "2026-08-01T11:00:00+00:00"
        assert stats["duration_seconds"] == 7200.0
        assert stats["evaluations_per_hour"] == 1.5

    def test_out_of_order_records_are_sorted_by_time(self):
        records = [
            _record(timestamp_utc="2026-08-01T11:00:00+00:00"),
            _record(timestamp_utc="2026-08-01T09:00:00+00:00"),
        ]

        stats = compute_summary_stats(records)

        assert stats["first_timestamp"] == "2026-08-01T09:00:00+00:00"
        assert stats["last_timestamp"] == "2026-08-01T11:00:00+00:00"

    def test_ignores_records_with_unparseable_timestamps(self):
        records = [
            _record(timestamp_utc="not-a-timestamp"),
            _record(timestamp_utc="2026-08-01T09:00:00+00:00"),
        ]

        stats = compute_summary_stats(records)

        assert stats["total_evaluations"] == 2
        assert stats["first_timestamp"] == "2026-08-01T09:00:00+00:00"
        assert stats["last_timestamp"] == "2026-08-01T09:00:00+00:00"


# ---------------------------------------------------------------
# compute_status_counts
# ---------------------------------------------------------------


class TestComputeStatusCounts:
    def test_counts_each_status(self):
        records = [
            _record(status="NO_SETUP"),
            _record(status="NO_SETUP"),
            _pending_record(),
            _record(status="BLOCKED"),
        ]

        counts = compute_status_counts(records)

        assert counts == {"NO_SETUP": 2, "SIGNAL_PENDING_APPROVAL": 1, "BLOCKED": 1}

    def test_none_status_bucketed_as_api_error(self):
        counts = compute_status_counts([_error_record()])

        assert counts == {"API_ERROR": 1}


# ---------------------------------------------------------------
# compute_http_status_counts
# ---------------------------------------------------------------


class TestComputeHttpStatusCounts:
    def test_counts_by_code(self):
        records = [
            _record(http_status_code=200),
            _record(http_status_code=200),
            _error_record(http_status_code=503),
        ]

        counts = compute_http_status_counts(records)

        assert counts == {"200": 2, "503": 1}

    def test_missing_code_bucketed_as_none(self):
        records = [_error_record(http_status_code=None)]

        counts = compute_http_status_counts(records)

        assert counts == {"none": 1}


# ---------------------------------------------------------------
# compute_api_error_counts
# ---------------------------------------------------------------


class TestComputeApiErrorCounts:
    def test_counts_by_kind_and_none(self):
        records = [
            _record(),  # api_error=None
            _error_record(),  # kind="http_error"
            _record(
                status=None,
                api_error={
                    "kind": "connection_error",
                    "http_status_code": None,
                    "detail": "refused",
                },
            ),
        ]

        counts = compute_api_error_counts(records)

        assert counts == {"none": 1, "http_error": 1, "connection_error": 1}


# ---------------------------------------------------------------
# compute_rejection_reason_counts
# ---------------------------------------------------------------


class TestComputeRejectionReasonCounts:
    def test_ranked_by_frequency_descending(self):
        records = [
            _record(rejection_reasons=["A"]),
            _record(rejection_reasons=["A"]),
            _record(rejection_reasons=["B"]),
        ]

        ranked = compute_rejection_reason_counts(records)

        assert ranked == [("A", 2), ("B", 1)]

    def test_ties_broken_alphabetically(self):
        records = [
            _record(rejection_reasons=["Z reason"]),
            _record(rejection_reasons=["A reason"]),
        ]

        ranked = compute_rejection_reason_counts(records)

        assert ranked == [("A reason", 1), ("Z reason", 1)]

    def test_multiple_reasons_in_one_record_all_counted(self):
        records = [_record(rejection_reasons=["A", "B"])]

        ranked = compute_rejection_reason_counts(records)

        assert ranked == [("A", 1), ("B", 1)]

    def test_pending_signals_contribute_nothing(self):
        ranked = compute_rejection_reason_counts([_pending_record()])

        assert ranked == []

    def test_missing_rejection_reasons_field_is_tolerated(self):
        ranked = compute_rejection_reason_counts([_error_record()])

        assert ranked == []


# ---------------------------------------------------------------
# extract_pending_signals
# ---------------------------------------------------------------


class TestExtractPendingSignals:
    def test_filters_to_only_pending_signals(self):
        records = [_record(), _pending_record(), _error_record()]

        pending = extract_pending_signals(records)

        assert len(pending) == 1

    def test_contains_exactly_the_documented_fields(self):
        pending = extract_pending_signals([_pending_record()])

        assert set(pending[0].keys()) == set(PENDING_SIGNAL_FIELDS)

    def test_values_copied_verbatim(self):
        signal = _pending_record(
            direction="SELL",
            entry=1.2000,
            stop_loss=1.2050,
            take_profit=1.1900,
            confidence=100,
            risk_reward=2.0,
        )

        pending = extract_pending_signals([signal])

        assert pending[0]["direction"] == "SELL"
        assert pending[0]["entry"] == 1.2000
        assert pending[0]["stop_loss"] == 1.2050
        assert pending[0]["take_profit"] == 1.1900
        assert pending[0]["confidence"] == 100
        assert pending[0]["risk_reward"] == 2.0

    def test_preserves_chronological_order(self):
        first = _pending_record(timestamp_utc="2026-08-01T09:00:00+00:00")
        second = _pending_record(timestamp_utc="2026-08-01T10:00:00+00:00")

        pending = extract_pending_signals([first, second])

        assert [p["timestamp_utc"] for p in pending] == [
            "2026-08-01T09:00:00+00:00",
            "2026-08-01T10:00:00+00:00",
        ]

    def test_no_pending_signals_returns_empty_list(self):
        assert extract_pending_signals([_record(), _error_record()]) == []


# ---------------------------------------------------------------
# compute_strategy_version_summary
# ---------------------------------------------------------------


class TestComputeStrategyVersionSummary:
    def test_groups_by_version(self):
        records = [
            _record(strategy_version="v1", status="NO_SETUP"),
            _record(strategy_version="v1", status="NO_SETUP"),
            _pending_record(strategy_version="v2"),
        ]

        summary = compute_strategy_version_summary(records)

        assert summary["v1"]["total_evaluations"] == 2
        assert summary["v1"]["status_counts"] == {"NO_SETUP": 2}
        assert summary["v2"]["total_evaluations"] == 1
        assert summary["v2"]["status_counts"] == {"SIGNAL_PENDING_APPROVAL": 1}

    def test_single_version_produces_one_group(self):
        records = [_record(strategy_version="v1"), _pending_record(strategy_version="v1")]

        summary = compute_strategy_version_summary(records)

        assert list(summary.keys()) == ["v1"]
        assert summary["v1"]["total_evaluations"] == 2

    def test_missing_version_falls_back_to_unknown(self):
        summary = compute_strategy_version_summary(
            [_record(strategy_version=None), _record(strategy_version="")]
        )

        assert summary["unknown"]["total_evaluations"] == 2


# ---------------------------------------------------------------
# compute_hourly_histogram
# ---------------------------------------------------------------


class TestComputeHourlyHistogram:
    def test_buckets_by_hour(self):
        records = [
            _record(timestamp_utc="2026-08-01T09:05:00+00:00"),
            _record(timestamp_utc="2026-08-01T09:45:00+00:00"),
            _record(timestamp_utc="2026-08-01T10:10:00+00:00"),
        ]

        histogram = compute_hourly_histogram(records)

        assert histogram == [
            ("2026-08-01 09:00", 2),
            ("2026-08-01 10:00", 1),
        ]

    def test_chronological_order_regardless_of_input_order(self):
        records = [
            _record(timestamp_utc="2026-08-01T11:00:00+00:00"),
            _record(timestamp_utc="2026-08-01T09:00:00+00:00"),
        ]

        histogram = compute_hourly_histogram(records)

        assert [bucket for bucket, _ in histogram] == [
            "2026-08-01 09:00",
            "2026-08-01 11:00",
        ]

    def test_unparseable_timestamps_excluded(self):
        records = [_record(timestamp_utc="garbage")]

        assert compute_hourly_histogram(records) == []

    def test_empty_records_returns_empty_histogram(self):
        assert compute_hourly_histogram([]) == []


# ---------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------


class TestGenerateReport:
    def test_empty_log_reports_no_records(self):
        report = generate_report([], log_path=Path("x.jsonl"))

        assert "No records found" in report

    def test_contains_every_required_section(self):
        records = [
            _record(status="NO_SETUP"),
            _pending_record(),
            _error_record(),
        ]

        report = generate_report(records, log_path=Path("x.jsonl"))

        for heading in (
            "Total evaluations:",
            "First timestamp:",
            "Last timestamp:",
            "Total duration:",
            "Evaluations per hour:",
            "STATUS COUNTS",
            "HTTP STATUS CODE COUNTS",
            "API ERROR COUNTS",
            "REJECTION REASONS",
            "SIGNAL_PENDING_APPROVAL EVALUATIONS",
            "STRATEGY VERSION SUMMARY",
            "HOURLY EVALUATION HISTOGRAM",
        ):
            assert heading in report

    def test_pending_signal_fields_appear_in_report(self):
        report = generate_report([_pending_record()], log_path=Path("x.jsonl"))

        assert "BUY" in report
        assert "entry=1.10505" in report
        assert "sl=1.0995" in report
        assert "tp=1.1461" in report
        assert "confidence=100" in report
        assert "rr=2.0" in report

    def test_malformed_line_count_produces_warning(self):
        report = generate_report(
            [_record()], log_path=Path("x.jsonl"), malformed_line_count=2
        )

        assert "2 malformed line(s)" in report

    def test_no_malformed_lines_produces_no_warning(self):
        report = generate_report(
            [_record()], log_path=Path("x.jsonl"), malformed_line_count=0
        )

        assert "malformed" not in report

    def test_does_not_mutate_input_records(self):
        records = [_record(), _pending_record()]
        snapshot = json.dumps(records, sort_keys=True)

        generate_report(records, log_path=Path("x.jsonl"))

        assert json.dumps(records, sort_keys=True) == snapshot
