"""Structured JSON error handlers (ported pattern from dotmac_sub app/errors.py).

Phase 3 (web UI) adds HTML content negotiation here; keep `envelope` reusable.
Every error body in the app — handlers here and the hand-built middleware
responses (tenant resolver, rate limit, CSRF) — uses this one envelope shape.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.logging import request_id_var

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


def envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id_var.get(),
    }


_envelope = envelope  # backwards-compatible private alias


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=_envelope("not_found", str(exc)))

    @app.exception_handler(BadRequestError)
    async def _bad_request(_: Request, exc: BadRequestError) -> JSONResponse:
        return JSONResponse(status_code=400, content=_envelope("bad_request", str(exc)))

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=_envelope("conflict", str(exc)))

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(_: Request, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content=_envelope("unauthorized", str(exc))
        )

    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        logger.exception("Unhandled DomainError")
        return JSONResponse(
            status_code=500, content=_envelope("internal_error", "Internal error")
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_SLUGS.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_error", "Validation failed", safe),
        )

    @app.exception_handler(Exception)
    async def _catch_all(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500, content=_envelope("internal_error", "Internal error")
        )
