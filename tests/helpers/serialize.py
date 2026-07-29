"""
Deterministic, JSON-safe serialization of pipeline output.

Used by both the golden-file comparison helpers and the golden-file
generator script, so a snapshot written today and one compared against
it next month are guaranteed to use the exact same conversion rules.

The DataFrame conversion mirrors the pattern already used by
``main.py`` for its own JSON responses
(``frame.astype(object).where(pd.notnull(frame), None)``) — this reuses
an existing codebase convention rather than inventing a new one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


def _json_safe_value(value: Any) -> Any:
    """Convert one scalar cell/field into a JSON-serialisable value."""

    if value is None:
        return None

    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).isoformat()

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, (float,)):
        return value

    if isinstance(value, (int,)):
        return value

    return value


def dataframe_to_records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Convert a DataFrame into a deterministic, JSON-safe list of dicts.

    Row order is preserved exactly (never sorted) — row order is
    itself part of what these tests pin.
    """

    records: list[dict[str, Any]] = []

    for _, row in dataframe.iterrows():
        record = {
            column: _json_safe_value(row[column])
            for column in dataframe.columns
        }
        records.append(record)

    return records


def events_to_records(events: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of MarketEvent objects into JSON-safe dicts."""

    return [
        {
            key: _json_safe_value(value)
            for key, value in event.to_dict().items()
        }
        for event in events
    ]


def objects_to_records(objects: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of objects exposing to_dict() into JSON-safe dicts."""

    return [
        {
            key: _json_safe_value(value)
            for key, value in item.to_dict().items()
        }
        for item in objects
    ]
