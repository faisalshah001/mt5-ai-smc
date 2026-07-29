"""
Production Readiness Certification, Task 4: lightweight API-key
authentication (app/security.py).

ApiKeyMiddleware.dispatch() is exercised directly against a minimal,
manually-constructed ASGI request (no FastAPI TestClient/httpx
dependency -- this project does not otherwise depend on httpx, and
Starlette's own Request/Response primitives, already available
transitively through fastapi/starlette, are sufficient here).
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.security import API_KEY_ENV_VAR, API_KEY_HEADER, ApiKeyMiddleware


def _make_request(path: str, api_key: str | None = None) -> Request:
    headers = []

    if api_key is not None:
        headers.append(
            (API_KEY_HEADER.lower().encode(), api_key.encode())
        )

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "root_path": "",
    }

    return Request(scope)


async def _fake_call_next(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True}, status_code=200)


def _dispatch(path: str, api_key: str | None = None) -> JSONResponse:
    middleware = ApiKeyMiddleware(app=None)
    request = _make_request(path, api_key)

    return asyncio.run(middleware.dispatch(request, _fake_call_next))


def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)

    response = _dispatch("/protected")

    assert response.status_code == 200


def test_auth_disabled_when_env_var_is_blank(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "   ")

    response = _dispatch("/protected")

    assert response.status_code == 200


def test_auth_enabled_rejects_missing_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "secret123")

    response = _dispatch("/protected")

    assert response.status_code == 401


def test_auth_enabled_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "secret123")

    response = _dispatch("/protected", api_key="wrong")

    assert response.status_code == 401


def test_auth_enabled_accepts_correct_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "secret123")

    response = _dispatch("/protected", api_key="secret123")

    assert response.status_code == 200


def test_auth_enabled_still_allows_root_and_health(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "secret123")

    assert _dispatch("/").status_code == 200
    assert _dispatch("/health").status_code == 200


def test_auth_enabled_still_allows_docs_paths(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "secret123")

    assert _dispatch("/docs").status_code == 200
    assert _dispatch("/redoc").status_code == 200
    assert _dispatch("/openapi.json").status_code == 200
