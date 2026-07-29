import logging
from contextlib import asynccontextmanager
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.analysis.analysis_engine import analyze_market
from app.analysis.candle_validation import validate_and_normalize_candles
from app.analysis.market_structure import (
    classify_market_structure,
    detect_breaks_of_structure,
    detect_change_of_character,
    detect_swing_points,
)
from app.indicators.technical import calculate_indicators
from app.mt5.connection import connect_mt5, disconnect_mt5
from app.mt5.executor import MT5TimeoutError, run_mt5
from app.mt5.market import get_candles
from app.risk.calculator import calculate_trade_levels
from app.security import ApiKeyMiddleware
from app.strategies.multi_timeframe import analyse_multiple_timeframes
from app.strategies.smc_manual_signal import (
    SYMBOL as SMC_MANUAL_SIGNAL_SYMBOL,
    generate_eurusd_manual_signal,
)
from app.strategies.trend import analyse_trend


# Minimal safe default: a no-op if the deployment environment (or
# uvicorn) has already configured logging/handlers, since basicConfig
# only takes effect when the root logger has no handlers yet.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


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

# Production Readiness Certification, Task 4: disabled by default (no
# request is affected) unless MT5_AI_BRIDGE_API_KEY is set in the
# environment -- see app/security.py.
app.add_middleware(ApiKeyMiddleware)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "status": "online",
        "message": "MT5 AI Bridge is running",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        terminal = run_mt5(mt5.terminal_info)
        account = run_mt5(mt5.account_info)

    except MT5TimeoutError as error:
        logger.error("MT5 health check timed out: %s", error)

        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    return {
        "api_status": "online",
        "mt5_connected": terminal is not None,
        "account_connected": account is not None,
    }


@app.get("/account")
def get_account() -> dict[str, Any]:
    try:
        account = run_mt5(mt5.account_info)

        if account is None:
            error = run_mt5(mt5.last_error)

            logger.error("MT5 account_info() failed: %s", error)

            raise HTTPException(
                status_code=500,
                detail=f"Unable to read account: {error}",
            )

    except MT5TimeoutError as error:
        logger.error("MT5 account_info() timed out: %s", error)

        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

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
    try:
        positions = run_mt5(mt5.positions_get)

        if positions is None:
            error = run_mt5(mt5.last_error)

            logger.error("MT5 positions_get() failed: %s", error)

            raise HTTPException(
                status_code=500,
                detail=f"Unable to read positions: {error}",
            )

    except MT5TimeoutError as error:
        logger.error("MT5 positions_get() timed out: %s", error)

        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

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
        frame = validate_and_normalize_candles(frame)
        frame = calculate_indicators(frame)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /candles/%s/%s.", symbol, timeframe
        )
        raise

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
        frame = validate_and_normalize_candles(frame)
        frame = calculate_indicators(frame)
        analysis = analyse_trend(frame)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /strategy/trend/%s/%s.",
            symbol,
            timeframe,
        )
        raise

    return {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "analysis": analysis,
    }


def _load_and_validate_candles(
    symbol: str,
    timeframe: str,
    count: int,
) -> pd.DataFrame:
    """
    Retrieve candles and immediately apply Decision A's shared
    candle-validation component, before any indicator computation.
    """
    frame = get_candles(symbol, timeframe, count)
    return validate_and_normalize_candles(frame)


@app.get("/strategy/multi-timeframe/{symbol}")
def multi_timeframe_strategy_endpoint(
    symbol: str,
    count: int = Query(default=250, ge=200, le=1000),
) -> dict[str, Any]:
    try:
        analysis = analyse_multiple_timeframes(
            symbol=symbol,
            timeframes=["H1", "H4", "D1"],
            candle_loader=_load_and_validate_candles,
            indicator_calculator=calculate_indicators,
            trend_analyser=analyse_trend,
            count=count,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /strategy/multi-timeframe/%s.",
            symbol,
        )
        raise

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


@app.get(
    "/analysis/market-structure/{symbol}/{timeframe}",
    deprecated=True,
)
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
    # Deliberately not `Response | None`: FastAPI's dependency
    # injection recognises this parameter purely by its exact
    # `Response` annotation (see fastapi.dependencies.utils.
    # add_non_field_param_to_dependency) and always supplies a real
    # Response instance at request time regardless of this default.
    # The default of None exists only so this function remains
    # directly callable (bypassing the ASGI app) the way the existing
    # test suite already calls it.
    response: Response = None,
) -> dict[str, Any]:
    """
    [DEPRECATED] Legacy market-structure analysis endpoint
    (SMC_SPECIFICATION.md §3, Decision B, Phase 2).

    This endpoint is deprecated and will be removed in a future
    release once Decision B's Phase 3 exit criteria (§3, point 6) are
    satisfied. Its response contract (swing_points/bos_events/
    choch_events/summary/settings) remains unchanged and fully
    functional for the duration of the deprecation window — no new
    functionality is added to it from this point forward.

    Migrate to POST /api/v2/analyze, the canonical
    analyze_market()/state_machine.py pipeline, which is the long-term
    interface for structure, liquidity, and Order Block analysis.
    """
    if response is not None:
        # Deprecation Header (draft-ietf-httpapi-deprecation-header)
        # and the "successor-version" Link relation (RFC 8594 companion
        # convention) — the two response-header-level deprecation
        # signals §3 point 5 calls for "where technically feasible".
        # No Sunset date is set: Decision B Phase 3 has no committed
        # removal date yet, only exit criteria (§3 point 6).
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = (
            '</api/v2/analyze>; rel="successor-version"'
        )

    try:
        candles = get_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=count,
        )

        candles = validate_and_normalize_candles(candles)

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

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /analysis/market-structure/%s/%s.",
            symbol,
            timeframe,
        )
        raise

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


