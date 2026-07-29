# Troubleshooting

Status: diagnostic guide grounded in the actual current implementation — every symptom below is tied to the exact module/function/log line that produces it.

## MT5 unavailable

**Symptom:** every candle-consuming endpoint returns `500`; `/health` reports `"mt5_connected": false`.

- App startup calls `connect_mt5()` (`app/mt5/connection.py`) via FastAPI's `lifespan`. If `mt5.initialize()` fails, it logs `logger.error("MT5 initialization failed: %s", error)` and raises `RuntimeError` — this happens once, at process startup, so a failure here means the **entire app failed to start**, not just one request.
- If the app *did* start but MT5 later becomes unreachable, `get_candles()` (`app/mt5/market.py`) will fail per-request: `mt5.symbol_select()` returning `False` → `ValueError` (`400`, "Symbol '...' could not be selected in MT5"); `mt5.copy_rates_from_pos()` returning `None` → logged at `ERROR` ("MT5 candle retrieval failed for ...") then `RuntimeError` (`500`); an empty result → logged at `WARNING` then `ValueError` (`400`, "No candle data returned for ...").
- Check `/health` and `/account` first — both surface `mt5.last_error()` directly, and both log at `ERROR` before raising.

## Validation failures

**Symptom:** a candle-consuming endpoint returns `400` with a message about missing columns, invalid values, duplicate timestamps, or an invalid OHLC relationship.

