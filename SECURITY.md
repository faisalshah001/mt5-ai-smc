# Security Policy

## Scope and threat model

This project is a **local, read-only bridge** between a MetaTrader 5 (MT5) terminal already running and logged in on the same machine, and HTTP clients on that machine's network (n8n workflows, LLM tool integrations, local tooling). It is documented and intended for **local, trusted-network use**, not as a public-facing service:

- **No authentication is required by default.** An optional, opt-in API-key check (`app/security.py::ApiKeyMiddleware`) activates automatically the moment `MT5_AI_BRIDGE_API_KEY` is set in the environment, and stays fully disabled (identical to no auth at all) otherwise — see [`docs/DEPLOYMENT.md#authentication`](docs/DEPLOYMENT.md#authentication). Setting that key is **required** before exposing this application beyond the single trusted machine it runs on; it is not a substitute for a reverse proxy/VPN if you need TLS or network-level access control, which remain outside this codebase's scope.
- **This codebase never handles MT5 login credentials.** `app/mt5/connection.py::connect_mt5()` calls `MetaTrader5.initialize()` with no arguments — it attaches to a terminal that is already running and already authenticated. No username, password, or account token is read, stored, transmitted, or logged anywhere in this codebase.
- **No trade execution exists.** Every endpoint is read-only analysis (candle retrieval, indicator/structure computation) except `/risk/trade-levels`, which is a pure arithmetic calculation from caller-supplied numbers — it does not place, modify, or close any order.
- **No persistence layer.** Nothing is written to disk beyond application logs (see [`docs/TROUBLESHOOTING.md#logging`](docs/TROUBLESHOOTING.md#logging)); every request recomputes from the candle window it retrieves.

## Supported versions

| Component | Version | Supported |
|---|---|---|
| Application (`pyproject.toml`) | 1.0.x | :white_check_mark: |
| Canonical pipeline (`pipeline_version` in `/api/v2/analyze` responses) | 3.0.0 | :white_check_mark: |
| Legacy endpoint (`/analysis/market-structure`) | frozen, deprecated | :white_check_mark: (deprecated, still receives security fixes) |

Only the latest released minor/patch version receives security fixes. There is no long-term-support branch at this time.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a suspected security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository (**Security** tab → **Report a vulnerability**), or contact the maintainer directly through the contact method listed on their GitHub profile. Include:

- A description of the issue and its potential impact.
- Steps to reproduce (a minimal request/payload is ideal).
- The version/commit you tested against.

You should receive an acknowledgement within a reasonable time. We will work with you to understand and validate the issue, develop and test a fix, and coordinate disclosure timing before any public write-up.

## What counts as a security issue here

Given the threat model above, examples of genuinely in-scope reports include:

- A way to make an endpoint execute arbitrary code, read arbitrary files, or access MT5 account data beyond what its own documented response already exposes.
- A denial-of-service vector that doesn't require an already-privileged position on the local network.
- Dependency vulnerabilities in the pinned packages in `requirements.txt` with a realistic exploitation path through this application's actual usage.

Reports about the *absence* of authentication on a local-only, no-auth-by-design tool are a known, documented characteristic (see [Scope and threat model](#scope-and-threat-model) above), not a new finding — please raise a feature request instead if you'd like an auth layer added.
