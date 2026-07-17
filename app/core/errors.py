"""Structured error handlers (ported pattern from dotmac_sub app/errors.py),
with HTML content negotiation for browser clients.

`envelope()`/`_envelope` build the ONE error body shape used everywhere in
the app — these FastAPI handlers, and the hand-built ASGI middleware
responses (tenant resolver, rate limit, CSRF). `_negotiate()` is the single
place that decides JSON vs. branded HTML for that envelope; every handler
below routes through it rather than constructing its own `JSONResponse`, so
adding a new error type can never accidentally skip HTML negotiation. CSRF
middleware (`app.core.middleware.csrf`) imports `_negotiate` directly since
it runs as raw ASGI, outside FastAPI's exception-handler machinery.

Negotiation rule (deliberately simple — see module docstring in the task
brief): a request "prefers HTML" iff `"text/html" in Accept-header`. HTMX
requests send `Accept: text/html, */*` for both full-page and `hx-*`
fragment swaps, so they get the branded HTML page too — correct here, since
an HTML error page IS a valid swap target (unlike a success fragment, which
would need `hx-target`-specific partials this task doesn't add). API clients
that ask for `application/json` (no `text/html` substring) always get the
byte-identical JSON envelope, unchanged from before this task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.logging import request_id_var
from app.core.templating import render

logger = logging.getLogger(__name__)

# Stable machine-readable slugs for bare HTTPExceptions (raised by FastAPI
# internals, auth dependencies, and any leftover route-level raises).
_STATUS_SLUGS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}

# Every status this app has a dedicated branded template for. A status
# outside this map (e.g. 405, 429, 418) still gets a branded HTML page via
# the >=500/else fallback below — never a raw stack trace or blank page —
# just not a status-specific one (there's no meaningful copy difference for
# "method not allowed" vs. generic "bad request").
_HTML_TEMPLATES = {
    400: "errors/400.html",
    401: "errors/401.html",
    403: "errors/403.html",
    404: "errors/404.html",
    409: "errors/409.html",
    422: "errors/422.html",
    500: "errors/500.html",
}


def envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id_var.get(),
    }


_envelope = envelope  # backwards-compatible private alias


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def render_error(
    request: Request, status_code: int, env: dict[str, Any]
) -> HTMLResponse:
    """Render the branded HTML error page for `status_code` from `env`.

    Matches the `render_error(request, status, envelope) -> HTMLResponse`
    interface named in the task brief. `_negotiate` (below) is the JSON-vs-
    HTML decision point and delegates its HTML branch here; call this
    directly only when the caller already knows it wants HTML unconditionally
    (e.g. a future route-level handler outside FastAPI's exception-handler
    machinery).
    """
    # "csrf_failed" gets its own dedicated copy (errors/csrf.html) regardless
    # of status code — it's a 403 like any bare-forbidden HTTPException, but
    # the CSRF-specific explanation ("session expired", "try refreshing")
    # is more actionable than the generic 403 page for this one code.
    if env.get("code") == "csrf_failed":
        template = "errors/csrf.html"
    else:
        fallback = "errors/500.html" if status_code >= 500 else "errors/400.html"
        template = _HTML_TEMPLATES.get(status_code, fallback)
    return render(
        request,
        template,
        {
            "code": env.get("code"),
            "message": env.get("message"),
            "request_id": env.get("request_id"),
        },
        status_code=status_code,
    )


def _negotiate(
    request: Request,
    status_code: int,
    env: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """The single JSON-vs-HTML decision point for every error response.

    `env` is always the same envelope dict `envelope()` builds — HTML
    rendering shows exactly those fields (`code`, `message`, `request_id`),
    it never forks the envelope shape (see module docstring / SoT note in
    `app.core.errors`'s docstring).
    """
    if not _wants_html(request):
        return JSONResponse(status_code=status_code, content=env, headers=headers)

    response = render_error(request, status_code, env)
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    return response


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> Response:
        return _negotiate(request, 404, _envelope("not_found", str(exc)))

    @app.exception_handler(BadRequestError)
    async def _bad_request(request: Request, exc: BadRequestError) -> Response:
        return _negotiate(request, 400, _envelope("bad_request", str(exc)))

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> Response:
        return _negotiate(request, 409, _envelope("conflict", str(exc)))

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(request: Request, exc: UnauthorizedError) -> Response:
        return _negotiate(request, 401, _envelope("unauthorized", str(exc)))

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> Response:
        logger.exception("Unhandled DomainError")
        return _negotiate(request, 500, _envelope("internal_error", "Internal error"))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        code = _STATUS_SLUGS.get(exc.status_code, "http_error")
        return _negotiate(
            request,
            exc.status_code,
            envelope(code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        safe = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return _negotiate(
            request, 422, _envelope("validation_error", "Validation failed", safe)
        )

    @app.exception_handler(Exception)
    async def _catch_all(request: Request, exc: Exception) -> Response:
        logger.exception("Unhandled exception")
        return _negotiate(request, 500, _envelope("internal_error", "Internal error"))
