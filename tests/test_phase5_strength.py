"""
Phase 5 coverage for Decision #13 (MarketEvent.strength population,
SMC_SPECIFICATION.md §29).

Every assertion below was verified empirically against actual
analysis_engine.py output before being written.
"""

from __future__ import annotations

from app.analysis.analysis_engine import analyze_market
from tests.helpers.candles import build_zigzag_candles


def _events_by_type(result, event_type):
    return [e for e in result.events if e.event_type == event_type]


def test_strength_equals_break_distance_over_required_for_mss_and_bos():
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,
            1.2020,
            1.1850,
            1.1900,
            1.1800,
            1.2100,
            1.1950,
            1.2200,
            1.2100,
        ],
        candles_per_leg=8,
    )
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    for event_type in ("BOS", "MSS"):
        events = _events_by_type(result, event_type)
        assert events, f"expected at least one {event_type} event"

        for event in events:
            break_distance = event.metadata["break_distance"]
            required_break_distance = event.metadata[
                "required_break_distance"
            ]

            assert event.strength is not None
            assert event.strength == (
                break_distance / required_break_distance
            )

            # Decision #13: strength is a derived convenience field,
            # not a replacement for its inputs — both must remain
            # present in metadata alongside it.
            assert "break_distance" in event.metadata
            assert "required_break_distance" in event.metadata


def test_strength_is_none_for_choch():
    # CHoCH is swing-driven (state_machine.py Section 1) and never
    # sets break_distance — strength therefore falls through to None
    # via Decision #13's own "not computable" rule, not a CHoCH-
    # specific carve-out in this code.
    candles = build_zigzag_candles(
        [
            1.2000,
            1.1950,
            1.2020,
            1.1850,
            1.1900,
            1.1800,
            1.2100,
            1.1950,
            1.2200,
            1.2100,
        ],
        candles_per_leg=8,
    )
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    choch_events = _events_by_type(result, "CHoCH")
    assert choch_events, "expected at least one CHoCH event"

    for event in choch_events:
        assert event.strength is None
        assert event.metadata.get("break_distance") is None


def test_strength_is_none_for_mss_invalidated():
    # MSS_INVALIDATED is likewise swing-driven and never sets
    # break_distance.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    invalidated_events = _events_by_type(result, "MSS_INVALIDATED")
    assert invalidated_events, "expected at least one MSS_INVALIDATED event"

    for event in invalidated_events:
        assert event.strength is None
        assert event.metadata.get("break_distance") is None


def test_strength_is_exactly_one_at_the_break_threshold():
    # Boundary case (Decision #13): "A value of 1.0 means the event
    # exactly met the required break threshold." Exercised directly
    # against _build_structure_events with a minimal synthetic row
    # where break_distance == required_break_distance exactly — the
    # real pipeline's ATR-derived threshold is not something this
    # exact boundary can be reliably engineered against via candle
    # construction.
    import pandas as pd

    from app.analysis.analysis_engine import _build_structure_events

    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01T00:00:00Z"], utc=True
            ),
            "structure_event": ["BOS"],
            "event_direction": ["bullish"],
            "close": [1.1000],
            "broken_level": [1.0950],
            "break_distance": [0.0005],
            "required_break_distance": [0.0005],
        }
    )

    events = _build_structure_events(frame)

    assert len(events) == 1
    assert events[0].strength == 1.0


def test_strength_above_one_for_a_break_exceeding_the_threshold():
    import pandas as pd

    from app.analysis.analysis_engine import _build_structure_events

    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01T00:00:00Z"], utc=True
            ),
            "structure_event": ["BOS"],
            "event_direction": ["bullish"],
            "close": [1.1000],
            "broken_level": [1.0950],
            "break_distance": [0.0015],
            "required_break_distance": [0.0005],
        }
    )

    events = _build_structure_events(frame)

    assert len(events) == 1
    assert events[0].strength == 3.0


def test_strength_is_none_when_required_break_distance_is_zero():
    # Division-by-zero guard: not addressed by the specification, but
    # required to avoid crashing when minimum_break_atr is configured
    # to 0 (a permitted value — only negative values are rejected).
    import pandas as pd

    from app.analysis.analysis_engine import _build_structure_events

    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01T00:00:00Z"], utc=True
            ),
            "structure_event": ["BOS"],
            "event_direction": ["bullish"],
            "close": [1.1000],
            "broken_level": [1.0950],
            "break_distance": [0.0005],
            "required_break_distance": [0.0],
        }
    )

    events = _build_structure_events(frame)

    assert len(events) == 1
    assert events[0].strength is None


def test_strength_not_clamped_and_not_negative_for_a_strong_break():
    # Decision #13: "The value is NOT clamped — no upper bound is
    # imposed." A break several times the minimum required distance
    # must report strength > 1.0 uncapped.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1120, 1.0850, 1.1250, 1.1150],
        candles_per_leg=8,
    )
    result = analyze_market(symbol="TEST", timeframe="H1", candles=candles)

    mss_events = _events_by_type(result, "MSS")
    assert mss_events

    for event in mss_events:
        assert event.strength > 1.0


def test_strength_field_still_exists_on_market_event_default_none():
    # The field is not removed even where unused — matches Decision
    # #13's explicit backward-compatibility requirement.
    from datetime import datetime, timezone

    from app.analysis.models import MarketEvent

    event = MarketEvent(
        event_id="EV_TEST",
        event_type="BOS",
        time=datetime.now(timezone.utc),
        index=0,
    )

    assert event.strength is None
