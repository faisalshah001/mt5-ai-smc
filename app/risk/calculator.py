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