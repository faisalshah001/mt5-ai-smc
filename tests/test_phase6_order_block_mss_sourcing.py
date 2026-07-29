"""
Phase 6 coverage for Decision #12 (Order Block MSS-sourcing,
SMC_SPECIFICATION.md §28, Appendix B).

Several scenarios here are hand-crafted DataFrames rather than
zigzag-built candle histories: detect_order_blocks only needs OHLC
plus structure_event/event_direction/broken_level, and precisely
engineering the "same anchor candle" (match) vs. "different anchor
candle" (mismatch) promotion cases requires exact control over candle
colour that swing-based fixtures don't reliably provide (verified
empirically: several natural CHoCH-producing fixtures were tried and
consistently produced the mismatch case only, across multiple
lookback_bars values). Every value below was confirmed against actual
detect_order_blocks output before being written into an assertion.
"""

from __future__ import annotations

import pandas as pd

from app.analysis.order_blocks import detect_order_blocks
from tests.helpers.candles import build_zigzag_candles


def _flat_row(price):
    return price, price


def _build_frame(rows, atr=0.01):
    """
    rows: list of dicts, each with at least "open"/"close" and
    optionally "structure_event"/"event_direction"/"broken_level"/
    "mss_invalidated_origin_index".
    """
    n = len(rows)
    opens = [r["open"] for r in rows]
    closes = [r["close"] for r in rows]
    highs = [max(o, c) + 0.002 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.002 for o, c in zip(opens, closes)]

    return pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-01-01", periods=n, freq="1h", tz="UTC"
            ),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr14": [atr] * n,
            "structure_event": [
                r.get("structure_event") for r in rows
            ],
            "event_direction": [
                r.get("event_direction") for r in rows
            ],
            "broken_level": [r.get("broken_level") for r in rows],
            "mss_invalidated_origin_index": [
                r.get("mss_invalidated_origin_index") for r in rows
            ],
        }
    )


def _promotion_match_frame():
    rows = [{"open": 1.10, "close": 1.10} for _ in range(7)]
    rows.append({"open": 1.10, "close": 1.095})  # 7: bearish anchor
    rows.append({"open": 1.095, "close": 1.11})  # 8
    rows.append({"open": 1.11, "close": 1.13})  # 9
    rows.append(
        {
            "open": 1.13,
            "close": 1.20,
            "structure_event": "MSS",
            "event_direction": "bullish",
            "broken_level": 1.12,
        }
    )  # 10: MSS
    rows.extend({"open": 1.20, "close": 1.20} for _ in range(7))  # 11-17
    rows.append(
        {
            "open": 1.20,
            "close": 1.25,
            "structure_event": "CHoCH",
            "event_direction": "bullish",
            "broken_level": 1.12,
        }
    )  # 18: CHoCH, same anchor candle as MSS (no intervening
    #     opposite-colour candle in either search window)
    rows.append({"open": 1.25, "close": 1.25})  # 19
    return _build_frame(rows)


def _run_canonical_pipeline(candles):
    # Decision #3 (Phase 7): detect_structure_state performs
    # per-cycle classification itself — no separate
    # classify_market_structure pass in the canonical pipeline.
    from app.analysis.market_structure import detect_swing_points
    from app.analysis.state_machine import detect_structure_state
    from app.indicators.technical import calculate_indicators

    indicators = calculate_indicators(candles)
    swings = detect_swing_points(indicators, left_bars=3, right_bars=3)
    return detect_structure_state(swings, minimum_break_atr=0.10)


def _promotion_mismatch_frame():
    # Reuses a Phase-0-established, empirically-verified CHoCH scenario
    # where the MSS's own anchor (candle_index 40) genuinely differs
    # from the CHoCH's own anchor (candle_index 56), because a real
    # swing-low leg (with its own bearish candles) forms between them.
    # Run through the full canonical pipeline (not raw candles) so
    # structure_event/event_direction/broken_level/atr14 are populated.
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
    return _run_canonical_pipeline(candles)


def _invalidation_cascade_frame(mitigate_before_invalidation=False):
    rows = [{"open": 1.10, "close": 1.10} for _ in range(7)]
    rows.append({"open": 1.10, "close": 1.095})  # 7: bearish anchor
    rows.append({"open": 1.095, "close": 1.11})  # 8
    rows.append({"open": 1.11, "close": 1.13})  # 9
    rows.append(
        {
            "open": 1.13,
            "close": 1.20,
            "structure_event": "MSS",
            "event_direction": "bullish",
            "broken_level": 1.12,
        }
    )  # 10: MSS, block range approx [1.093, 1.102]

    if mitigate_before_invalidation:
        rows.append({"open": 1.20, "close": 1.20})  # 11
        rows.append(
            {"open": 1.20, "close": 1.095}
        )  # 12: dips back into block range -> mitigation
        rows.append({"open": 1.095, "close": 1.10})  # 13
        rows.append({"open": 1.10, "close": 1.10})  # 14
    else:
        rows.extend({"open": 1.20, "close": 1.20} for _ in range(4))  # 11-14

    rows[-1] = {
        **rows[-1],
        "structure_event": "MSS_INVALIDATED",
        "event_direction": "bearish",
        "broken_level": 1.12,
        "mss_invalidated_origin_index": 10,
    }
    return _build_frame(rows)


