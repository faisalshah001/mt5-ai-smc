"""
Golden-file regression comparison.

A "golden" is a committed, JSON-serialised snapshot of a pipeline
function's output, captured once from the current (pre-Phase-1)
implementation. Baseline tests compare fresh output against the
committed golden on every run. A mismatch means production behaviour
changed — which, for Phase 0's own tests, is never expected, since no
production file is touched in this phase.

Later phases are expected to deliberately update specific goldens when
a spec-approved decision changes output on purpose; that is a manual,
reviewed action (regenerate via ``tests/_generate_goldens.py``), never
an automatic one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


def _golden_path(name: str) -> Path:
    return _GOLDEN_DIR / f"{name}.json"


def save_golden(name: str, data: Any) -> None:
    """Write a golden snapshot to disk (used only by the generator script)."""

    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    with _golden_path(name).open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=False)
        handle.write("\n")


def load_golden(name: str) -> Any:
    """Load a committed golden snapshot."""

    path = _golden_path(name)

    if not path.exists():
        raise FileNotFoundError(
            f"Golden file '{name}' does not exist at {path}. "
            "Run tests/_generate_goldens.py to create it."
        )

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def assert_matches_golden(name: str, data: Any) -> None:
    """
    Assert freshly-computed, JSON-safe ``data`` matches the committed
    golden snapshot exactly.

    On mismatch, reports the first differing record/field to keep the
    failure message actionable instead of dumping the entire payload.
    """

    expected = load_golden(name)

    if data == expected:
        return

    if isinstance(data, list) and isinstance(expected, list):
        if len(data) != len(expected):
            raise AssertionError(
                f"Golden '{name}' record count changed: "
                f"expected {len(expected)}, got {len(data)}."
            )

        for position, (actual_row, expected_row) in enumerate(
            zip(data, expected)
        ):
            if actual_row != expected_row:
                raise AssertionError(
                    f"Golden '{name}' differs at record {position}:\n"
                    f"  expected: {expected_row}\n"
                    f"  actual:   {actual_row}"
                )

    raise AssertionError(f"Golden '{name}' output no longer matches.")
