from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

import pandas as pd

from app.analysis.analysis_engine import analyze_market
from app.analysis.models import AnalysisResult, OrderBlock
from app.mt5.market import (
    get_account_snapshot,
    get_candles,
    get_open_position_count,
    get_symbol_trade_specs,
)
from app.risk.calculator import (
    calculate_position_size,
    calculate_structural_trade_levels,
)


SYMBOL = "EURUSD"

DEFAULT_RISK_PERCENT = 0.5
DEFAULT_MINIMUM_RISK_REWARD = 2.0

# The M5 confirmation window must exceed the swing engine's own
# right_bars lookahead (the number of trailing candles required
# before a swing pivot can be classified at all) -- otherwise a
# "just confirmed" swing could never fall inside the window. See
# evaluate_m5_confirmation.
DEFAULT_M5_LOOKBACK_CANDLES = 5

DEFAULT_H4_COUNT = 200
DEFAULT_H1_COUNT = 200
DEFAULT_M15_COUNT = 300
DEFAULT_M5_COUNT = 100

_DIRECTION_TO_SIGNAL = {
    "bullish": "BUY",
    "bearish": "SELL",
}


def _direction_label(direction: str) -> str:
    """Map an engine direction ('bullish'/'bearish') to BUY/SELL."""

    return _DIRECTION_TO_SIGNAL[direction]


def evaluate_h4_bias(h4_result: AnalysisResult) -> dict[str, Any]:
    """
    Step 1 (BUY) / Step 1 (SELL): H4 directional bias only.

    Reuses the canonical engine's own StructureSnapshot.external_trend
    -- no separate bias detection logic is introduced. A neutral H4
    trend means there is no directional bias to trade from.
    """

    snapshot = h4_result.structure_snapshot
    trend = snapshot.external_trend if snapshot is not None else "neutral"

    passed = trend in ("bullish", "bearish")

    return {
        "passed": passed,
        "direction": trend if passed else None,
        "structure_state": (
            snapshot.structure_state if snapshot is not None else "neutral"
        ),
    }


def evaluate_h1_confirmation(
    h1_result: AnalysisResult,
    expected_direction: str,
) -> dict[str, Any]:
    """
    Step 2: H1 confirmation only.

    H1 confirms the H4 bias when its own external_trend agrees with
    the H4 direction. Reuses the same StructureSnapshot field as H4 --
    "confirmation" here means agreement between two independently
    computed structure states, not a separate confirmation algorithm.
    """

    snapshot = h1_result.structure_snapshot
    h1_trend = snapshot.external_trend if snapshot is not None else "neutral"

    passed = h1_trend == expected_direction

    return {
        "passed": passed,
        "h1_trend": h1_trend,
        "expected_direction": expected_direction,
        "structure_state": (
            snapshot.structure_state if snapshot is not None else "neutral"
        ),
    }


def evaluate_choch(
    m15_result: AnalysisResult,
    direction: str,
) -> Optional[dict[str, Any]]:
    """
    Step 4: confirmed CHoCH only -- MSS alone is never sufficient.

    Only MarketEvents of type "CHoCH" are ever considered here; MSS
    events are never inspected as a trigger, so an MSS that never
    resolves into a CHoCH cannot produce a signal by construction.

    The current M15 external_trend must still match `direction` --
    this guards against anchoring on a CHoCH that a later, opposite
    CHoCH has since superseded (a stale reversal, not the live trend).
    """

    snapshot = m15_result.structure_snapshot

    if snapshot is None or snapshot.external_trend != direction:
        return None

    choch_events = [
        event
        for event in m15_result.events
        if event.event_type == "CHoCH" and event.direction == direction
    ]

    if not choch_events:
        return None

    latest = max(choch_events, key=lambda event: event.index)

    return {
        "passed": True,
        "index": latest.index,
        "time": latest.time.isoformat(),
        "broken_level": latest.broken_level,
        "event_id": latest.event_id,
    }


