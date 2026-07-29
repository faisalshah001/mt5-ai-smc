# MT5 AI Bridge

A read-only MetaTrader 5 analysis bridge implementing an institutional
Smart Money Concepts (SMC) structure-analysis pipeline, exposed over
FastAPI for local tools, [n8n](https://n8n.io/) workflows, and LLM tool
integrations.

This project connects to an already-running, already-logged-in MT5
terminal and exposes candle retrieval, technical indicators, a
multi-timeframe trend strategy, ATR-based risk levels, and a full
Smart Money Concepts (market structure, liquidity, Order Block)
analysis pipeline over a local HTTP API. It never places, modifies, or
closes trades, and it never reads or stores MT5 login credentials.

## Features

- **Canonical SMC pipeline** (`POST /api/v2/analyze`): swing detection,
  unified per-cycle market-structure classification, a BOS / MSS /
  CHoCH / MSS-invalidation state machine, liquidity pool (equal highs
  / equal lows) detection and lifecycle, Order Block detection and
  full lifecycle (creation, MSS→CHoCH promotion, mitigation,
  invalidation, expiration), and a single unified, time-sorted
  `MarketEvent` stream.
- **Legacy structure endpoint** (`GET /analysis/market-structure/...`,
  deprecated): a simpler, independent swing/BOS/CHoCH pipeline, kept
  running unchanged for existing consumers during the deprecation
  window — see [Versioning](#versioning) below.
- **Technical indicators**: EMA20/50/200, RSI14, MACD, ATR14.
- **Trend strategy**: a standalone EMA/RSI/MACD scoring heuristic,
  single-timeframe and multi-timeframe (H1/H4/D1) variants.
- **Risk calculator**: ATR-based stop-loss / take-profit levels from a
  signal, entry price, and ATR value.
- **Deterministic by design**: every pipeline stage is a pure function
  of its input — see
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#21-deterministic-processing).

## Requirements

- **Windows** — the `MetaTrader5` package only ships Windows wheels,
  so this project only runs on Windows (see the
  [Makefile](Makefile)'s note on this).
- **Python 3.11, 3.12, or 3.13**.
- A MetaTrader 5 terminal already installed, running, and logged into
  a broker account on the same machine.

## Installation

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

(`make install-dev` runs the equivalent command — see the
[Makefile](Makefile) for every available developer command.)

## Running

Start the MT5 terminal, log into your account, then:

```powershell
make run
# equivalent to: uvicorn main:app --reload
```

Interactive API docs are then available at `http://127.0.0.1:8000/docs`
(Swagger UI) and `http://127.0.0.1:8000/redoc`.

## Quick usage

```bash
# Liveness / MT5 connection check
curl http://127.0.0.1:8000/health

# Run the canonical SMC pipeline
curl -X POST http://127.0.0.1:8000/api/v2/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol": "EURUSD", "timeframe": "H4", "count": 200}'
```

See [`examples/`](examples/) for a curl walkthrough of every endpoint
and a minimal Python client, and
[`docs/API.md`](docs/API.md) for the full request/response reference.

## Project structure

```
main.py                    FastAPI app and all HTTP routes
app/
  mt5/                      MT5 connection + candle retrieval
  indicators/                Technical indicator calculation
  risk/                       ATR-based trade-level calculator
  strategies/                 Trend and multi-timeframe strategies
  analysis/                   Canonical SMC pipeline (see docs/ARCHITECTURE.md)
    candle_validation.py       Shared candle validation/normalisation
    market_structure.py        Legacy swing/BOS/CHoCH pipeline
    state_machine.py           Canonical BOS/MSS/CHoCH state machine
    liquidity.py / liquidity_registry.py     Liquidity pool detection + lifecycle
    order_blocks.py / order_block_registry.py Order Block detection + lifecycle
    models.py                  Domain model (MarketEvent, LiquidityPool, OrderBlock, ...)
    analysis_engine.py         Pipeline orchestrator (analyze_market)
tests/                       Unit, baseline, hardening, and golden-file tests
docs/                        Architecture, data-flow, API, testing, troubleshooting docs
examples/                    Usage examples (curl + Python client)
SMC_SPECIFICATION.md        Frozen specification governing all trading logic
IMPLEMENTATION_ROADMAP.md   Spec-to-code implementation gap analysis
```

For the full architectural rationale (why the canonical and legacy
engines coexist, why the pipeline is deterministic, module
responsibilities), see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Testing

```powershell
make test              # full suite (pytest -v)
make test-determinism   # double-run + reverse-file-order determinism gate
make test-goldens        # golden-file regression tests only
```

See [`docs/TESTING.md`](docs/TESTING.md) for what each gate verifies
and the golden-file regeneration discipline.

## Documentation

| Document | Covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, canonical vs. legacy engine, determinism guarantees |
| [`docs/DATA_FLOW.md`](docs/DATA_FLOW.md) | Every pipeline stage and DataFrame column |
| [`docs/STATE_MACHINE.md`](docs/STATE_MACHINE.md) | BOS/MSS/CHoCH state machine rules |
| [`docs/ORDER_BLOCKS.md`](docs/ORDER_BLOCKS.md) | Order Block detection and lifecycle rules |
| [`docs/API.md`](docs/API.md) | Full endpoint reference, request/response shapes, error codes |
| [`docs/TESTING.md`](docs/TESTING.md) | Test suite structure, determinism gate, golden files |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common issues, logging |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Production run command, worker configuration, timeouts, authentication, restart policy |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Full contributor guide |
| [`SMC_SPECIFICATION.md`](SMC_SPECIFICATION.md) | The frozen specification governing all trading logic |
| [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) | Spec-to-code gap analysis |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

## Versioning

Three independent version numbers exist in this project — see
[`docs/API.md#versioning`](docs/API.md#versioning) for the full
explanation:

| Component | Where | Current value |
|---|---|---|
| Application package | `pyproject.toml` | `1.0.0` |
| FastAPI app (OpenAPI/Swagger) | `main.py` | `1.3.0` |
| Canonical pipeline | `AnalysisResult.metadata["pipeline_version"]` | `3.0.0` |

## Security

This is a **local, read-only bridge**, intended for trusted-network
use only. It never reads, stores, or transmits MT5 login credentials,
and never places trades. Authentication is disabled by default (local
development) and activates automatically the moment
`MT5_AI_BRIDGE_API_KEY` is set — required before exposing this service
beyond one trusted machine; see
[`docs/DEPLOYMENT.md#authentication`](docs/DEPLOYMENT.md#authentication).
See [`SECURITY.md`](SECURITY.md) for the full threat model and for how
to report a vulnerability privately.

## License

[MIT](LICENSE) © Faisal Shah

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) — any change to trading logic
must trace to an approved decision in
[`SMC_SPECIFICATION.md`](SMC_SPECIFICATION.md).
