from contextlib import asynccontextmanager
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from app.analysis.market_structure import (
    classify_market_structure,
    detect_breaks_of_structure,
    detect_change_of_character,
    detect_swing_points,
)
from app.indicators.technical import calculate_indicators
from app.mt5.connection import connect_mt5, disconnect_mt5
from app.mt5.market import get_candles
from app.risk.calculator import calculate_trade_levels
from app.strategies.multi_timeframe import analyse_multiple_timeframes
from app.strategies.trend import analyse_trend


@asynccontextmanager
async def lifespan(app: FastAPI):
    connect_mt5()

    yield

    disconnect_mt5()


app = FastAPI(
    title="MT5 AI Bridge",
    description="Read-only local API for MT5, n8n, and Claude.",
    version="1.3.0",
    lifespan=lifespan,
)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "status": "online",
        "message": "MT5 AI Bridge is running",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    terminal = mt5.terminal_info()
    account = mt5.account_info()

    return {
        "api_status": "online",
        "mt5_connected": terminal is not None,
        "account_connected": account is not None,
    }


@app.get("/account")
def get_account() -> dict[str, Any]:
    account = mt5.account_info()

    if account is None:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read account: {mt5.last_error()}",
        )

    return {
        "login": account.login,
        "server": account.server,
        "currency": account.currency,
        "balance": account.balance,
        "equity": account.equity,
        "profit": account.profit,
        "margin": account.margin,
        "margin_free": account.margin_free,
        "margin_level": account.margin_level,
        "leverage": account.leverage,
        "trade_allowed": account.trade_allowed,
    }


@app.get("/positions")
def get_positions() -> dict[str, Any]:
    positions = mt5.positions_get()

    if positions is None:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read positions: {mt5.last_error()}",
        )

    results = []

    for position in positions:
        results.append(
            {
                "ticket": position.ticket,
                "symbol": position.symbol,
                "type": (
                    "BUY"
                    if position.type == mt5.POSITION_TYPE_BUY
                    else "SELL"
                ),
                "volume": position.volume,
                "price_open": position.price_open,
                "price_current": position.price_current,
                "stop_loss": position.sl,
                "take_profit": position.tp,
                "profit": position.profit,
                "comment": position.comment,
            }
        )

    return {
        "count": len(results),
        "positions": results,
    }


@app.get("/candles/{symbol}/{timeframe}")
def candles_endpoint(
    symbol: str,
    timeframe: str,
    count: int = Query(default=250, ge=50, le=1000),
) -> dict[str, Any]:
    try:
        frame = get_candles(symbol, timeframe, count)
        frame = calculate_indicators(frame)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    symbol = symbol.strip().upper()
    timeframe = timeframe.strip().upper()

    frame["time"] = frame["time"].astype(str)
    frame = frame.astype(object).where(
        pd.notnull(frame),
        None,
    )

    latest = frame.iloc[-1]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(frame),
        "latest_indicators": {
            "close": latest["close"],
            "ema20": latest["ema20"],
            "ema50": latest["ema50"],
            "ema200": latest["ema200"],
            "rsi14": latest["rsi14"],
            "macd": latest["macd"],
            "macd_signal": latest["macd_signal"],
            "macd_histogram": latest["macd_histogram"],
            "atr14": latest["atr14"],
        },
        "candles": frame.to_dict(orient="records"),
    }


@app.get("/strategy/trend/{symbol}/{timeframe}")
def trend_strategy_endpoint(
    symbol: str,
    timeframe: str,
    count: int = Query(default=250, ge=200, le=1000),
) -> dict[str, Any]:
    try:
        frame = get_candles(symbol, timeframe, count)
        frame = calculate_indicators(frame)
        analysis = analyse_trend(frame)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    return {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "analysis": analysis,
    }