def _find_current_cycle_start_index(
    m15_result: AnalysisResult,
    before_index: int,
) -> int:
    """
    Locate the start of the trend cycle that produced the CHoCH at
    `before_index` (Finding 2 fix).

    A trend cycle ends only at a confirmed CHoCH (of either direction)
    or an MSS_INVALIDATED -- Decision #3 (SMC_SPECIFICATION.md §7)
    establishes that "MSS alone never ends a cycle", so an MSS event
    itself is not a valid lower bound: in real ICT sequencing, the
    liquidity sweep is what *triggers* the break of structure, so it
    routinely PRECEDES its own cycle's MSS event, not follows it (see
    tests/helpers/candles.py::build_ict_m15_sequence_candles, a
    verified fixture with exactly this sweep-before-MSS order).

    The correct "same cycle" lower bound is therefore the end of the
    PREVIOUS cycle: the most recent CHoCH (either direction) or
    MSS_INVALIDATED strictly before `before_index`, plus one (that
    boundary row itself belongs to the cycle it completes, per
    state_machine.py's own cycle-reset comments -- never to the cycle
    that follows it). This still rejects Finding 2's stale-sweep
    scenario, since a sweep from an even earlier, already-resolved
    cycle sits before that boundary, while correctly accepting a
    sweep that precedes its own cycle's MSS.

    Returns 0 (start of the fetched window) if no earlier CHoCH or
    MSS_INVALIDATED exists -- i.e. `before_index` is in the first
    cycle visible in this candle window.
    """

    boundary_events = [
        event
        for event in m15_result.events
        if event.event_type in ("CHoCH", "MSS_INVALIDATED")
        and event.index < before_index
    ]

    if not boundary_events:
        return 0

    return max(boundary_events, key=lambda event: event.index).index + 1


def evaluate_liquidity_sweep(
    m15_result: AnalysisResult,
    direction: str,
    before_index: int,
) -> Optional[dict[str, Any]]:
    """
    Step 3: liquidity sweep on M15, occurring within the same trend
    cycle as the confirmed CHoCH found by evaluate_choch -- not merely
    anywhere earlier in the series (Finding 2 fix).

    A BUY requires a sell-side (SSL) sweep, which the liquidity engine
    already records with direction="bullish" (a sweep below sell-side
    liquidity that closes back above it is a bullish signal). A SELL
    requires a buy-side (BSL) sweep, recorded as direction="bearish".
    Filtering LIQUIDITY_SWEPT events by `direction == direction`
    therefore selects the correct side automatically, with no new
    sweep-detection logic.

    A sweep is only accepted when
    cycle_start_index <= sweep.index <= before_index, where
    cycle_start_index is the end of the previous cycle (see
    _find_current_cycle_start_index) -- excluding sweeps left over
    from an earlier, already-resolved cycle.
    """

    cycle_start_index = _find_current_cycle_start_index(
        m15_result,
        before_index,
    )

    sweeps = [
        event
        for event in m15_result.events
        if event.event_type == "LIQUIDITY_SWEPT"
        and event.direction == direction
        and cycle_start_index <= event.index <= before_index
    ]

    if not sweeps:
        return None

    latest = max(sweeps, key=lambda event: event.index)

    return {
        "passed": True,
        "index": latest.index,
        "time": latest.time.isoformat(),
        "level": latest.broken_level,
        "liquidity_id": latest.source_id,
        "liquidity_type": latest.source_type,
    }


def evaluate_displacement_and_order_block(
    m15_result: AnalysisResult,
    direction: str,
    choch_index: int,
) -> Optional[OrderBlock]:
    """
    Steps 5-6: bullish/bearish displacement and its linked, valid
    Order Block.

    Reuses app.analysis.order_blocks.detect_order_blocks' own output
    directly -- an Order Block's existence already encodes a
    displacement large enough to clear minimum_displacement_atr, so no
    separate displacement check is implemented here.

    "Linked to that displacement" is interpreted as: the Order Block
    was created or promoted-to-confirmed on the exact same row as the
    CHoCH found by evaluate_choch (created_index for a CHoCH-sourced
    block, confirmed_index for an MSS-sourced block later promoted by
    that CHoCH -- see order_blocks.py, Decision #12).

    "Valid" excludes any Order Block that is not yet confirmed, or
    that has already been invalidated or expired.
    """

    order_block_type = "bullish" if direction == "bullish" else "bearish"

    for block in m15_result.order_blocks:
        if block.order_block_type != order_block_type:
            continue

        if block.confirmation_status != "confirmed":
            continue

        if block.invalidated or block.expired:
            continue

        anchor_index = (
            block.confirmed_index
            if block.confirmed_index is not None
            else block.created_index
        )

        if anchor_index == choch_index:
            return block

    return None


def evaluate_retracement(order_block: OrderBlock) -> bool:
    """
    Step 7: price retraces into the Order Block.

    Reuses the Order Block's own `mitigated` field -- mitigation, in
    app.analysis.order_blocks.detect_order_blocks, is defined as
    exactly this: a later candle trading back into the block's range.
    No separate price-containment check is introduced.
    """

    return bool(order_block.mitigated) and not order_block.invalidated and not order_block.expired


