# API Reference

Status: every route registered on the FastAPI app in `main.py`, as implemented today. All endpoints are `GET` except the canonical analysis endpoint (`POST`). None require authentication (`SMC_SPECIFICATION.md` and `CLAUDE.md` describe this as a local, read-only bridge for MT5/n8n/LLM tooling).

Every candle-consuming endpoint applies `validate_and_normalize_candles` (Decision A) before any computation — see [DATA_FLOW.md §1](DATA_FLOW.md#1-mt5--validated-candles). Every endpoint maps a raised `ValueError` → HTTP `400` and `RuntimeError` → HTTP `500`; unexpected exceptions are logged (`logger.exception`) and re-raised unchanged, so FastAPI's default handling still applies — see [TROUBLESHOOTING.md §Logging](TROUBLESHOOTING.md#logging).

## Utility endpoints

### `GET /`
Liveness check. No parameters. Response: `{"status": "online", "message": "MT5 AI Bridge is running"}`.

### `GET /health`
Response: `{"api_status": "online", "mt5_connected": bool, "account_connected": bool}` — reads `mt5.terminal_info()`/`mt5.account_info()` directly, does not raise on MT5 being unavailable.

### `GET /account`
Response: login/server/currency/balance/equity/profit/margin/margin_free/margin_level/leverage/trade_allowed, read from `mt5.account_info()`. **Errors:** `500` if `mt5.account_info()` returns `None` (logged at `ERROR` before raising).

### `GET /positions`
Response: `{"count": int, "positions": [...]}`, each with ticket/symbol/type (`BUY`/`SELL`)/volume/price_open/price_current/stop_loss/take_profit/profit/comment. **Errors:** `500` if `mt5.positions_get()` returns `None` (logged at `ERROR`).

## Candle / analysis endpoints

### `GET /candles/{symbol}/{timeframe}`
Query: `count` (default `250`, `50 ≤ count ≤ 1000`). Retrieves candles, validates, computes indicators. Response: `{symbol, timeframe, count, latest_indicators: {close, ema20, ema50, ema200, rsi14, macd, macd_signal, macd_histogram, atr14}, candles: [...]}` (time serialised as string, `NaN`/`NaT` as `null`). **Errors:** `400` (validation failure), `500` (MT5 retrieval failure).

### `GET /strategy/trend/{symbol}/{timeframe}`
Query: `count` (default `250`, `200 ≤ count ≤ 1000` — the higher floor exists so EMA200 has a meaningful history). Runs `app/strategies/trend.py::analyse_trend` — a standalone EMA/RSI/MACD scoring heuristic, **not part of the SMC pipeline** and not governed by `SMC_SPECIFICATION.md`. Response: `{symbol, timeframe, analysis: {trend, signal, confidence, scores, reasons, close, ema_alignment, momentum}}`. **Errors:** `400`, `500`.

### `GET /strategy/multi-timeframe/{symbol}`
Query: `count` (default `250`, `200 ≤ count ≤ 1000`). Runs `analyse_trend` across `["H1", "H4", "D1"]` (hardcoded) via `app/strategies/multi_timeframe.py::analyse_multiple_timeframes` and aggregates. Response: `{symbol, timeframes, overall_signal, alignment, confidence, summary: {buy, sell, wait}, analysis_by_timeframe}`. **Errors:** `400`, `500`.

### `GET /risk/trade-levels`
Query (all required except the last two): `signal` (`buy`/`sell`), `entry_price`, `atr`, `stop_loss_atr_multiplier` (default `1.5`, `> 0`), `risk_reward_ratio` (default `2.0`, `> 0`). Pure calculation, no MT5/candle involvement — `app/risk/calculator.py::calculate_trade_levels`. Response: the input parameters plus `stop_distance`, `target_distance`, `stop_loss`, `take_profit`. **Errors:** `400` only (no candle/MT5 dependency, so no `500` path here).

## Market-structure endpoints

### `GET /analysis/market-structure/{symbol}/{timeframe}` — **DEPRECATED**

The **legacy** engine (see [ARCHITECTURE.md §4](ARCHITECTURE.md#4-canonical-vs-legacy-engine)). Marked `deprecated=True` in the OpenAPI schema (renders with a strikethrough / "Deprecated" badge in Swagger UI). Fully functional — receives Decision A's validation and nothing else going forward.

Query: `count` (default `200`, `50–2000`), `left_bars`/`right_bars` (default `3`, `1–20`), `minimum_break_atr` (default `0.10`, `0–5`).

Response (frozen contract, unchanged since Phase 0):
```json
{
  "symbol": "EURUSD",
  "timeframe": "H4",
  "settings": {"count": 200, "left_bars": 3, "right_bars": 3, "minimum_break_atr": 0.10},
  "summary": {"swing_highs": 0, "swing_lows": 0, "bullish_bos": 0, "bearish_bos": 0, "bullish_choch": 0, "bearish_choch": 0},
  "swing_points": [...],
  "bos_events": [...],
  "choch_events": [...]
}
```
`swing_points`/`bos_events`/`choch_events` are each the **last 20** matching rows (`.tail(20)`). **Response headers:** `Deprecation: true` and `Link: </api/v2/analyze>; rel="successor-version"` — see [Deprecation Strategy](#deprecation-strategy). **Errors:** `400`, `500`.

### `POST /api/v2/analyze` — canonical, long-term interface

The **canonical** engine ([ARCHITECTURE.md §4](ARCHITECTURE.md#4-canonical-vs-legacy-engine)). Not deprecated.

**Request body** (`AnalyzeRequest`):
```json
{"symbol": "EURUSD", "timeframe": "H4", "count": 200}
```
`count`: default `200`, `50 ≤ count ≤ 2000`. Note: unlike the legacy endpoint, this request model does **not** expose `left_bars`/`right_bars`/`minimum_break_atr` or any of `analyze_market()`'s optional `swing_options`/`structure_options`/`liquidity_options`/`order_block_options` — the endpoint always runs with the pipeline's built-in defaults. Customising those parameters currently requires calling `analyze_market()` directly in Python, not through this HTTP surface.

**Response** (`AnalysisResult`-shaped, via `analyze_endpoint`):
```json
{
  "symbol": "EURUSD",
  "timeframe": "H4",
  "structure": [...],
  "liquidity_dataframe": [...],
  "events": [...],
  "liquidity": [...],
  "order_blocks": [...],
  "structure_snapshot": {...} | null,
  "metadata": {...}
}
```
- `structure`: every row of the final pipeline DataFrame (see [DATA_FLOW.md](DATA_FLOW.md) for every column).
- `liquidity_dataframe`: the liquidity-stage DataFrame (a subset of `structure`'s row history, liquidity-specific columns only).
- `events`: the full, time-sorted `MarketEvent` stream (structure + liquidity + Order Block events combined).
- `liquidity` / `order_blocks`: every `LiquidityPool`/`OrderBlock` object ever created this run, each via its own `to_dict()`.
- `structure_snapshot`: the latest-state summary, or `null` if the input produced an empty result.
- `metadata`: `pipeline_version` (currently `"3.0.0"`), `input_candle_count`, `processed_candle_count`, per-stage event counts, active-liquidity/Order-Block counts, and the exact kwargs each stage ran with.

All DataFrame-shaped fields go through `_dataframe_to_records` — datetime columns stringified, `NaN`/`NaT` → `null`, consistent with every other endpoint's serialization convention. **Errors:** `400`, `500`.

## Errors

| Status | Meaning | Source |
|---|---|---|
| `400` | `ValueError` — malformed input, validation rejection, unsupported symbol/timeframe | candle validation, MT5 symbol lookup, pipeline parameter validation |
| `401` | Missing or invalid `X-API-Key` header — only possible when `MT5_AI_BRIDGE_API_KEY` is configured (disabled by default) | `app/security.py::ApiKeyMiddleware` — see [DEPLOYMENT.md#authentication](DEPLOYMENT.md#authentication) |
| `500` | `RuntimeError` — MT5 connectivity/retrieval failure, or an unexpected (unanticipated) exception | `app/mt5/*`, or anything escaping the endpoint's explicit `except` clauses |
| `503` | `MT5TimeoutError` (a `RuntimeError` subclass) — an `mt5.*` call did not complete within its timeout (default 10s) | `app/mt5/executor.py::run_mt5` — see [DEPLOYMENT.md#timeouts](DEPLOYMENT.md#timeouts) |

Every `400`/`500`/`503` response body is `{"detail": "<the exception's str()>"}` (FastAPI's standard `HTTPException` shape). `401` responses use the same `{"detail": "..."}` shape for consistency, set by `ApiKeyMiddleware` directly rather than via `HTTPException`.

## OpenAPI notes

FastAPI auto-generates the OpenAPI schema (`/openapi.json`) and interactive docs (`/docs`, `/redoc`) from the route definitions above — no separate OpenAPI spec file is maintained by hand. The app itself: `title="MT5 AI Bridge"`, `version="1.3.0"` (this is the **API/app** version, unrelated to `metadata["pipeline_version"]` — see [Versioning](#versioning)). The legacy endpoint's `deprecated=True` is the only route-level OpenAPI customisation beyond the default path/method/parameter inference.

## Versioning

Two independent version numbers exist and must not be conflated:

- **`app = FastAPI(version="1.3.0", ...)`** — the HTTP application's own version, shown in `/docs`/`/openapi.json`. Not tied to any SMC decision.
- **`AnalysisResult.metadata["pipeline_version"]`** (currently `"3.0.0"`), returned in every `/api/v2/analyze` response — governed by `SMC_SPECIFICATION.md` §33's semver-like scheme: **MAJOR** = event semantics or response-shape change, **MINOR** = additive new event types/fields, **PATCH** = a defect fix bringing behaviour into conformance with the spec, without changing the spec. It is bumped only when a decision's implementation explicitly requires it (recorded per-decision in §33) — never guessed. The legacy endpoint carries no equivalent version field; its response contract is simply frozen.

## Deprecation strategy

`SMC_SPECIFICATION.md` §3, Decision B defines a three-phase lifecycle for the legacy endpoint:

1. **Phase 1 — Introduction** (done): canonical endpoint introduced alongside the unchanged legacy one.
2. **Phase 2 — Deprecation notice** (done, current state): legacy endpoint marked deprecated in OpenAPI (`deprecated=True`) and via response headers (`Deprecation: true`, `Link: </api/v2/analyze>; rel="successor-version"`) — no `Sunset` date, since none has been committed. No runtime behaviour change; the legacy response body is untouched.
3. **Phase 3 — Removal** (not implemented, gated): requires all first-party consumers migrated, Decisions #6/#10/#11/#12 and #14 "where applicable" complete (all satisfied except #14, which is itself blocked pending its own design step), and the Phase 2 notice period to have elapsed. This is the only phase classified **MAJOR** for `pipeline_version` — introducing the canonical endpoint and deprecating the legacy one were both non-breaking.

**`[INVARIANT]`**: no adapter translates one endpoint's output into the other's shape, at any point in this lifecycle — see [ARCHITECTURE.md §2.5](ARCHITECTURE.md#25-no-adapter-layer-between-legacy-and-canonical).

## Cross-references

- Response DataFrame column meanings: [DATA_FLOW.md](DATA_FLOW.md)
- Canonical engine internals: [STATE_MACHINE.md](STATE_MACHINE.md), [ORDER_BLOCKS.md](ORDER_BLOCKS.md)
- Endpoint-level test coverage: [TESTING.md](TESTING.md)
