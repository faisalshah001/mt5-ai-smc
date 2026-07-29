# Production Deployment

Status: Production Readiness Certification, Task 3. Covers running this
service as a supervised, production-configured process. The development
workflow (`make run` / `uvicorn main:app --reload`) is unchanged and remains
the right choice for local development — nothing here replaces it.

## Prerequisites

Same as development: Windows, Python 3.11/3.12/3.13, a MetaTrader 5 terminal
already installed, running, and logged into a broker account on the same
machine (`app/mt5/connection.py::connect_mt5()` attaches to it at startup and
the process will not start without it — see
[TROUBLESHOOTING.md#mt5-unavailable](TROUBLESHOOTING.md#mt5-unavailable)).

## Production run command

```powershell
make run-prod
# equivalent to: uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Differences from `make run`:

- **No `--reload`.** The file-watcher `--reload` implies has no purpose in
  production and causes unwanted restarts on any filesystem write.
- **`--workers 1`, deliberately** — see [Worker configuration](#worker-configuration)
  below. This is a certified choice, not an oversight.
- **`--host`/`--port`**: adjust for your environment. If you bind to
  anything other than `127.0.0.1` (e.g. `0.0.0.0` to serve a remote n8n
  instance), you **must** set `MT5_AI_BRIDGE_API_KEY` first — see
  [Authentication](#authentication) below and [SECURITY.md](../SECURITY.md).

Run it under a process supervisor (below), not directly in an interactive
shell long-term.

## Worker configuration

**Recommended: exactly 1 uvicorn worker process.**

Every `mt5.*` call in this codebase is routed through a single dedicated
worker thread (`app/mt5/executor.py::run_mt5`, Production Readiness
Certification Task 1), which guarantees no two `mt5.*` calls ever execute
concurrently *within one process*. That guarantee does not extend across
separate OS processes: each `--workers N` process would open its own,
independent connection to the same MT5 terminal, and the `MetaTrader5`
package documents no guarantee about concurrent access from multiple
separate processes either. Until that is independently verified against a
live terminal, `--workers 1` is the certified, safe configuration — it is
the deliberate scope of the fix that was implemented, not a temporary
placeholder.

If throughput becomes a real bottleneck, prefer adding a caching layer in
front of this service (most calls are read-heavy and idempotent within a
candle's lifetime) over increasing `--workers` without that verification.

## Timeouts

Every `mt5.*` call has a timeout (default 10 seconds), after which the
request fails fast with `HTTP 503` rather than hanging
(Production Readiness Certification, Task 2 —
[API.md#errors](API.md#errors) documents the resulting status codes).
Override the default via:

```
MT5_CALL_TIMEOUT_SECONDS=15
```

Set this once in the environment the service runs under (see
[Restart policy](#restart-policy) below for where that is, per supervisor).

## Authentication

Disabled by default (identical to local development) unless
`MT5_AI_BRIDGE_API_KEY` is set, in which case every endpoint except `/`,
`/health`, `/docs`, `/redoc`, and `/openapi.json` requires a matching
`X-API-Key` header (Production Readiness Certification, Task 4 — see
[app/security.py](../app/security.py) and [SECURITY.md](../SECURITY.md)).

```powershell
$env:MT5_AI_BRIDGE_API_KEY = "<a long, random value>"
make run-prod
```

```bash
curl -H "X-API-Key: <the same value>" http://127.0.0.1:8000/account
```

**Required, not optional, the moment this service is reachable from
anywhere other than the single machine it runs on.**

## Restart policy

This is a long-running Windows process with no built-in restart-on-crash
behaviour — that must come from a supervisor. Recommended: **NSSM** (Non-Sucking
Service Manager), which runs it as a native Windows service:

```powershell
nssm install MT5AIBridge "C:\path\to\venv\Scripts\uvicorn.exe" "main:app --host 127.0.0.1 --port 8000 --workers 1"
nssm set MT5AIBridge AppDirectory "C:\path\to\MT5_AI"
nssm set MT5AIBridge AppExit Default Restart
nssm set MT5AIBridge AppRestartDelay 5000
nssm start MT5AIBridge
```

`AppExit Default Restart` restarts the process on any exit (crash or
otherwise); `AppRestartDelay` avoids a tight restart loop if MT5 itself is
unavailable. Point your monitoring at `GET /health` — it now returns `503`
(rather than hanging) when MT5 is unresponsive, so repeated `503`s from
`/health` is the signal to alert or restart on.

Alternative without installing anything extra: Windows Task Scheduler, a
trigger "At startup", action running the same command, and "Restart the task"
configured under the task's Settings tab on failure.

## What this does not cover

Load balancing, multi-machine deployment, and TLS termination are all out of
scope for a single-terminal-per-instance service — put a reverse proxy in
front of it if you need TLS or need to consolidate multiple such instances
behind one address; that proxy is also the natural place to add rate
limiting if it's ever needed (not implemented in this codebase).

## Cross-references

- Endpoint-by-endpoint error/status-code behaviour: [API.md](API.md)
- Threat model and what this service intentionally does not protect against: [SECURITY.md](../SECURITY.md)
- Logging configuration and known limitations: [TROUBLESHOOTING.md#logging](TROUBLESHOOTING.md#logging)