All of these originate in `app/analysis/candle_validation.py::validate_and_normalize_candles` (Decision A) — see [DATA_FLOW.md §1](DATA_FLOW.md#1-mt5--validated-candles) for the exact check list. Every rejection is preceded by a `logger.warning` summary (row/column counts, never the actual candle values) at the `app.analysis.candle_validation` logger — check application logs for the exact count and which check failed; the HTTP error message truncates to the first 10 offending row indices.

**If this fires unexpectedly on data you believe is well-formed:** remember the row indices in the "invalid time"/"invalid numeric" error messages are *pre-sort* positions (as received from MT5), while the "invalid high"/"invalid low" messages are *post-sort, reset* positions (`0..n-1` after chronological sorting) — the same index number means a different candle depending on which check raised. This is a known, documented inconsistency (see the module's own docstring), not a bug to work around, just something to account for when correlating an error message back to raw MT5 output.

## Missing events

**Symptom:** you expect a `MarketEvent` (e.g. for an Order Block or liquidity pool lifecycle transition) that isn't in `AnalysisResult.events`.

- Confirm the transition actually happened by checking the relevant DataFrame column first (`order_block_expired`, `liquidity_broken`, etc. — see [DATA_FLOW.md](DATA_FLOW.md)), not just the event list — **expiration is a known gap**: both `LiquidityPool` and `OrderBlock` expiration (`maximum_age_bars`) update the object's own state and the DataFrame's boolean/id columns, but emit **no** `MarketEvent` — `LIQUIDITY_EXPIRED` exists as an `EventType` value but is never constructed anywhere; `ORDER_BLOCK_EXPIRED` doesn't even exist as an `EventType` value. If you're looking for an expiration event, it will never appear — check the registry object's own `expired`/`expired_time`/`expired_index` fields instead.
- `maximum_age_bars` (the only trigger for expiration at all) is **not** wired to any HTTP endpoint or to `analyze_market()`'s default call — if you're not seeing expiration-related columns populate at all, that's expected unless you're calling `detect_liquidity_registry`/`detect_order_blocks` directly with `maximum_age_bars` set.
- For every other lifecycle transition (sweep, break, mitigation, invalidation, confirmation, structural BOS/MSS/CHoCH/MSS_INVALIDATED), an event genuinely is emitted — if one of *those* is missing, treat it as a real bug, not this known gap.

## Unexpected MSS

**Symptom:** an MSS fires where you didn't expect one, or doesn't fire where you did.

- MSS is a **close-based** trigger: `current_state == "bullish"` and `close` breaks below `protected_low` by at least `atr14 × minimum_break_atr` (default `0.10`). Check the row's `atr14` and `protected_low`/`protected_high` values directly — a small `minimum_break_atr` makes MSS trigger on comparatively small moves.
- Remember MSS does **not** by itself change `current_trend` — only `current_state` (to `mss_bullish`/`mss_bearish`). If you expected a full trend reversal from a single MSS, that's the pending/confirmed distinction working as designed — a CHoCH is required to actually flip `current_trend`. See [STATE_MACHINE.md §3](STATE_MACHINE.md#3-events).
- Check `protected_low`/`protected_high`'s `status` column — if it already reads `"broken"`, a *second* MSS cannot re-trigger against the same level until it's reseeded (via `MSS_INVALIDATED`) or replaced (via a fresh classified swing) — `broken_bearish_mss_level != protected_low` (etc.) is the guard preventing repeated firing.
- If you're comparing against the **legacy** endpoint's `bos`/`choch` output, remember it has no MSS concept at all — its `choch` field means something structurally different (a simple BOS-direction-flip model, not a state-machine-confirmed reversal). Don't compare the two engines' outputs as if they were the same vocabulary. See [ARCHITECTURE.md §4](ARCHITECTURE.md#4-canonical-vs-legacy-engine).

## Missing Order Blocks

**Symptom:** you expect an `ORDER_BLOCK_CREATED` event or a populated `order_block_id` row that isn't there.

Creation requires **all** of (see [ORDER_BLOCKS.md §1](ORDER_BLOCKS.md#1-creation)): a qualifying `structure_event` in `source_event_types` (default all of BOS/MSS/CHoCH) with a non-neutral direction; the event candle's body ratio ≥ `minimum_event_body_ratio` (default `0.55`) and coloured correctly; a same-coloured-opposite anchor candle found within `lookback_bars` (default `12`); displacement from that anchor to the event candle's close ≥ `atr14 × minimum_displacement_atr` (default `1.0`); and, if `require_liquidity_sweep=True`, a matching swept-liquidity row in between. Any one of these failing silently produces no block for that event — there's no separate rejection log for this (unlike candle validation); check each condition against the actual row values directly, or lower the relevant threshold if the fixture is intentionally minimal (many hand-built test fixtures explicitly need larger displacement/lookback than their defaults to trigger a block at all).

## Golden failures

**Symptom:** a golden-file test fails.

See [TESTING.md §4 and §6](TESTING.md#4-goldens) for the full procedure. In short: **do not regenerate to make it pass.** First determine whether the code change that caused it is an approved, deliberate behaviour change or an actual regression. `tests/helpers/golden.py::assert_matches_golden` reports the *first* differing record on a list mismatch — read that before assuming the whole file is wrong; often only one field on one row actually changed.

## Test failures

- Run the specific failing file in isolation first (`pytest tests/test_X.py -v`) to rule out cross-test interference — if it only fails as part of the full suite, that itself is a bug (this project's own regression discipline runs the suite in reverse file order specifically to catch this class of issue; see [TESTING.md §2](TESTING.md#2-determinism)).
- If a hand-built (`build_zigzag_candles`-based) fixture's assertion looks wrong, don't hand-recompute the expected value from the waypoint list — run the fixture through the actual function interactively and read off the real output; this codebase's own history shows manual OHLC/index arithmetic is unreliable.
- If several unrelated-seeming tests fail together after a `state_machine.py` change, check the Decision #8 same-row event-ordering invariant first (CHoCH/MSS_INVALIDATED > MSS > BOS, at most one event per row) — this is the most fragile invariant in that file given how many decisions' branches share it.

## Logging

Structured logging (Python's standard `logging` module) exists at: `app.mt5.connection` (connect/disconnect, `INFO`/`ERROR`), `app.mt5.market` (retrieval failures, `ERROR`/`WARNING`), `app.analysis.candle_validation` (rejection summaries, `WARNING`), and `main` (`/account`/`/positions` MT5 failures at `ERROR`; unexpected-exception catch-alls at each MT5/candle-consuming endpoint, via `logger.exception` — full stack trace). A minimal `logging.basicConfig(level=logging.INFO, ...)` safe default is set in `main.py`; it is a no-op if the deployment environment has already configured logging/handlers.

**What is deliberately not logged:** full candle datasets, credentials/secrets/tokens/account passwords, and the same exception at more than one layer (an already-logged `ValueError`/`RuntimeError` from `candle_validation.py`/`market.py` is not re-logged at the endpoint layer — only genuinely unanticipated exceptions are).

**If you need more visibility than exists today:** the SMC pipeline functions themselves (`state_machine.py`, `liquidity.py`, `order_blocks.py`, `analysis_engine.py`) have no logging calls at all — they are pure functions with no operational-boundary events of their own to log (their entire behaviour is captured in their return value, which the caller already has). Diagnosing a specific row's behaviour there means inspecting the returned DataFrame/objects directly, not searching logs.

## Performance

- The known algorithmic characteristics: `liquidity.py` and `order_blocks.py` each scan their *entire* active-pool/-block list every row (bounded by how many pools/blocks are still active, not by total candle count, but unbounded in principle since `maximum_age_bars`-based expiration isn't wired to production); `LiquidityRegistry.add()`/`OrderBlockRegistry.add()` do an O(n) linear scan for ID-uniqueness on every insert. None of this is expected to matter at realistic H1/H4/D1 candle-history sizes — it would only become material on very large M1 histories processed in one call.
- `detect_swing_points` (`market_structure.py`) is a per-row Python loop with fresh `.iloc[]` slicing each iteration rather than a vectorised rolling window — negligible at the default `left_bars=right_bars=3`.
- If a specific run is slow, profile before assuming it's one of the above — `calculate_indicators`' `.ewm()` calls and MT5's own `copy_rates_from_pos` round-trip are also plausible costs, especially for large `count` values.

## Deployment

- This is a local-first FastAPI app (`uvicorn main:app`), with MT5 connectivity required at process startup (`lifespan`) — it cannot start successfully without a reachable MT5 terminal, by design (see [Deployment MT5 unavailable](#mt5-unavailable) above).
- No authentication exists on any route — this app is described (`CLAUDE.md`, `main.py`'s own FastAPI `description`) as a local bridge for MT5/n8n/LLM tooling, not a public-facing service. Do not expose it directly to an untrusted network without adding an auth layer first; that is outside this codebase's current scope.
- `logging.basicConfig`'s safe default writes to stderr with no rotation/retention policy — for a long-running deployment, supply your own logging configuration before the app module is imported (it will take precedence, since `basicConfig` no-ops once handlers already exist).
- `pipeline_version` in `/api/v2/analyze` responses is the signal to watch for behaviour changes across deployments — see [API.md §Versioning](API.md#versioning).

## Cross-references

- Full pipeline column reference (useful for inspecting "what actually happened" on a specific row): [DATA_FLOW.md](DATA_FLOW.md)
- State machine internals: [STATE_MACHINE.md](STATE_MACHINE.md)
- Order Block internals: [ORDER_BLOCKS.md](ORDER_BLOCKS.md)
- Endpoint-by-endpoint error behaviour: [API.md](API.md)
- Test/golden mechanics: [TESTING.md](TESTING.md)