@app.get("/strategy/multi-timeframe/{symbol}")
def multi_timeframe_strategy_endpoint(
    symbol: str,
    count: int = Query(default=250, ge=200, le=1000),
) -> dict[str, Any]:
    try:
        analysis = analyse_multiple_timeframes(
            symbol=symbol,
            timeframes=["H1", "H4", "D1"],
            candle_loader=get_candles,
            indicator_calculator=calculate_indicators,
            trend_analyser=analyse_trend,
            count=count,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    return analysis


@app.get("/risk/trade-levels")
def trade_levels_endpoint(
    signal: str,
    entry_price: float,
    atr: float,
    stop_loss_atr_multiplier: float = Query(
        default=1.5,
        gt=0,
    ),
    risk_reward_ratio: float = Query(
        default=2.0,
        gt=0,
    ),
) -> dict[str, Any]:
    try:
        levels = calculate_trade_levels(
            signal=signal,
            entry_price=entry_price,
            atr=atr,
            stop_loss_atr_multiplier=stop_loss_atr_multiplier,
            risk_reward_ratio=risk_reward_ratio,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    return levels


@app.get("/analysis/market-structure/{symbol}/{timeframe}")
def market_structure_endpoint(
    symbol: str,
    timeframe: str,
    count: int = Query(default=200, ge=50, le=2000),
    left_bars: int = Query(default=3, ge=1, le=20),
    right_bars: int = Query(default=3, ge=1, le=20),
    minimum_break_atr: float = Query(
        default=0.10,
        ge=0,
        le=5,
    ),
) -> dict[str, Any]:
    try:
        candles = get_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=count,
        )

        candles = calculate_indicators(candles)

        structure = detect_swing_points(
            candles,
            left_bars=left_bars,
            right_bars=right_bars,
        )

        structure = classify_market_structure(structure)

        structure = detect_breaks_of_structure(
            structure,
            minimum_break_atr=minimum_break_atr,
        )

        result = detect_change_of_character(structure)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    swing_points = result.loc[
        result["swing_high"] | result["swing_low"],
        [
            "time",
            "high",
            "low",
            "swing_high",
            "swing_low",
            "structure",
        ],
    ].tail(20).copy()

    bos_events = result.loc[
        result["bos"].notna(),
        [
            "time",
            "close",
            "bos",
            "broken_level",
            "break_distance",
            "required_break_distance",
        ],
    ].tail(20).copy()

    choch_events = result.loc[
        result["choch"].notna(),
        [
            "time",
            "close",
            "bos",
            "choch",
            "broken_level",
        ],
    ].tail(20).copy()

    swing_points["time"] = swing_points["time"].astype(str)
    bos_events["time"] = bos_events["time"].astype(str)
    choch_events["time"] = choch_events["time"].astype(str)

    swing_points = swing_points.astype(object).where(
        pd.notnull(swing_points),
        None,
    )

    bos_events = bos_events.astype(object).where(
        pd.notnull(bos_events),
        None,
    )

    choch_events = choch_events.astype(object).where(
        pd.notnull(choch_events),
        None,
    )

    return {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "settings": {
            "count": count,
            "left_bars": left_bars,
            "right_bars": right_bars,
            "minimum_break_atr": minimum_break_atr,
        },
        "summary": {
            "swing_highs": int(
                result["swing_high"].sum()
            ),
            "swing_lows": int(
                result["swing_low"].sum()
            ),
            "bullish_bos": int(
                (result["bos"] == "bullish").sum()
            ),
            "bearish_bos": int(
                (result["bos"] == "bearish").sum()
            ),
            "bullish_choch": int(
                (result["choch"] == "bullish").sum()
            ),
            "bearish_choch": int(
                (result["choch"] == "bearish").sum()
            ),
        },
        "swing_points": swing_points.to_dict(
            orient="records",
        ),
        "bos_events": bos_events.to_dict(
            orient="records",
        ),
        "choch_events": choch_events.to_dict(
            orient="records",
        ),
    }