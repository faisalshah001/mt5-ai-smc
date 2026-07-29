import math
from typing import Any


def calculate_trade_levels(
    signal: str,
    entry_price: float,
    atr: float,
    stop_loss_atr_multiplier: float = 1.5,
    risk_reward_ratio: float = 2.0,
) -> dict[str, Any]:
    """
    Calculate stop-loss and take-profit levels using ATR.

    Parameters:
    - signal: buy or sell
    - entry_price: proposed trade entry price
    - atr: current Average True Range value
    - stop_loss_atr_multiplier: ATR distance used for stop loss
    - risk_reward_ratio: target reward compared with risk
    """

    clean_signal = signal.strip().lower()

    if clean_signal not in {"buy", "sell"}:
        raise ValueError(
            "Signal must be either 'buy' or 'sell'."
        )

    if entry_price <= 0:
        raise ValueError(
            "Entry price must be greater than zero."
        )

    if atr <= 0:
        raise ValueError(
            "ATR must be greater than zero."
        )

    if stop_loss_atr_multiplier <= 0:
        raise ValueError(
            "Stop-loss ATR multiplier must be greater than zero."
        )

    if risk_reward_ratio <= 0:
        raise ValueError(
            "Risk-reward ratio must be greater than zero."
        )

    stop_distance = atr * stop_loss_atr_multiplier
    target_distance = stop_distance * risk_reward_ratio

    if clean_signal == "buy":
        stop_loss = entry_price - stop_distance
        take_profit = entry_price + target_distance
    else:
        stop_loss = entry_price + stop_distance
        take_profit = entry_price - target_distance

    return {
        "signal": clean_signal,
        "entry_price": entry_price,
        "atr": atr,
        "stop_loss_atr_multiplier": stop_loss_atr_multiplier,
        "risk_reward_ratio": risk_reward_ratio,
        "stop_distance": stop_distance,
        "target_distance": target_distance,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def calculate_structural_trade_levels(
    signal: str,
    entry_price: float,
    stop_loss_price: float,
    risk_reward_ratio: float = 2.0,
) -> dict[str, Any]:
    """
    Calculate a take-profit level from a structurally-derived
    stop-loss (for example, a Smart Money Concepts Order Block's
    distal level), enforcing a minimum risk:reward ratio.

    Unlike calculate_trade_levels (which derives its stop distance
    from ATR), this function takes the stop-loss price directly, so
    the caller can anchor risk to market structure instead of
    volatility.

    Parameters:
    - signal: buy or sell
    - entry_price: proposed trade entry price
    - stop_loss_price: structurally-derived stop-loss price
    - risk_reward_ratio: minimum reward-to-risk multiple; the
      take-profit is placed at exactly this multiple of the
      entry-to-stop distance
    """

    clean_signal = signal.strip().lower()

    if clean_signal not in {"buy", "sell"}:
        raise ValueError(
            "Signal must be either 'buy' or 'sell'."
        )

    if entry_price <= 0:
        raise ValueError(
            "Entry price must be greater than zero."
        )

    if stop_loss_price <= 0:
        raise ValueError(
            "Stop-loss price must be greater than zero."
        )

    if risk_reward_ratio <= 0:
        raise ValueError(
            "Risk-reward ratio must be greater than zero."
        )

    if clean_signal == "buy":
        if stop_loss_price >= entry_price:
            raise ValueError(
                "Stop-loss must be below entry price for a buy "
                "signal."
            )

        stop_distance = entry_price - stop_loss_price
        take_profit = entry_price + stop_distance * risk_reward_ratio
    else:
        if stop_loss_price <= entry_price:
            raise ValueError(
                "Stop-loss must be above entry price for a sell "
                "signal."
            )

        stop_distance = stop_loss_price - entry_price
        take_profit = entry_price - stop_distance * risk_reward_ratio

    return {
        "signal": clean_signal,
        "entry_price": entry_price,
        "stop_loss": stop_loss_price,
        "take_profit": take_profit,
        "stop_distance": stop_distance,
        "risk_reward_ratio": risk_reward_ratio,
    }


def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
    tick_size: float,
    tick_value: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> dict[str, Any]:
    """
    Calculate a broker-compliant lot size that risks exactly
    risk_percent of account_balance, given a structural stop distance
    and the symbol's trade specification.

    Parameters:
    - account_balance: current account balance
    - risk_percent: percentage of account_balance to risk (0-100)
    - entry_price, stop_loss_price: used only to derive stop_distance
    - tick_size: minimum price increment for the symbol
    - tick_value: monetary value of one tick_size move for one lot,
      in the account currency (already reflects the symbol's contract
      size, per MT5's own tick_value definition -- no separate
      contract_size term is needed in this formula)
    - volume_min, volume_max, volume_step: broker-enforced lot
      constraints

    The computed lot size is rounded down to the nearest volume_step
    and clamped to [volume_min, volume_max]. If the risk-correct size
    rounds below volume_min, the trade cannot be sized within the risk
    budget at this stop distance -- the caller must treat this as a
    blocked signal, not silently trade an oversized lot.
    """

    if account_balance <= 0:
        raise ValueError(
            "Account balance must be greater than zero."
        )

    if not 0 < risk_percent <= 100:
        raise ValueError(
            "risk_percent must be greater than zero and at most 100."
        )

    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError(
            "entry_price and stop_loss_price must be greater than "
            "zero."
        )

    if tick_size <= 0:
        raise ValueError("tick_size must be greater than zero.")

    if tick_value <= 0:
        raise ValueError("tick_value must be greater than zero.")

    if volume_step <= 0:
        raise ValueError("volume_step must be greater than zero.")

    if volume_min <= 0 or volume_max <= 0 or volume_min > volume_max:
        raise ValueError(
            "volume_min and volume_max must be positive, with "
            "volume_min <= volume_max."
        )

    stop_distance = abs(entry_price - stop_loss_price)

    if stop_distance <= 0:
        raise ValueError(
            "entry_price and stop_loss_price cannot be equal."
        )

    risk_amount = account_balance * (risk_percent / 100.0)
    value_per_unit_distance = tick_value / tick_size
    raw_position_size = risk_amount / (
        stop_distance * value_per_unit_distance
    )

    # A small epsilon guards against floating-point representation
    # error rounding a value like "30.0 steps" down to 29 steps.
    steps = math.floor(raw_position_size / volume_step + 1e-9)
    stepped_position_size = round(steps * volume_step, 8)

    if stepped_position_size < volume_min:
        return {
            "risk_amount": risk_amount,
            "raw_position_size": raw_position_size,
            "position_size": 0.0,
            "sufficient_size": False,
        }

    position_size = min(stepped_position_size, volume_max)

    return {
        "risk_amount": risk_amount,
        "raw_position_size": raw_position_size,
        "position_size": position_size,
        "sufficient_size": True,
    }