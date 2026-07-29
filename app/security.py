"""
Lightweight API-key authentication (Production Readiness
Certification, Task 4).

Disabled entirely -- every request passes through completely
unchanged -- unless the MT5_AI_BRIDGE_API_KEY environment variable is
set to a non-empty value. This keeps local development frictionless
by default and turns itself on automatically the moment an operator
configures a key for a shared/production deployment; no separate
on/off switch exists to fall out of sync with the key itself.

Implemented as ASGI middleware (rather than a FastAPI
Depends()-based dependency) so it applies uniformly to every route
without touching any route function's signature -- several of this
project's existing tests call route functions directly, bypassing the
ASGI app entirely, and must remain unaffected by this change.
"""

from __future__ import annotations

import logging
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

API_KEY_ENV_VAR = "MT5_AI_BRIDGE_API_KEY"
API_KEY_HEADER = "X-API-Key"

# Reachable without an API key even when one is configured: liveness
# probes and read-only schema browsing carry no account data and must
# stay reachable by infra tooling that cannot supply credentials.
_UNPROTECTED_PATHS = frozenset(
    {
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests missing a valid X-API-Key header, if configured."""

    async def dispatch(self, request: Request, call_next) -> Response:
        configured_key = os.environ.get(API_KEY_ENV_VAR, "").strip()

        if not configured_key:
            return await call_next(request)

        if request.url.path in _UNPROTECTED_PATHS:
            return await call_next(request)

        supplied_key = request.headers.get(API_KEY_HEADER, "")

        if not secrets.compare_digest(supplied_key, configured_key):
            logger.warning(
                "Rejected request to %s: missing or invalid %s header.",
                request.url.path,
                API_KEY_HEADER,
            )

            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key."},
                headers={"WWW-Authenticate": API_KEY_HEADER},
            )

        return await call_next(request)