class AnalyzeRequest(BaseModel):
    """
    Request body for the canonical analysis endpoint
    (POST /api/v2/analyze).
    """

    symbol: str
    timeframe: str
    count: int = Field(default=200, ge=50, le=2000)


def _dataframe_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Convert a pipeline DataFrame into JSON-safe records, following the
    same NaN/NaT-handling and time-to-string convention already used
    by every other endpoint in this file.
    """

    if frame is None or frame.empty:
        return []

    converted = frame.copy()

    for column_name in converted.columns:
        if pd.api.types.is_datetime64_any_dtype(
            converted[column_name]
        ):
            converted[column_name] = converted[
                column_name
            ].astype(str)

    converted = converted.astype(object).where(
        pd.notnull(converted),
        None,
    )

    return converted.to_dict(orient="records")


@app.post("/api/v2/analyze")
def analyze_endpoint(request: AnalyzeRequest) -> dict[str, Any]:
    """
    Canonical market-structure analysis endpoint
    (SMC_SPECIFICATION.md, Decision B, Phase 1).

    This is the long-term interface for structure, liquidity, and
    Order Block analysis, running the full analyze_market()/
    state_machine.py pipeline. It exposes the pipeline's output
    directly, without reshaping it to resemble the legacy
    /analysis/market-structure response contract.

    The legacy /analysis/market-structure endpoint remains available,
    unchanged, for the duration of the deprecation lifecycle defined
    in SMC_SPECIFICATION.md §3 — this endpoint does not replace or
    redirect it.
    """
    try:
        candles = get_candles(
            symbol=request.symbol,
            timeframe=request.timeframe,
            count=request.count,
        )

        result = analyze_market(
            symbol=request.symbol,
            timeframe=request.timeframe,
            candles=candles,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /api/v2/analyze for %s %s.",
            request.symbol,
            request.timeframe,
        )
        raise

    structure_snapshot = (
        result.structure_snapshot.to_dict()
        if result.structure_snapshot is not None
        else None
    )

    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "structure": _dataframe_to_records(result.structure),
        "liquidity_dataframe": _dataframe_to_records(
            result.liquidity_dataframe
        ),
        "events": [event.to_dict() for event in result.events],
        "liquidity": [pool.to_dict() for pool in result.liquidity],
        "order_blocks": [
            block.to_dict() for block in result.order_blocks
        ],
        "structure_snapshot": structure_snapshot,
        "metadata": result.metadata,
    }


class SmcSignalRequest(BaseModel):
    """
    Request body for the manual EURUSD ICT/SMC signal endpoint
    (POST /api/v2/strategy/smc-signal).

    The frozen manual-signal strategy supports EURUSD only -- symbol
    is accepted here (rather than hardcoded) purely so a mismatched
    value is rejected explicitly with HTTP 400, instead of the
    request silently being interpreted as EURUSD regardless of what
    was asked for.
    """

    symbol: str = Field(
        default="EURUSD",
        description="Trading symbol. Only 'EURUSD' is supported.",
    )


@app.post("/api/v2/strategy/smc-signal")
def smc_signal_endpoint(
    request: SmcSignalRequest,
) -> dict[str, Any]:
    """
    Manual-approval-only EURUSD ICT/SMC trade signal.

    Runs the frozen entry sequence -- H4 bias -> H1 confirmation ->
    M15 liquidity sweep -> confirmed CHoCH -> displacement -> linked
    Order Block -> retracement -> M5 execution confirmation -> risk
    validation -- via
    app.strategies.smc_manual_signal.generate_eurusd_manual_signal(),
    and returns its output unchanged.

    This endpoint never places an order. It returns a trade proposal
    only (status "SIGNAL_PENDING_APPROVAL", "NO_SETUP", or "BLOCKED")
    for manual human review and execution.
    """
    try:
        clean_symbol = request.symbol.strip().upper()

        if clean_symbol != SMC_MANUAL_SIGNAL_SYMBOL:
            raise ValueError(
                "This endpoint only supports "
                f"{SMC_MANUAL_SIGNAL_SYMBOL}. Received "
                f"'{clean_symbol}'."
            )

        result = generate_eurusd_manual_signal()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except MT5TimeoutError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    except Exception:
        logger.exception(
            "Unexpected error in /api/v2/strategy/smc-signal."
        )
        raise

    return result