"""Targeted coverage for the tenant-resolution health-path bypass.

`_HEALTH_PATHS` short-circuits `TenantResolverMiddleware.dispatch` before it
ever calls `self._resolve()` (and therefore `SessionLocal()`), because health
probes must succeed even when the DB is unreachable (see
app/core/middleware/tenant.py's module docstring and the "/health does not
touch DB" contract referenced there). This file drives the real middleware
over a raw ASGI scope (mirroring tests/unit/test_logging.py's middleware
test) to prove:

- exact `_HEALTH_PATHS` members bypass resolution entirely — the DB seam
  (`SessionLocal`) is never touched
- near-miss paths ("/health2", "/health/ready/x") are NOT exempted and must
  still go through `_resolve()` / `SessionLocal()`
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest
from dotmac_kernel.middleware import tenant as tenant_module
from dotmac_kernel.middleware.tenant import TenantResolverMiddleware


async def _ok_inner_app(scope, receive, send) -> None:
    """Minimal ASGI inner app: always responds 200 OK."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b"{}", "more_body": False})


def _drive(
    app: TenantResolverMiddleware, path: str, host: str = "example.com"
) -> tuple[dict, int]:
    """Drive one ASGI request through `app`; return (scope, response status).

    `scope["state"]` is the same dict `request.state` writes through (Starlette's
    `State` wraps `scope["state"]` directly), so asserting on it after the call
    proves what the middleware set on `request.state`.
    """

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    status: dict[str, int] = {}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            status["code"] = message["status"]  # type: ignore[assignment]

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "scheme": "http",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    asyncio.run(app(scope, receive, send))
    return scope, status.get("code", 0)


def _exploding_session_local(*args: object, **kwargs: object) -> None:
    raise AssertionError(
        "SessionLocal() was called while handling a health-path request; "
        "health checks must not touch the DB."
    )


def _recording_session_local() -> tuple[Callable[..., object], dict[str, int]]:
    """Fake SessionLocal that records each open and resolves to 'no tenant'.

    Used for the near-miss paths, which must reach the DB seam but should
    resolve benignly (no tenant found) rather than raise, so the test proves
    the seam was invoked without needing a real database.
    """
    calls = {"count": 0}

    class _FakeResult:
        def first(self) -> None:
            return None

    class _FakeSession:
        def __enter__(self) -> _FakeSession:
            calls["count"] += 1
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

        def scalars(self, *args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    def _factory(*args: object, **kwargs: object) -> _FakeSession:
        return _FakeSession()

    return _factory, calls


@pytest.mark.parametrize("path", ["/health", "/health/ready"])
def test_health_paths_bypass_tenant_resolution_without_db_access(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Exact `_HEALTH_PATHS` members short-circuit before `_resolve()`.

    The DB seam (`SessionLocal`) is stubbed to raise `AssertionError` on any
    call. If the middleware attempted resolution for a health path, this test
    would fail with that AssertionError propagating out of `app(...)` instead
    of reaching the response assertions below.
    """
    monkeypatch.setattr(tenant_module, "SessionLocal", _exploding_session_local)
    app = TenantResolverMiddleware(_ok_inner_app)

    scope, status = _drive(app, path)

    assert status == 200
    assert scope["state"]["tenant"] is None


def test_unresolved_tenant_404_uses_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middleware's hand-built 404 must match the app-wide error envelope."""
    session_local, _ = _recording_session_local()
    monkeypatch.setattr(tenant_module, "SessionLocal", session_local)
    app = TenantResolverMiddleware(_ok_inner_app)

    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/anything",
        "raw_path": b"/anything",
        "query_string": b"",
        "headers": [(b"host", b"nosuch.example.com")],
        "scheme": "http",
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    asyncio.run(app(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 404
    body = json.loads(
        b"".join(
            m.get("body", b"")  # type: ignore[arg-type]
            for m in messages
            if m["type"] == "http.response.body"
        )
    )
    assert body["code"] == "tenant_not_found"
    assert body["message"] == "Tenant not found"
    assert "request_id" in body
    assert "detail" not in body


@pytest.mark.parametrize("path", ["/health2", "/health/ready/x"])
def test_near_miss_paths_do_not_bypass_resolution(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Paths that merely resemble health paths must still hit `_resolve()`.

    A near-miss path is not in `_HEALTH_PATHS`, so `dispatch` must fall
    through to `self._resolve(host)`, which opens a DB session. The recording
    stub proves that seam was actually invoked (rather than the request
    failing/erroring before ever reaching it).
    """
    session_local, calls = _recording_session_local()
    monkeypatch.setattr(tenant_module, "SessionLocal", session_local)
    app = TenantResolverMiddleware(_ok_inner_app)

    _drive(app, path)

    assert calls["count"] >= 1, (
        "resolver did not open a DB session for a near-miss path; "
        "the health-path bypass may be over-matching"
    )


# ---------------------------------------------------------------------------
# Static-asset bypass (backlog fix): `/static/*` must not touch the DB
# either, for the same reason as health paths — before this fix, a request
# for a static asset with the DB unreachable 500'd instead of serving the
# file, because `dispatch` unconditionally opened `SessionLocal()`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/static", "/static/css/main.css", "/static/js/htmx.min.js"]
)
def test_static_paths_bypass_tenant_resolution_without_db_access(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Exact `/static` and anything under `/static/` short-circuit before
    `_resolve()` — mirrors the health-path bypass test above."""
    monkeypatch.setattr(tenant_module, "SessionLocal", _exploding_session_local)
    app = TenantResolverMiddleware(_ok_inner_app)

    scope, status = _drive(app, path)

    assert status == 200
    assert scope["state"]["tenant"] is None


@pytest.mark.parametrize("path", ["/staticevil", "/static2/x"])
def test_static_near_miss_paths_do_not_bypass_resolution(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """`/staticevil` and `/static2/x` merely start with the string "static"
    but are neither `/static` nor under `/static/` — a naive
    `path.startswith("/static")` check (no trailing slash) would wrongly
    bypass these. They must still hit `_resolve()` / `SessionLocal()`."""
    session_local, calls = _recording_session_local()
    monkeypatch.setattr(tenant_module, "SessionLocal", session_local)
    app = TenantResolverMiddleware(_ok_inner_app)

    _drive(app, path)

    assert calls["count"] >= 1, (
        "resolver did not open a DB session for a near-miss static path; "
        "the static-path bypass may be over-matching"
    )