def test_mss_sourced_block_created_provisional():
    # Truncated before the CHoCH row (18) — checks the state
    # immediately after creation, prior to any promotion.
    frame = _promotion_match_frame().iloc[:15].reset_index(drop=True)
    _, registry, _ = detect_order_blocks(frame, lookback_bars=12)

    mss_sourced = [
        b for b in registry.all() if b.source_event_type == "MSS"
    ]
    assert mss_sourced
    for block in mss_sourced:
        assert block.confirmation_status == "provisional"
        assert block.confirming_event_id is None


def test_promotion_match_confirms_in_place_no_duplicate():
    frame = _promotion_match_frame()
    result, registry, events = detect_order_blocks(
        frame, lookback_bars=12
    )

    # Exactly one block total — no duplicate created for the
    # confirming CHoCH, since it resolved to the same anchor.
    assert len(registry.all()) == 1

    block = registry.all()[0]
    assert block.source_event_type == "MSS"
    assert block.candle_index == 7
    assert block.confirmation_status == "confirmed"
    assert block.confirming_event_type == "CHoCH"
    assert block.confirming_event_id == "STR_CHoCH_00018"
    # Provenance is preserved, never overwritten by promotion.
    assert block.source_event_id == "STR_MSS_00010"
    assert block.created_index == 10

    assert result.loc[18, "order_block_confirmed"] == True  # noqa: E712
    assert (
        result.loc[18, "confirmed_order_block_id"]
        == block.order_block_id
    )

    confirmed_events = [
        e for e in events if e.event_type == "ORDER_BLOCK_CONFIRMED"
    ]
    assert len(confirmed_events) == 1
    assert confirmed_events[0].metadata["source_event_id"] == (
        "STR_MSS_00010"
    )


def test_promotion_mismatch_confirms_original_and_creates_independent_block():
    frame = _promotion_mismatch_frame()
    _, registry, events = detect_order_blocks(frame, lookback_bars=12)

    mss_sourced = [
        b for b in registry.all() if b.source_event_type == "MSS"
    ]
    choch_sourced = [
        b for b in registry.all() if b.source_event_type == "CHoCH"
    ]

    assert len(mss_sourced) == 1
    assert len(choch_sourced) == 1
    assert mss_sourced[0].candle_index != choch_sourced[0].candle_index

    # §28 point 4: "left as an independent, already-confirmed block" —
    # the original MSS-sourced block is still promoted, even though a
    # separate CHoCH-sourced block was also created.
    assert mss_sourced[0].confirmation_status == "confirmed"
    assert mss_sourced[0].confirming_event_type == "CHoCH"

    confirmed_events = [
        e for e in events if e.event_type == "ORDER_BLOCK_CONFIRMED"
    ]
    assert len(confirmed_events) == 1


def test_invalidation_cascade_invalidates_active_block():
    frame = _invalidation_cascade_frame(
        mitigate_before_invalidation=False
    )
    result, registry, events = detect_order_blocks(
        frame, lookback_bars=12
    )

    block = registry.all()[0]
    assert block.status == "invalidated"
    assert block.invalidation_reason == "mss_invalidated"
    assert block.invalidated_index == 14

    # Audit clarification: confirmation_status is never touched by the
    # cascade — it stays "provisional" permanently.
    assert block.confirmation_status == "provisional"

    assert result.loc[14, "order_block_invalidated"] == True  # noqa: E712
    assert (
        result.loc[14, "invalidated_order_block_id"]
        == block.order_block_id
    )

    invalidated_events = [
        e for e in events if e.event_type == "ORDER_BLOCK_INVALIDATED"
    ]
    assert len(invalidated_events) == 1
    assert (
        invalidated_events[0].metadata["invalidation_reason"]
        == "mss_invalidated"
    )


def test_invalidation_cascade_never_re_invalidates_a_terminal_block():
    frame = _invalidation_cascade_frame(
        mitigate_before_invalidation=True
    )
    _, registry, events = detect_order_blocks(frame, lookback_bars=12)

    block = registry.all()[0]

    # Price-based mitigation happened first (row 12); the cascade at
    # row 14 must leave it alone — status transitions are one-way and
    # already terminal.
    assert block.status == "mitigated"
    assert block.mitigated is True
    assert block.invalidated is False
    assert block.invalidation_reason is None

    invalidated_events = [
        e for e in events if e.event_type == "ORDER_BLOCK_INVALIDATED"
    ]
    assert invalidated_events == []


def test_bos_and_choch_sourced_blocks_default_to_confirmed():
    # BOS/CHoCH-sourced blocks are terminal by construction — never
    # provisional, never touched by the invalidation cascade.
    candles = build_zigzag_candles(
        [1.1000, 1.1050, 1.0980, 1.1150, 1.1080, 1.1250, 1.1150, 1.1350],
        candles_per_leg=8,
    )
    structure = _run_canonical_pipeline(candles)

    _, registry, _ = detect_order_blocks(structure, lookback_bars=12)

    bos_sourced = [
        b for b in registry.all() if b.source_event_type == "BOS"
    ]
    assert bos_sourced
    for block in bos_sourced:
        assert block.confirmation_status == "confirmed"
        assert block.invalidation_reason is None


def test_source_event_types_accepts_mss_alone():
    frame = _promotion_match_frame()

    _, registry, _ = detect_order_blocks(
        frame,
        source_event_types=("MSS",),
        lookback_bars=12,
    )

    assert any(
        b.source_event_type == "MSS" for b in registry.all()
    )


def test_unsupported_source_event_type_still_rejected():
    import pytest

    frame = _promotion_match_frame()

    with pytest.raises(ValueError, match="Unsupported source_event_types"):
        detect_order_blocks(
            frame,
            source_event_types=("FVG",),
            lookback_bars=12,
        )
