"""
Focused tests for the new, purely-computational helpers in
scripts/generate_forward_test_research_report.py.

The script's own end-to-end flow (main()) requires a live MT5
connection and the real forward-test log, so it is intentionally not
exercised here -- it was verified manually against the real log per
the task's testing requirements. This file covers the small, reusable
pure functions the task brief called out explicitly: sample_size_label,
_safe_ratio, _signed_expected_pips, and the baseline
save/refuse-overwrite/replace/compare behaviour (using tmp_path so no
repository file is ever touched).
"""

from __future__ import annotations

import json

from scripts.analyze_rejection_outcomes import WindowOutcome
from scripts.generate_forward_test_research_report import (
    SAMPLE_SIZE_LARGE_LABEL,
    _safe_ratio,
    _signed_expected_pips,
    compare_to_baseline,
    load_baseline,
    sample_size_label,
    save_baseline,
)


# ---------------------------------------------------------------------------
# sample_size_label
# ---------------------------------------------------------------------------


def test_sample_size_label_boundaries():
    assert sample_size_label(0) == "VERY SMALL SAMPLE"
    assert sample_size_label(9) == "VERY SMALL SAMPLE"
    assert sample_size_label(10) == "SMALL SAMPLE"
    assert sample_size_label(29) == "SMALL SAMPLE"
    assert sample_size_label(30) == "DEVELOPING SAMPLE"
    assert sample_size_label(99) == "DEVELOPING SAMPLE"
    assert sample_size_label(100) == SAMPLE_SIZE_LARGE_LABEL
    assert sample_size_label(1000) == SAMPLE_SIZE_LARGE_LABEL


# ---------------------------------------------------------------------------
# _safe_ratio
# ---------------------------------------------------------------------------


def test_safe_ratio_normal_division():
    assert _safe_ratio(6.0, 3.0) == 2.0


def test_safe_ratio_zero_denominator_returns_none():
    assert _safe_ratio(6.0, 0.0) is None


def test_safe_ratio_missing_inputs_return_none():
    assert _safe_ratio(None, 3.0) is None
    assert _safe_ratio(6.0, None) is None
    assert _safe_ratio(None, None) is None


# ---------------------------------------------------------------------------
# _signed_expected_pips
# ---------------------------------------------------------------------------


def _window_outcome(**overrides) -> WindowOutcome:
    base = dict(
        window_minutes=60,
        candles_available=12,
        reference_price=1.1000,
        ending_price=1.1010,
        net_movement_pips=10.0,
        movement_in_expected_direction_pips=10.0,
        movement_against_expected_direction_pips=0.0,
        mfe_price=1.1015,
        mfe_pips=15.0,
        mae_price=1.0995,
        mae_pips=5.0,
        insufficient_data=False,
    )
    base.update(overrides)
    return WindowOutcome(**base)


def test_signed_expected_pips_favorable():
    window = _window_outcome(
        movement_in_expected_direction_pips=10.0,
        movement_against_expected_direction_pips=0.0,
    )
    assert _signed_expected_pips(window) == 10.0


def test_signed_expected_pips_adverse():
    window = _window_outcome(
        movement_in_expected_direction_pips=0.0,
        movement_against_expected_direction_pips=7.5,
    )
    assert _signed_expected_pips(window) == -7.5


def test_signed_expected_pips_missing_fields_return_none():
    window = _window_outcome(movement_in_expected_direction_pips=None)
    assert _signed_expected_pips(window) is None


# ---------------------------------------------------------------------------
# Baseline save / refuse-overwrite / replace / compare
# ---------------------------------------------------------------------------


def test_save_baseline_creates_new_file(tmp_path):
    baseline_path = tmp_path / "research_baseline.json"
    snapshot = {"saved_at_utc": "2026-08-01T00:00:00+00:00", "total_evaluations": 10}

    saved, message = save_baseline(snapshot, baseline_path, replace=False)

    assert saved is True
    assert "Saved" in message
    assert baseline_path.exists()
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == snapshot


def test_save_baseline_refuses_to_overwrite_existing_without_replace(tmp_path):
    baseline_path = tmp_path / "research_baseline.json"
    original = {"saved_at_utc": "2026-08-01T00:00:00+00:00", "total_evaluations": 10}
    save_baseline(original, baseline_path, replace=False)

    attempted_overwrite = {"saved_at_utc": "2026-08-02T00:00:00+00:00", "total_evaluations": 20}
    saved, message = save_baseline(attempted_overwrite, baseline_path, replace=False)

    assert saved is False
    assert "not overwritten" in message
    # File on disk must be untouched.
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == original


def test_save_baseline_replaces_when_explicitly_requested(tmp_path):
    baseline_path = tmp_path / "research_baseline.json"
    original = {"saved_at_utc": "2026-08-01T00:00:00+00:00", "total_evaluations": 10}
    save_baseline(original, baseline_path, replace=False)

    replacement = {"saved_at_utc": "2026-08-02T00:00:00+00:00", "total_evaluations": 20}
    saved, message = save_baseline(replacement, baseline_path, replace=True)

    assert saved is True
    assert "Replaced" in message
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == replacement


def test_load_baseline_missing_file_returns_none(tmp_path):
    assert load_baseline(tmp_path / "does_not_exist.json") is None


def test_load_baseline_malformed_json_returns_none(tmp_path):
    baseline_path = tmp_path / "research_baseline.json"
    baseline_path.write_text("{not valid json", encoding="utf-8")

    assert load_baseline(baseline_path) is None


def test_compare_to_baseline_no_baseline_available():
    current = {
        "total_evaluations": 10,
        "deduplicated_rejection_episodes": 2,
        "actual_setup_count": 0,
        "by_category": {},
    }

    result = compare_to_baseline(current, None)

    assert result["baseline_available"] is False


def test_compare_to_baseline_computes_deltas_per_category():
    baseline = {
        "saved_at_utc": "2026-08-01T00:00:00+00:00",
        "total_evaluations": 10,
        "deduplicated_rejection_episodes": 2,
        "actual_setup_count": 0,
        "by_category": {
            "M15_CHOCH_MISSING": {
                "episode_count": 5,
                "mean_mfe_pips_60m": 4.0,
                "mean_mae_pips_60m": 3.0,
                "mfe_mae_ratio_60m": 1.33,
            }
        },
    }
    current = {
        "total_evaluations": 20,
        "deduplicated_rejection_episodes": 4,
        "actual_setup_count": 1,
        "by_category": {
            "M15_CHOCH_MISSING": {
                "episode_count": 8,
                "mean_mfe_pips_60m": 5.0,
                "mean_mae_pips_60m": 3.5,
                "mfe_mae_ratio_60m": 1.43,
            }
        },
    }

    result = compare_to_baseline(current, baseline)

    assert result["baseline_available"] is True
    category = result["by_category"]["M15_CHOCH_MISSING"]
    assert category["mean_mfe_pips_60m_delta"] == 1.0
    assert category["mean_mae_pips_60m_delta"] == 0.5
