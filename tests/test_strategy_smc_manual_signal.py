"""
Tests for the frozen EURUSD manual-signal orchestration layer
(app.strategies.smc_manual_signal).

Unit tests exercise each evaluate_* step function against small,
hand-built engine objects (dataclasses from app.analysis.models) --
no MT5, no full analyze_market() run. Integration tests run the real
canonical engine (analyze_market) over deterministic, verified
multi-timeframe candle fixtures (tests/helpers/candles.py) to prove
the whole sequence wires together correctly end to end, for both
directions and every rejection path.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.analysis.analysis_engine import analyze_market
from app.analysis.models import AnalysisResult, MarketEvent, OrderBlock, StructureSnapshot
from app.risk.calculator import calculate_position_size, calculate_structural_trade_levels
from app.strategies.smc_manual_signal import (
    SYMBOL,
    evaluate_choch,
    evaluate_displacement_and_order_block,
    evaluate_entry_within_order_block,
    evaluate_h1_confirmation,
    evaluate_h4_bias,
    evaluate_liquidity_sweep,
    evaluate_m5_confirmation,
    evaluate_retracement,
    generate_eurusd_manual_signal,
)
from tests.helpers.candles import (
    build_ict_bias_candles,
    build_ict_m15_sequence_candles,
    build_ict_m5_sequence_candles,
    reflect_candles,
)


TIME = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _event(event_type, direction, index, **kwargs) -> MarketEvent:
    return MarketEvent(
        event_id=f"EV_{index}",
        event_type=event_type,
        time=TIME,
        index=index,
        direction=direction,
        **kwargs,
    )


def _order_block(
    *,
    order_block_type="bullish",
    confirmation_status="confirmed",
    invalidated=False,
    expired=False,
    mitigated=False,
    created_index=10,
    confirmed_index=None,
    distal_level=1.0900,
    proximal_level=1.0950,
    metadata=None,
) -> OrderBlock:
    # high/low are always the max/min of distal and proximal -- matching
    # order_blocks.py, where high/low are the source candle's literal
    # high/low, while proximal/distal are assigned per direction (see
    # evaluate_entry_within_order_block's docstring). Deriving them here
    # keeps bearish fixtures (proximal < distal) correctly oriented,
    # instead of requiring every caller to pre-swap the values by hand.
    return OrderBlock(
        order_block_id="OB_TEST",
        order_block_type=order_block_type,
        created_time=TIME,
        created_index=created_index,
        candle_time=TIME,
        candle_index=created_index - 1,
        high=max(distal_level, proximal_level),
        low=min(distal_level, proximal_level),
        open=distal_level,
        close=proximal_level,
        proximal_level=proximal_level,
        distal_level=distal_level,
        confirmation_status=confirmation_status,
        confirmed_index=confirmed_index,
        invalidated=invalidated,
        expired=expired,
        mitigated=mitigated,
        metadata=metadata or {},
    )


def _analysis_result(*, structure_snapshot=None, events=None, order_blocks=None, candles=None) -> AnalysisResult:
    return AnalysisResult(
        symbol=SYMBOL,
        timeframe="TEST",
        candles=candles if candles is not None else pd.DataFrame({"close": [1.1000]}),
        structure_snapshot=structure_snapshot,
        events=events or [],
        order_blocks=order_blocks or [],
    )


# ---------------------------------------------------------------
# Unit tests: evaluate_h4_bias
# ---------------------------------------------------------------


class TestEvaluateH4Bias:
    def test_bullish_bias_passes(self):
        snapshot = StructureSnapshot(external_trend="bullish", structure_state="bullish")
        result = evaluate_h4_bias(_analysis_result(structure_snapshot=snapshot))
        assert result["passed"] is True
        assert result["direction"] == "bullish"

    def test_bearish_bias_passes(self):
        snapshot = StructureSnapshot(external_trend="bearish", structure_state="bearish")
        result = evaluate_h4_bias(_analysis_result(structure_snapshot=snapshot))
        assert result["passed"] is True
        assert result["direction"] == "bearish"

    def test_neutral_bias_fails(self):
        snapshot = StructureSnapshot(external_trend="neutral", structure_state="neutral")
        result = evaluate_h4_bias(_analysis_result(structure_snapshot=snapshot))
        assert result["passed"] is False
        assert result["direction"] is None

    def test_missing_snapshot_fails(self):
        result = evaluate_h4_bias(_analysis_result(structure_snapshot=None))
        assert result["passed"] is False


# ---------------------------------------------------------------
# Unit tests: evaluate_h1_confirmation
# ---------------------------------------------------------------


class TestEvaluateH1Confirmation:
    def test_agreeing_trend_passes(self):
        snapshot = StructureSnapshot(external_trend="bullish", structure_state="bullish")
        result = evaluate_h1_confirmation(_analysis_result(structure_snapshot=snapshot), "bullish")
        assert result["passed"] is True

    def test_disagreeing_trend_fails(self):
        snapshot = StructureSnapshot(external_trend="bearish", structure_state="bearish")
        result = evaluate_h1_confirmation(_analysis_result(structure_snapshot=snapshot), "bullish")
        assert result["passed"] is False

    def test_neutral_h1_fails(self):
        snapshot = StructureSnapshot(external_trend="neutral", structure_state="neutral")
        result = evaluate_h1_confirmation(_analysis_result(structure_snapshot=snapshot), "bullish")
        assert result["passed"] is False


# ---------------------------------------------------------------
# Unit tests: evaluate_choch -- confirmed CHoCH only, MSS insufficient
# ---------------------------------------------------------------


class TestEvaluateChoch:
    def test_confirmed_choch_found(self):
        snapshot = StructureSnapshot(external_trend="bullish", structure_state="bullish")
        events = [_event("CHoCH", "bullish", 10, broken_level=1.09)]
        result = evaluate_choch(_analysis_result(structure_snapshot=snapshot, events=events), "bullish")
        assert result is not None
        assert result["index"] == 10

    def test_mss_alone_is_not_sufficient(self):
        """An MSS with no confirming CHoCH must never produce a signal."""

        snapshot = StructureSnapshot(external_trend="bearish", structure_state="mss_bullish")
        events = [_event("MSS", "bullish", 10, broken_level=1.09)]
        result = evaluate_choch(_analysis_result(structure_snapshot=snapshot, events=events), "bullish")
        assert result is None

    def test_stale_choch_superseded_by_opposite_trend_is_rejected(self):
        """If the current M15 trend no longer matches, an old CHoCH cannot anchor a live signal."""

        snapshot = StructureSnapshot(external_trend="bearish", structure_state="bearish")
        events = [
            _event("CHoCH", "bullish", 5, broken_level=1.09),
            _event("CHoCH", "bearish", 10, broken_level=1.10),
        ]
        result = evaluate_choch(_analysis_result(structure_snapshot=snapshot, events=events), "bullish")
        assert result is None

    def test_latest_matching_choch_is_selected(self):
        snapshot = StructureSnapshot(external_trend="bullish", structure_state="bullish")
        events = [
            _event("CHoCH", "bullish", 5, broken_level=1.09),
            _event("CHoCH", "bearish", 8, broken_level=1.10),
            _event("CHoCH", "bullish", 12, broken_level=1.095),
        ]
        result = evaluate_choch(_analysis_result(structure_snapshot=snapshot, events=events), "bullish")
        assert result is not None
        assert result["index"] == 12


# ---------------------------------------------------------------
# Unit tests: evaluate_liquidity_sweep
# ---------------------------------------------------------------


class TestEvaluateLiquiditySweep:
    def test_sweep_before_choch_found(self):
        events = [_event("LIQUIDITY_SWEPT", "bullish", 5, broken_level=1.09)]
        result = evaluate_liquidity_sweep(_analysis_result(events=events), "bullish", before_index=10)
        assert result is not None
        assert result["index"] == 5

    def test_sweep_after_choch_is_rejected(self):
        events = [_event("LIQUIDITY_SWEPT", "bullish", 15, broken_level=1.09)]
        result = evaluate_liquidity_sweep(_analysis_result(events=events), "bullish", before_index=10)
        assert result is None

    def test_wrong_direction_sweep_is_rejected(self):
        events = [_event("LIQUIDITY_SWEPT", "bearish", 5, broken_level=1.09)]
        result = evaluate_liquidity_sweep(_analysis_result(events=events), "bullish", before_index=10)
        assert result is None

    def test_no_sweep_at_all(self):
        result = evaluate_liquidity_sweep(_analysis_result(events=[]), "bullish", before_index=10)
        assert result is None

    def test_old_sweep_from_previous_cycle_is_rejected(self):
        """Finding 2: a sweep from an earlier, already-resolved cycle
        (before the previous cycle's own CHoCH) must not satisfy a
        later, unrelated CHoCH."""
        events = [
            _event("LIQUIDITY_SWEPT", "bullish", 2, broken_level=1.05),  # stale, from an earlier cycle
            _event("CHoCH", "bearish", 8, broken_level=1.07),  # previous cycle's own end
            _event("MSS", "bullish", 10, broken_level=1.09),  # this cycle's MSS
        ]
        result = evaluate_liquidity_sweep(_analysis_result(events=events), "bullish", before_index=15)
        assert result is None

    def test_sweep_inside_current_cycle_is_accepted(self):
        """Finding 2: a sweep occurring after the previous cycle's own
        end is accepted even though it precedes this cycle's own MSS
        -- the normal ICT order, where the sweep triggers the break."""
        events = [
            _event("LIQUIDITY_SWEPT", "bullish", 2, broken_level=1.05),  # stale, from an earlier cycle
            _event("CHoCH", "bearish", 8, broken_level=1.07),  # previous cycle's own end
            _event("LIQUIDITY_SWEPT", "bullish", 9, broken_level=1.08),  # belongs to this cycle
            _event("MSS", "bullish", 10, broken_level=1.09),
        ]
        result = evaluate_liquidity_sweep(_analysis_result(events=events), "bullish", before_index=15)
        assert result is not None
        assert result["index"] == 9


# ---------------------------------------------------------------
# Unit tests: evaluate_displacement_and_order_block
# ---------------------------------------------------------------


class TestEvaluateDisplacementAndOrderBlock:
    def test_choch_sourced_block_matched_by_created_index(self):
        block = _order_block(order_block_type="bullish", confirmation_status="confirmed", created_index=20)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is block

    def test_mss_promoted_block_matched_by_confirmed_index(self):
        block = _order_block(
            order_block_type="bearish",
            confirmation_status="confirmed",
            created_index=15,
            confirmed_index=20,
        )
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bearish", choch_index=20
        )
        assert result is block

    def test_still_provisional_block_is_rejected(self):
        block = _order_block(confirmation_status="provisional", created_index=20)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is None

    def test_invalidated_block_is_rejected(self):
        block = _order_block(invalidated=True, created_index=20)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is None

    def test_expired_block_is_rejected(self):
        block = _order_block(expired=True, created_index=20)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is None

    def test_wrong_direction_block_is_rejected(self):
        block = _order_block(order_block_type="bearish", created_index=20)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is None

    def test_mismatched_anchor_index_is_rejected(self):
        block = _order_block(created_index=19)
        result = evaluate_displacement_and_order_block(
            _analysis_result(order_blocks=[block]), "bullish", choch_index=20
        )
        assert result is None


# ---------------------------------------------------------------
# Unit tests: evaluate_retracement
# ---------------------------------------------------------------


class TestEvaluateRetracement:
    def test_mitigated_block_passes(self):
        block = _order_block(mitigated=True)
        assert evaluate_retracement(block) is True

    def test_not_yet_mitigated_fails(self):
        block = _order_block(mitigated=False)
        assert evaluate_retracement(block) is False

    def test_invalidated_block_fails_even_if_mitigated_flag_set(self):
        block = _order_block(mitigated=True, invalidated=True)
        assert evaluate_retracement(block) is False


# ---------------------------------------------------------------
# Unit tests: evaluate_entry_within_order_block (Finding 3)
#
# Bullish block: distal=1.0900 (low/far edge), proximal=1.0950
# (high/near edge, price approaches from above). ATR=0.0020 ->
# tolerance = 0.25 * 0.0020 = 0.0005.
#
# Bearish block: proximal=1.0950 (low/near edge, price approaches
# from below), distal=1.1000 (high/far edge). Same ATR/tolerance,
# mirrored.
# ---------------------------------------------------------------


class TestEvaluateEntryWithinOrderBlock:
    def test_entry_inside_order_block_is_accepted(self):
        block = _order_block(
            order_block_type="bullish",
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": 0.0020},
        )
        assert evaluate_entry_within_order_block(block, 1.0925) is True

    def test_entry_within_proximal_tolerance_is_accepted(self):
        block = _order_block(
            order_block_type="bullish",
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": 0.0020},
        )
        # 0.0003 beyond proximal (1.0950), within the 0.0005 tolerance.
        assert evaluate_entry_within_order_block(block, 1.0953) is True

    def test_entry_beyond_proximal_tolerance_is_rejected(self):
        block = _order_block(
            order_block_type="bullish",
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": 0.0020},
        )
        # 0.0010 beyond proximal (1.0950), past the 0.0005 tolerance.
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_entry_beyond_distal_edge_is_rejected(self):
        block = _order_block(
            order_block_type="bullish",
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": 0.0020},
        )
        # Only 0.0001 beyond distal (1.0900) -- well within what the
        # 0.0005 tolerance would allow if it were symmetric. Must
        # still be rejected: no tolerance exists on the distal side.
        assert evaluate_entry_within_order_block(block, 1.0899) is False

    def test_bearish_entry_inside_order_block_is_accepted(self):
        block = _order_block(
            order_block_type="bearish",
            proximal_level=1.0950,
            distal_level=1.1000,
            metadata={"atr": 0.0020},
        )
        assert evaluate_entry_within_order_block(block, 1.0975) is True

    def test_bearish_entry_within_proximal_tolerance_is_accepted(self):
        block = _order_block(
            order_block_type="bearish",
            proximal_level=1.0950,
            distal_level=1.1000,
            metadata={"atr": 0.0020},
        )
        # 0.0003 below proximal (1.0950), within the 0.0005 tolerance.
        assert evaluate_entry_within_order_block(block, 1.0947) is True

    def test_bearish_entry_beyond_proximal_tolerance_is_rejected(self):
        block = _order_block(
            order_block_type="bearish",
            proximal_level=1.0950,
            distal_level=1.1000,
            metadata={"atr": 0.0020},
        )
        # 0.0010 below proximal (1.0950), past the 0.0005 tolerance.
        assert evaluate_entry_within_order_block(block, 1.0940) is False

    def test_bearish_entry_beyond_distal_edge_is_rejected(self):
        block = _order_block(
            order_block_type="bearish",
            proximal_level=1.0950,
            distal_level=1.1000,
            metadata={"atr": 0.0020},
        )
        # Only 0.0001 beyond distal (1.1000) -- well within what the
        # 0.0005 tolerance would allow if it were symmetric. Must
        # still be rejected: no tolerance exists on the distal side.
        assert evaluate_entry_within_order_block(block, 1.1001) is False


# ---------------------------------------------------------------
# Unit tests: evaluate_entry_within_order_block ATR-validation
# hardening.
#
# entry_price=1.0960 lies outside the Order Block's own range
# (order_block.low=1.0900, high=1.0950) and would only ever be
# accepted via the proximal-edge tolerance branch -- so any of these
# invalid-ATR cases returning True (or raising) would mean the guard
# failed to fail closed.
# ---------------------------------------------------------------


class TestEvaluateEntryWithinOrderBlockAtrSafety:
    def test_none_atr_fails_closed(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": None},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_nan_atr_fails_closed(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": float("nan")},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_positive_infinity_atr_fails_closed(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": float("inf")},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_string_atr_fails_closed_without_raising(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": "not-a-number"},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_zero_atr_fails_closed(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": 0.0},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False

    def test_negative_atr_fails_closed(self):
        block = _order_block(
            distal_level=1.0900,
            proximal_level=1.0950,
            metadata={"atr": -0.0020},
        )
        assert evaluate_entry_within_order_block(block, 1.0960) is False


# ---------------------------------------------------------------
# Unit tests: evaluate_m5_confirmation
# ---------------------------------------------------------------


class TestEvaluateM5Confirmation:
    def _candles(self, count: int) -> pd.DataFrame:
        return pd.DataFrame({"close": [1.1000 + 0.0001 * i for i in range(count)]})

    def test_sweep_and_choch_within_window_pass(self):
        events = [
            _event("LIQUIDITY_SWEPT", "bullish", 16),
            _event("CHoCH", "bullish", 18),
        ]
        result = _analysis_result(events=events, candles=self._candles(20))
        confirmation = evaluate_m5_confirmation(result, "bullish", lookback_candles=5)
        assert confirmation is not None
        assert confirmation["close_price"] == pytest.approx(1.1000 + 0.0001 * 19)

    def test_choch_outside_window_fails(self):
        events = [
            _event("LIQUIDITY_SWEPT", "bullish", 5),
            _event("CHoCH", "bullish", 8),
        ]
        result = _analysis_result(events=events, candles=self._candles(20))
        assert evaluate_m5_confirmation(result, "bullish", lookback_candles=5) is None

    def test_choch_within_window_but_no_sweep_fails(self):
        events = [_event("CHoCH", "bullish", 18)]
        result = _analysis_result(events=events, candles=self._candles(20))
        assert evaluate_m5_confirmation(result, "bullish", lookback_candles=5) is None

    def test_sweep_within_window_but_no_choch_fails(self):
        events = [_event("LIQUIDITY_SWEPT", "bullish", 18)]
        result = _analysis_result(events=events, candles=self._candles(20))
        assert evaluate_m5_confirmation(result, "bullish", lookback_candles=5) is None

    def test_empty_candles_fails(self):
        result = _analysis_result(events=[], candles=pd.DataFrame({"close": []}))
        assert evaluate_m5_confirmation(result, "bullish", lookback_candles=5) is None


# ---------------------------------------------------------------
# Unit tests: risk helpers (RR floor + position sizing)
# ---------------------------------------------------------------


class TestCalculateStructuralTradeLevels:
    def test_buy_take_profit_at_exact_minimum_rr(self):
        levels = calculate_structural_trade_levels("buy", entry_price=1.1000, stop_loss_price=1.0950, risk_reward_ratio=2.0)
        assert levels["take_profit"] == pytest.approx(1.1100)
        assert levels["risk_reward_ratio"] == 2.0

    def test_sell_take_profit_at_exact_minimum_rr(self):
        levels = calculate_structural_trade_levels("sell", entry_price=1.1000, stop_loss_price=1.1050, risk_reward_ratio=2.0)
        assert levels["take_profit"] == pytest.approx(1.0900)

    def test_buy_stop_above_entry_is_rejected(self):
        with pytest.raises(ValueError):
            calculate_structural_trade_levels("buy", entry_price=1.1000, stop_loss_price=1.1050)

    def test_sell_stop_below_entry_is_rejected(self):
        with pytest.raises(ValueError):
            calculate_structural_trade_levels("sell", entry_price=1.1000, stop_loss_price=1.0950)


class TestCalculatePositionSize:
    def test_known_arithmetic(self):
        sizing = calculate_position_size(
            account_balance=10000.0,
            risk_percent=0.5,
            entry_price=1.1000,
            stop_loss_price=1.0950,
            tick_size=0.00001,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        # risk_amount=50; stop_distance=0.0050; value_per_unit=100000
        # raw = 50 / (0.0050 * 100000) = 0.10
        assert sizing["sufficient_size"] is True
        assert sizing["position_size"] == pytest.approx(0.10)

    def test_rounds_down_to_volume_step(self):
        sizing = calculate_position_size(
            account_balance=10000.0,
            risk_percent=0.5,
            entry_price=1.11005,
            stop_loss_price=1.0995,
            tick_size=0.00001,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        assert sizing["position_size"] == pytest.approx(0.04)

    def test_below_minimum_volume_is_insufficient(self):
        sizing = calculate_position_size(
            account_balance=100.0,
            risk_percent=0.5,
            entry_price=1.1000,
            stop_loss_price=1.0000,
            tick_size=0.00001,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        assert sizing["sufficient_size"] is False
        assert sizing["position_size"] == 0.0

    def test_equal_entry_and_stop_is_rejected(self):
        with pytest.raises(ValueError):
            calculate_position_size(
                account_balance=10000.0,
                risk_percent=0.5,
                entry_price=1.1000,
                stop_loss_price=1.1000,
                tick_size=0.00001,
                tick_value=1.0,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
            )


# ---------------------------------------------------------------
# Integration tests: full orchestrator, real engine, verified fixtures
# ---------------------------------------------------------------


class _FakeMT5:
    """Injected MT5-access stand-ins -- no live terminal required."""

    def __init__(
        self,
        *,
        h4_h1_candles: pd.DataFrame,
        m15_candles: pd.DataFrame,
        m5_candles: pd.DataFrame,
        trade_mode: str = "demo",
        open_positions: int = 0,
        balance: float = 10000.0,
    ):
        self.h4_h1_candles = h4_h1_candles
        self.m15_candles = m15_candles
        self.m5_candles = m5_candles
        self.trade_mode = trade_mode
        self.open_positions = open_positions
        self.balance = balance

    def candle_loader(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        return {
            "H4": self.h4_h1_candles,
            "H1": self.h4_h1_candles,
            "M15": self.m15_candles,
            "M5": self.m5_candles,
        }[timeframe]

    def account_info_provider(self) -> dict:
        return {
            "balance": self.balance,
            "equity": self.balance,
            "currency": "USD",
            "trade_mode": self.trade_mode,
        }

    def position_count_provider(self, symbol: str) -> int:
        return self.open_positions

    def symbol_specs_provider(self, symbol: str) -> dict:
        return {
            "contract_size": 100000.0,
            "tick_size": 0.00001,
            "tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }


def _shift_price(candles: pd.DataFrame, delta: float) -> pd.DataFrame:
    """
    Apply a uniform price shift to a candle series (open/high/low/
    close). Every swing/ATR/displacement relationship in the analysis
    engine depends only on price *differences*, so a uniform shift
    changes nothing structurally -- it only moves the absolute price
    level. Used below to re-anchor the M5 fixture's own natural entry
    price back inside the Finding 3 Order Block tolerance, without
    touching the shared, independently-verified fixture builders in
    tests/helpers/candles.py.
    """

    shifted = candles.copy()

    for column in ("open", "high", "low", "close"):
        shifted[column] = shifted[column] + delta

    return shifted


@pytest.fixture
def bullish_fixtures():
    # build_ict_m5_sequence_candles()'s own natural entry price lands
    # ~15.5 pips beyond the M15 Order Block's proximal edge (Finding
    # 3) -- shifted here, uniformly, back inside the Order Block's
    # 0.25 ATR tolerance band, so this fixture represents a genuinely
    # valid ICT setup under evaluate_entry_within_order_block. See
    # TestEntryZoneFinding3 for tests against the *unshifted* fixture.
    return {
        "h4_h1": build_ict_bias_candles(),
        "m15": build_ict_m15_sequence_candles(),
        "m5": _shift_price(build_ict_m5_sequence_candles(), -0.0050),
    }


@pytest.fixture
def bearish_fixtures(bullish_fixtures):
    return {
        "h4_h1": reflect_candles(bullish_fixtures["h4_h1"], center=1.0975),
        "m15": reflect_candles(bullish_fixtures["m15"], center=1.1035),
        "m5": reflect_candles(bullish_fixtures["m5"], center=1.1035),
    }


def _generate(fixtures: dict, **overrides):
    fake = _FakeMT5(
        h4_h1_candles=fixtures["h4_h1"],
        m15_candles=fixtures["m15"],
        m5_candles=fixtures["m5"],
        **{k: v for k, v in overrides.items() if k in {"trade_mode", "open_positions", "balance"}},
    )
    return generate_eurusd_manual_signal(
        swing_options={"left_bars": 1, "right_bars": 1},
        candle_loader=fake.candle_loader,
        account_info_provider=fake.account_info_provider,
        position_count_provider=fake.position_count_provider,
        symbol_specs_provider=fake.symbol_specs_provider,
    )


class TestGenerateEurusdManualSignalIntegration:
    def test_full_buy_sequence_produces_pending_signal(self, bullish_fixtures):
        result = _generate(bullish_fixtures)

        assert result["status"] == "SIGNAL_PENDING_APPROVAL"
        assert result["symbol"] == "EURUSD"
        assert result["direction"] == "BUY"
        assert result["risk_percent"] == 0.5
        assert result["risk_reward"] == pytest.approx(2.0)
        assert result["stop_loss"] < result["entry"] < result["take_profit"]
        assert result["position_size"] > 0
        assert result["confidence"] == 100
        assert result["rejection_reasons"] == []

        for step in (
            "h4_bias",
            "h1_confirmation",
            "liquidity_sweep",
            "choch",
            "displacement",
            "order_block",
            "retracement",
            "m5_confirmation",
        ):
            assert result["evidence"][step].get("passed") is True

    def test_full_sell_sequence_produces_pending_signal(self, bearish_fixtures):
        result = _generate(bearish_fixtures)

        assert result["status"] == "SIGNAL_PENDING_APPROVAL"
        assert result["direction"] == "SELL"
        assert result["risk_reward"] == pytest.approx(2.0)
        assert result["take_profit"] < result["entry"] < result["stop_loss"]
        assert result["position_size"] > 0

    def test_never_places_an_order(self, bullish_fixtures):
        """No code path in this module may ever call an execution primitive."""

        import app.strategies.smc_manual_signal as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "order_send(" not in source
        assert "MetaTrader5" not in source

        result = _generate(bullish_fixtures)
        assert result["status"] == "SIGNAL_PENDING_APPROVAL"
        # The schema itself never carries an order ticket / execution
        # confirmation field -- only a proposal for manual review.
        assert "ticket" not in result
        assert "executed" not in result

    def test_non_demo_account_is_blocked_before_any_analysis(self, bullish_fixtures):
        result = _generate(bullish_fixtures, trade_mode="real")

        assert result["status"] == "BLOCKED"
        assert result["rejection_reasons"]
        assert "demo" in result["rejection_reasons"][0].lower()
        # No market analysis should have been attempted.
        assert result["evidence"]["h4_bias"] == {}

    def test_existing_open_position_blocks_a_new_signal(self, bullish_fixtures):
        result = _generate(bullish_fixtures, open_positions=1)

        assert result["status"] == "BLOCKED"
        assert result["rejection_reasons"]
        assert "one open" in result["rejection_reasons"][0].lower() or "already open" in result["rejection_reasons"][0].lower()

    def test_h4_neutral_bias_yields_no_setup(self, bullish_fixtures):
        neutral_h4 = bullish_fixtures["h4_h1"].copy()
        # Flatten the series so no swing ever classifies with a
        # direction: keep prices constant (still passes candle
        # validation; open==high==low==close is permitted).
        neutral_h4[["open", "high", "low", "close"]] = 1.1000

        result = _generate({**bullish_fixtures, "h4_h1": neutral_h4})

        assert result["status"] == "NO_SETUP"
        assert result["evidence"]["h4_bias"]["passed"] is False

    def test_h1_disagreement_yields_no_setup(self, bullish_fixtures):
        h1_bearish = reflect_candles(bullish_fixtures["h4_h1"], center=1.0975)

        fake = _FakeMT5(
            h4_h1_candles=bullish_fixtures["h4_h1"],
            m15_candles=bullish_fixtures["m15"],
            m5_candles=bullish_fixtures["m5"],
        )

        def candle_loader(symbol, timeframe, count):
            if timeframe == "H1":
                return h1_bearish
            return fake.candle_loader(symbol, timeframe, count)

        result = generate_eurusd_manual_signal(
            swing_options={"left_bars": 1, "right_bars": 1},
            candle_loader=candle_loader,
            account_info_provider=fake.account_info_provider,
            position_count_provider=fake.position_count_provider,
            symbol_specs_provider=fake.symbol_specs_provider,
        )

        assert result["status"] == "NO_SETUP"
        assert result["evidence"]["h1_confirmation"]["passed"] is False

    def test_missing_m15_choch_yields_no_setup(self, bullish_fixtures):
        # Truncate the M15 fixture before the CHoCH candle -- the
        # sequence never confirms a reversal.
        truncated_m15 = bullish_fixtures["m15"].iloc[:14].copy()

        result = _generate({**bullish_fixtures, "m15": truncated_m15})

        assert result["status"] == "NO_SETUP"
        assert result["evidence"]["choch"] == {}

    def test_missing_m5_confirmation_yields_no_setup(self, bullish_fixtures):
        # Truncate the M5 fixture before its CHoCH candle.
        truncated_m5 = bullish_fixtures["m5"].iloc[:15].copy()

        result = _generate({**bullish_fixtures, "m5": truncated_m5})

        assert result["status"] == "NO_SETUP"
        assert result["evidence"]["m5_confirmation"] == {}
        # Everything through retracement must already have passed.
        assert result["evidence"]["retracement"]["passed"] is True

    def test_mss_without_confirming_choch_never_signals(self, bullish_fixtures):
        """
        The M15 fixture up to (but excluding) the CHoCH candle contains
        a fired MSS with no confirming CHoCH -- this must never be
        mistaken for a valid entry.
        """

        up_to_mss_only = bullish_fixtures["m15"].iloc[:16].copy()

        result = _generate({**bullish_fixtures, "m15": up_to_mss_only})

        assert result["status"] == "NO_SETUP"
        assert result["evidence"]["choch"] == {}


class TestEntryZoneFinding3:
    """
    Finding 3: the M5-derived entry price must remain anchored to the
    M15 Order Block supplying the stop-loss. Uses the real engine
    (analyze_market) end to end, not hand-built stubs.
    """

    def test_unshifted_golden_path_fixture_is_now_rejected(self):
        """
        The *original*, unmodified build_ict_m5_sequence_candles()
        fixture -- what the golden-path tests relied on before this
        fix -- produces an entry price ~15.5 pips beyond the M15
        Order Block's proximal edge, well past the 0.25 ATR tolerance.
        Before this fix this fixture produced SIGNAL_PENDING_APPROVAL
        undetected; it must now be correctly rejected.
        """

        fixtures = {
            "h4_h1": build_ict_bias_candles(),
            "m15": build_ict_m15_sequence_candles(),
            "m5": build_ict_m5_sequence_candles(),  # unshifted
        }

        result = _generate(fixtures)

        assert result["status"] == "NO_SETUP"
        assert any(
            "drifted outside the M15 Order Block" in reason
            for reason in result["rejection_reasons"]
        )

    def test_deliberately_shifted_m5_counterexample_is_rejected(self, bullish_fixtures):
        """A larger, deliberate M5 price drift must also be rejected."""

        shifted_m5 = _shift_price(bullish_fixtures["m5"], 0.0500)

        result = _generate({**bullish_fixtures, "m5": shifted_m5})

        assert result["status"] == "NO_SETUP"
        assert any(
            "drifted outside the M15 Order Block" in reason
            for reason in result["rejection_reasons"]
        )

    def test_corrected_golden_path_entry_is_confirmed_within_order_block(self, bullish_fixtures):
        """The corrected bullish_fixtures fixture's entry must fall
        inside the selected M15 Order Block's own range."""

        result = _generate(bullish_fixtures)

        assert result["status"] == "SIGNAL_PENDING_APPROVAL"

        ob = result["evidence"]["order_block"]
        assert ob["low"] <= result["entry"] <= ob["high"]

    def test_corrected_bearish_golden_path_entry_is_confirmed_within_order_block(self, bearish_fixtures):
        """BUY/SELL mirror of the above, via the reflected fixtures."""

        result = _generate(bearish_fixtures)

        assert result["status"] == "SIGNAL_PENDING_APPROVAL"

        ob = result["evidence"]["order_block"]
        assert ob["low"] <= result["entry"] <= ob["high"]


class TestAnalyzeMarketWiringSanityCheck:
    """
    Confirms the fixtures used above genuinely exercise
    analyze_market() (the real, unmodified canonical engine) rather
    than some reimplementation -- guards against a future refactor
    accidentally bypassing it.
    """

    def test_m15_fixture_produces_expected_engine_events(self, bullish_fixtures):
        result = analyze_market(
            symbol="EURUSD",
            timeframe="M15",
            candles=bullish_fixtures["m15"],
            swing_options={"left_bars": 1, "right_bars": 1},
        )
        event_types = [event.event_type for event in result.events]
        assert "LIQUIDITY_SWEPT" in event_types
        assert "MSS" in event_types
        assert "CHoCH" in event_types
        assert "ORDER_BLOCK_CREATED" in event_types
