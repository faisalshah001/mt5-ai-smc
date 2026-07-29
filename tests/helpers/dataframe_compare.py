"""
Deterministic DataFrame comparison helpers for hand-verified baseline
tests (as opposed to golden-file snapshot tests, see golden.py).
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def cell(dataframe: pd.DataFrame, row_position: int, column: str) -> Any:
    """
    Read one cell by row position, returning ``None`` for any missing
    value instead of ``pandas.NA``/``NaN``/``NaT`` — keeps assertions
    in test bodies simple (``== None`` or ``== expected_value``).
    """

    value = dataframe.iloc[row_position][column]

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value
