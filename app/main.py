"""FastAPI app entrypoint.

Middleware order (outermost → innermost):
1. TrustedHostMiddleware — drops requests to unknown hosts (prod)
2. TenantResolverMiddleware — sets request.state.tenant
3. (CSRF, rate limit, auth — add as you port from dotmac_starter)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.persons import router as persons_router
from app.api.tenants import router as tenants_router
from app.config import settings, validate_settings
from app.middleware.tenant import TenantResolverMiddleware
from app.services.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    NotFoundError,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    errors = validate_settings(settings)
    for err in errors:
        if settings.is_production:
            raise RuntimeError(f"Configuration error: {err}")
        logger.warning("Config: %s", err)
    yield


app = FastAPI(title="dotmac_starter_mt", lifespan=lifespan)

# Trusted hosts — only enable in prod with explicit list.
if settings.trusted_hosts:
    hosts = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

# Resolver runs after TrustedHost so we never resolve an untrusted host.
app.add_middleware(TenantResolverMiddleware)


# Domain exception handlers — same envelope shape as dotmac_starter.
@app.exception_handler(NotFoundError)
async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(BadRequestError)
async def _bad_request(_: Request, exc: BadRequestError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(DomainError)
async def _domain_fallback(_: Request, exc: DomainError) -> JSONResponse:
    logger.exception("Unhandled DomainError")
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness — does not touch DB."""
    return {"status": "ok"}


app.include_router(tenants_router)
app.include_router(auth_router)
app.include_router(persons_router)