def evaluate_m5_confirmation(
    m5_result: AnalysisResult,
    direction: str,
    lookback_candles: int,
) -> Optional[dict[str, Any]]:
    """
    Step 8: M5 execution confirmation only -- a local liquidity sweep,
    a confirmed CHoCH, both within the last `lookback_candles` M5
    candles, using the latest M5 candle's close as the entry trigger.

    Reuses the same engine and the same event types as the M15 steps,
    scoped to a short recency window instead of the whole series.
    """

    candle_count = len(m5_result.candles)

    if candle_count == 0:
        return None

    window_start = max(0, candle_count - lookback_candles)

    choch_events = [
        event
        for event in m5_result.events
        if event.event_type == "CHoCH"
        and event.direction == direction
        and event.index >= window_start
    ]

    if not choch_events:
        return None

    latest_choch = max(choch_events, key=lambda event: event.index)

    sweep_events = [
        event
        for event in m5_result.events
        if event.event_type == "LIQUIDITY_SWEPT"
        and event.direction == direction
        and window_start <= event.index <= latest_choch.index
    ]

    if not sweep_events:
        return None

    latest_sweep = max(sweep_events, key=lambda event: event.index)

    close_price = float(m5_result.candles["close"].iloc[-1])

    return {
        "passed": True,
        "close_price": close_price,
        "choch_index": latest_choch.index,
        "choch_time": latest_choch.time.isoformat(),
        "liquidity_sweep_index": latest_sweep.index,
        "liquidity_sweep_time": latest_sweep.time.isoformat(),
        "lookback_candles": lookback_candles,
    }


def _displacement_evidence(order_block: OrderBlock) -> dict[str, Any]:
    """Build the 'displacement' evidence entry from an Order Block."""

    atr = order_block.metadata.get("atr") if order_block.metadata else None
    atr_ratio = (
        order_block.displacement_distance / atr
        if atr is not None and atr > 0
        else None
    )

    return {
        "passed": True,
        "distance": order_block.displacement_distance,
        "distance_pips": order_block.displacement_distance_pips,
        "atr_ratio": atr_ratio,
        "start_index": order_block.displacement_start_index,
        "end_index": order_block.displacement_end_index,
    }


def _order_block_evidence(order_block: OrderBlock) -> dict[str, Any]:
    """Build the 'order_block' evidence entry from an Order Block."""

    return {
        "passed": True,
        "order_block_id": order_block.order_block_id,
        "order_block_type": order_block.order_block_type,
        "high": order_block.high,
        "low": order_block.low,
        "proximal_level": order_block.proximal_level,
        "distal_level": order_block.distal_level,
        "confirmation_status": order_block.confirmation_status,
        "source_event_type": order_block.source_event_type,
        "candle_index": order_block.candle_index,
    }


def _empty_evidence() -> dict[str, dict[str, Any]]:
    """The fixed evidence schema, with every step initialised empty."""

    return {
        "h4_bias": {},
        "h1_confirmation": {},
        "liquidity_sweep": {},
        "choch": {},
        "displacement": {},
        "order_block": {},
        "retracement": {},
        "m5_confirmation": {},
    }


