# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Application versioning (this file) and the canonical pipeline's own
`pipeline_version` (returned in every `/api/v2/analyze` response) are
tracked independently — see [`docs/API.md#versioning`](docs/API.md#versioning)
for why, and `SMC_SPECIFICATION.md` §33 for the rules governing when
`pipeline_version` itself changes.

No entry in this file was produced by regenerating a golden file
without first proving the corresponding output change was required —
see [`docs/TESTING.md`](docs/TESTING.md) for that discipline.

## [1.0.0] — Initial public release

The first published release. Everything below was implemented against
the frozen `SMC_SPECIFICATION.md` before this repository's first
public release; there is no prior published version to diff against.

### Added

- **Canonical SMC analysis pipeline** (`app/analysis/`): candle
  validation, technical indicators, swing detection, unified per-cycle
  market-structure classification and BOS/MSS/CHoCH/MSS_INVALIDATED
  state machine, liquidity pool (EQH/EQL) detection and lifecycle,
  Order Block detection and full lifecycle (creation, MSS→CHoCH
  promotion, mitigation, invalidation, expiration), a unified
  `MarketEvent` stream, and `StructureSnapshot`/`AnalysisResult`
  serialisation. See `docs/ARCHITECTURE.md`, `docs/DATA_FLOW.md`,
  `docs/STATE_MACHINE.md`, `docs/ORDER_BLOCKS.md`.
- **Canonical endpoint**: `POST /api/v2/analyze` — the long-term
  interface, exposing the full pipeline output directly.
- **Legacy endpoint**: `GET /analysis/market-structure/{symbol}/{timeframe}`
  — a simpler, independent BOS/CHoCH pipeline, run side by side with
  the canonical engine per a governed, three-phase deprecation
  lifecycle (`SMC_SPECIFICATION.md` §3, Decision B). Currently in
  **Phase 2 — Deprecation notice**: functional, marked `deprecated`
  in its OpenAPI entry and via `Deprecation`/`Link` response headers,
  receiving no new functionality.
- **Shared candle validation** (`app/analysis/candle_validation.py`):
  a single hygiene gate (NaN/±Infinity rejection, duplicate-timestamp
  rejection, OHLC-relationship checks, chronological sort) applied to
  every candle-consuming endpoint.
- **Protected-level lifecycle**: status/source tracking, reseeding on
  initialization and post-invalidation, a closed four-transition set
  (Creation/Replacement/Reseed/Clearing).
- **MSS invalidation**: same-original-direction swing detection,
  `MSS_INVALIDATED` event, cross-engine join metadata
  (`mss_origin_index`/`mss_origin_event_id`).
- **Order Block MSS-sourcing**: provisional/confirmed lifecycle,
  MSS-invalidation cascade, CHoCH promotion by anchor-candle identity.
- **`MarketEvent.strength`** population for BOS/MSS/CHoCH.
- **Utility endpoints**: `/`, `/health`, `/account`, `/positions`,
  `/candles/{symbol}/{timeframe}`, `/strategy/trend/{symbol}/{timeframe}`,
  `/strategy/multi-timeframe/{symbol}`, `/risk/trade-levels`.
- **Structured logging** (Python `logging`) at MT5 connection
  lifecycle, unexpected MT5 retrieval failures, candle-validation
  rejection summaries, and endpoint-level unexpected exceptions.
- **Test suite**: 157 tests, 16 golden-file regression snapshots, a
  deterministic zigzag-candle fixture builder, standing double-run and
  reverse-file-order regression gates. See `docs/TESTING.md`.
- **Documentation set** (`docs/`): `ARCHITECTURE.md`, `DATA_FLOW.md`,
  `STATE_MACHINE.md`, `ORDER_BLOCKS.md`, `API.md`, `TESTING.md`,
  `CONTRIBUTING.md`, `TROUBLESHOOTING.md`.
- **Release engineering**: `pyproject.toml`, `LICENSE` (MIT),
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, GitHub issue/PR templates, CI
  workflow (`.github/workflows/ci.yml`), `Makefile`, `examples/`.

### Fixed

- **RSI zero-average-loss defect** (`app/indicators/technical.py`):
  `rsi14` previously returned `NaN` instead of the textbook-correct
  `100.0` whenever an uninterrupted uptrend drove the average loss to
  exactly zero — reproducible on real market data, not just a
  theoretical edge case. Scoped to the standalone trend-strategy
  feature (`/strategy/trend`, `/strategy/multi-timeframe`); the SMC
  pipeline itself never reads `rsi14`.
- **Missing-data guard** (`app/analysis/state_machine.py`): a
  structure event already determined from swing classification alone
  (CHoCH, later also `MSS_INVALIDATED`) no longer gets silently
  dropped merely because `close`/ATR is simultaneously unavailable on
  the same row.

### Removed

- **`app/analysis/advanced_market_structure.py`** and
  **`app/analysis/event_registry.py`** — orphaned modules predating
  this project's spec-driven implementation, confirmed to have zero
  consumers anywhere in production code or tests before removal (see
  the production-readiness audit).

### Known limitations (tracked, not regressions)

- Live-safe incremental output mode (`SMC_SPECIFICATION.md` §30,
  Decision #14) and Internal Structure (§9, Decision #5) are specified
  but not implemented — both are explicitly deferred pending their own
  design/decision steps.
- `LiquidityPool`/`OrderBlock` age-based expiration exists in code but
  emits no corresponding `MarketEvent`, and is not wired to any HTTP
  endpoint's defaults — see `docs/TROUBLESHOOTING.md#missing-events`.
- `black`/`mypy` CI checks are informational (non-blocking) for this
  release — see `pyproject.toml`'s tooling notes and this release's
  own engineering deliverables for why.