def generate_eurusd_manual_signal(
    *,
    h4_count: int = DEFAULT_H4_COUNT,
    h1_count: int = DEFAULT_H1_COUNT,
    m15_count: int = DEFAULT_M15_COUNT,
    m5_count: int = DEFAULT_M5_COUNT,
    m5_lookback_candles: int = DEFAULT_M5_LOOKBACK_CANDLES,
    risk_percent: float = DEFAULT_RISK_PERCENT,
    minimum_risk_reward: float = DEFAULT_MINIMUM_RISK_REWARD,
    swing_options: Optional[Mapping[str, Any]] = None,
    structure_options: Optional[Mapping[str, Any]] = None,
    liquidity_options: Optional[Mapping[str, Any]] = None,
    order_block_options: Optional[Mapping[str, Any]] = None,
    candle_loader: Callable[[str, str, int], pd.DataFrame] = get_candles,
    account_info_provider: Callable[[], dict[str, Any]] = get_account_snapshot,
    position_count_provider: Callable[[str], int] = get_open_position_count,
    symbol_specs_provider: Callable[
        [str], dict[str, float]
    ] = get_symbol_trade_specs,
) -> dict[str, Any]:
    """
    Generate a manual-approval-only EURUSD trade proposal following
    the frozen ICT/SMC entry sequence:

        H4 bias -> H1 confirmation -> M15 liquidity sweep -> M15
        confirmed CHoCH -> M15 displacement -> M15 linked Order Block
        -> M15 retracement into the Order Block -> M5 execution
        confirmation -> risk validation.

    This function NEVER places an order. It only returns a trade
    proposal dict for a human to review and manually execute. No
    execution path (mt5.order_send or equivalent) exists anywhere in
    this module.

    Every market-structure computation is delegated to
    app.analysis.analysis_engine.analyze_market -- this module adds no
    new swing/BOS/MSS/CHoCH/liquidity/Order-Block detection logic. It
    only sequences existing engine output across four timeframes and
    applies the frozen risk/account/portfolio gates.

    Symbol is fixed to EURUSD (SYMBOL) -- not a parameter -- per the
    frozen strategy's "EURUSD only" rule.

    Parameters
    ----------
    h4_count, h1_count, m15_count, m5_count:
        Candle counts fetched per timeframe.
    m5_lookback_candles:
        Recency window (in M5 candles) for the M5 execution
        confirmation step. Must exceed the swing engine's right_bars
        for a confirmation to ever be structurally possible.
    risk_percent:
        Percentage of account balance risked per trade (frozen: 0.5).
    minimum_risk_reward:
        Minimum, and in this implementation exact, risk:reward
        multiple used to place the take-profit (frozen: 2.0).
    swing_options, structure_options, liquidity_options,
    order_block_options:
        Passed straight through to analyze_market() for every
        timeframe -- unset by default, meaning analyze_market's own
        defaults apply. Exposed primarily so tests can use smaller
        swing windows; production callers should rarely need to
        override these.
    candle_loader, account_info_provider, position_count_provider,
    symbol_specs_provider:
        Injected MT5-access callables (mirroring the existing
        dependency-injection pattern in
        app.strategies.multi_timeframe.analyse_multiple_timeframes),
        defaulting to the real MT5-backed functions in
        app.mt5.market. Tests substitute fakes here instead of
        requiring a live MT5 terminal.

    Returns
    -------
    A dict matching the frozen output schema: status, symbol,
    direction, entry, stop_loss, take_profit, risk_percent,
    risk_reward, position_size, confidence, evidence,
    rejection_reasons.
    """

    evidence = _empty_evidence()
    rejection_reasons: list[str] = []

    def _rejected(status: str) -> dict[str, Any]:
        return {
            "status": status,
            "symbol": SYMBOL,
            "direction": None,
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "risk_percent": risk_percent,
            "risk_reward": None,
            "position_size": None,
            "confidence": 0,
            "evidence": evidence,
            "rejection_reasons": rejection_reasons,
        }

    # -----------------------------------------------------------
    # Hard account/portfolio gates -- cheap, checked before any
    # candle fetch or analysis.
    # -----------------------------------------------------------

    account = account_info_provider()

    if account["trade_mode"] != "demo":
        rejection_reasons.append(
            f"Account trade_mode is '{account['trade_mode']}', not "
            "'demo'. Manual-signal generation is restricted to demo "
            "accounts."
        )
        return _rejected("BLOCKED")

    open_positions = position_count_provider(SYMBOL)

    if open_positions > 0:
        rejection_reasons.append(
            f"{open_positions} {SYMBOL} position(s) already open. "
            "Maximum one open EURUSD trade is permitted."
        )
        return _rejected("BLOCKED")

    # -----------------------------------------------------------
    # Step 1: H4 directional bias only.
    # -----------------------------------------------------------

    h4_candles = candle_loader(SYMBOL, "H4", h4_count)
    h4_result = analyze_market(
        symbol=SYMBOL,
        timeframe="H4",
        candles=h4_candles,
        swing_options=swing_options,
        structure_options=structure_options,
        liquidity_options=liquidity_options,
        order_block_options=order_block_options,
    )

    h4_bias = evaluate_h4_bias(h4_result)
    evidence["h4_bias"] = h4_bias

    if not h4_bias["passed"]:
        rejection_reasons.append(
            "No H4 directional bias (external_trend is neutral)."
        )
        return _rejected("NO_SETUP")

    direction = h4_bias["direction"]

    # -----------------------------------------------------------
    # Step 2: H1 confirmation only.
    # -----------------------------------------------------------

    h1_candles = candle_loader(SYMBOL, "H1", h1_count)
    h1_result = analyze_market(
        symbol=SYMBOL,
        timeframe="H1",
        candles=h1_candles,
        swing_options=swing_options,
        structure_options=structure_options,
        liquidity_options=liquidity_options,
        order_block_options=order_block_options,
    )

    h1_confirmation = evaluate_h1_confirmation(h1_result, direction)
    evidence["h1_confirmation"] = h1_confirmation

    if not h1_confirmation["passed"]:
        rejection_reasons.append(
            "H1 does not confirm the H4 directional bias."
        )
        return _rejected("NO_SETUP")

    # -----------------------------------------------------------
    # M15: confirmed CHoCH -> liquidity sweep before it ->
    # displacement/Order Block linked to it -> retracement into it.
    # -----------------------------------------------------------

    m15_candles = candle_loader(SYMBOL, "M15", m15_count)
    m15_result = analyze_market(
        symbol=SYMBOL,
        timeframe="M15",
        candles=m15_candles,
        swing_options=swing_options,
        structure_options=structure_options,
        liquidity_options=liquidity_options,
        order_block_options=order_block_options,
    )

    choch = evaluate_choch(m15_result, direction)

    if choch is None:
        rejection_reasons.append(
            f"No confirmed {direction} CHoCH found on M15 (an MSS "
            "alone is not sufficient)."
        )
        return _rejected("NO_SETUP")

    evidence["choch"] = choch

    liquidity_sweep = evaluate_liquidity_sweep(
        m15_result,
        direction,
        before_index=choch["index"],
    )

    if liquidity_sweep is None:
        rejection_reasons.append(
            f"No {direction}-direction liquidity sweep found on M15 "
            "before the confirmed CHoCH."
        )
        return _rejected("NO_SETUP")

    evidence["liquidity_sweep"] = liquidity_sweep

    order_block = evaluate_displacement_and_order_block(
        m15_result,
        direction,
        choch_index=choch["index"],
    )

    if order_block is None:
        rejection_reasons.append(
            f"No confirmed {direction} Order Block linked to the "
            "CHoCH's displacement was found on M15."
        )
        return _rejected("NO_SETUP")

    evidence["displacement"] = _displacement_evidence(order_block)
    evidence["order_block"] = _order_block_evidence(order_block)

    if not evaluate_retracement(order_block):
        evidence["retracement"] = {"passed": False}
        rejection_reasons.append(
            "Price has not yet retraced into the linked Order Block."
        )
        return _rejected("NO_SETUP")

    evidence["retracement"] = {
        "passed": True,
        "mitigated_index": order_block.mitigated_index,
        "mitigation_price": order_block.mitigation_price,
        "mitigation_percentage": order_block.mitigation_percentage,
    }

    # -----------------------------------------------------------
    # Step 8: M5 execution confirmation only.
    # -----------------------------------------------------------

    m5_candles = candle_loader(SYMBOL, "M5", m5_count)
    m5_result = analyze_market(
        symbol=SYMBOL,
        timeframe="M5",
        candles=m5_candles,
        swing_options=swing_options,
        structure_options=structure_options,
        liquidity_options=liquidity_options,
        order_block_options=order_block_options,
    )

    m5_confirmation = evaluate_m5_confirmation(
        m5_result,
        direction,
        m5_lookback_candles,
    )

    if m5_confirmation is None:
        rejection_reasons.append(
            f"No local {direction} liquidity sweep + confirmed CHoCH "
            f"found within the last {m5_lookback_candles} M5 candles."
        )
        return _rejected("NO_SETUP")

    evidence["m5_confirmation"] = m5_confirmation

    # -----------------------------------------------------------
    # Step 9: risk validation.
    # -----------------------------------------------------------

    entry_price = m5_confirmation["close_price"]
    stop_loss_price = order_block.distal_level

    try:
        levels = calculate_structural_trade_levels(
            signal="buy" if direction == "bullish" else "sell",
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            risk_reward_ratio=minimum_risk_reward,
        )
    except ValueError as error:
        rejection_reasons.append(
            f"Risk levels could not be computed: {error}"
        )
        return _rejected("BLOCKED")

    specs = symbol_specs_provider(SYMBOL)

    sizing = calculate_position_size(
        account_balance=account["balance"],
        risk_percent=risk_percent,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        tick_size=specs["tick_size"],
        tick_value=specs["tick_value"],
        volume_min=specs["volume_min"],
        volume_max=specs["volume_max"],
        volume_step=specs["volume_step"],
    )

    if not sizing["sufficient_size"]:
        rejection_reasons.append(
            "Computed position size rounds below the broker's "
            "minimum tradable volume."
        )
        return _rejected("BLOCKED")

    return {
        "status": "SIGNAL_PENDING_APPROVAL",
        "symbol": SYMBOL,
        "direction": _direction_label(direction),
        "entry": entry_price,
        "stop_loss": stop_loss_price,
        "take_profit": levels["take_profit"],
        "risk_percent": risk_percent,
        "risk_reward": levels["risk_reward_ratio"],
        "position_size": sizing["position_size"],
        "confidence": 100,
        "evidence": evidence,
        "rejection_reasons": [],
    }
