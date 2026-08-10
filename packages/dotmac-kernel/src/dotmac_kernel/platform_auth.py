"""Platform-admin authentication — the ONE platform guard + its auth routes.

Platform actors get their own identity (`dotmac_kernel.models_platform`) and
their own guard (`require_platform_admin`), deliberately separate from the
tenant `Party`/`AuthSession` model — see that module's docstring and
ADR-0004.

DECISION (Task 1): the platform auth endpoints live HERE, in core, with a
router mounted directly in `app/main.py` — NOT as a feature manifest. The
manifest/capability model exists for TENANT capabilities (per-tenant
enablement, admin-portal surfaces); the platform control plane must exist
even with every feature disabled (`DISABLED_FEATURES=*`, `WEB_ENABLED=false`)
or there would be no way to operate the deployment at all.

Token model — one signer, two token populations that can never cross:
- Tenant tokens (`dotmac_kernel.security.issue_access_token`): `sub` = party id,
  `tenant_id` claim, NO `aud` claim.
- Platform tokens (`issue_platform_token` below): `sub` = platform-admin id,
  `aud="platform"`, no tenant claim.
`require_platform_admin` rejects any token whose `aud` is not exactly
`"platform"`, and `dotmac_kernel.deps.authenticate_request` requires a matching
`tenant_id` claim platform tokens don't carry — so each surface structurally
rejects the other population's tokens.

Host model: platform routes resolve ONLY on `settings.platform_root_domain`.
The middleware already refuses to treat `/platform/*` on any other host as
platform-valid (`dotmac_kernel.middleware.tenant._is_platform_path`, host-exact);
the guard re-checks the host itself as defense-in-depth so a middleware
regression alone cannot re-expose the surface.

OIDC: deliberately NOT the default platform identity — a starter cannot
assume an IdP exists. `require_platform_admin` is the single seam a project
replaces to plug in OIDC (swap token validation; keep the host check and the
active-admin check). Documented in ADR-0004.

Bootstrap: `scripts/create_platform_admin.py` (CLI, platform/migration DB
credentials — the same trust boundary as running migrations). There is no
HTTP self-registration path for platform admins, ever.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_kernel.config import settings
from dotmac_kernel.db import get_platform_db
from dotmac_kernel.exceptions import UnauthorizedError
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.security import (
    decode_access_token,
    encode_jwt,
    hash_password,
    hash_token,
    verify_password,
)

PLATFORM_AUDIENCE = "platform"

# Constant-work login: verified against when the email doesn't resolve to an
# active admin, so the "no such admin" and "wrong password" paths both run
# exactly one password verification.
_DUMMY_HASH = hash_password("platform-dummy-password-for-constant-work")


def _request_host(request: Request) -> str:
    return (request.headers.get("host") or "").split(":")[0].lower()


def _platform_root() -> str:
    return settings.platform_root_domain.lower().lstrip(".")


def require_platform_host(request: Request) -> None:
    """Pre-auth platform-surface guard: the platform surface only EXISTS on
    the platform root host — anywhere else it 404s (mirroring the
    middleware's decision for unresolved paths). This is the guard for the
    genuinely pre-auth platform routes (login); everything else uses
    `require_platform_admin`, which embeds the same check."""
    if _request_host(request) != _platform_root():
        raise HTTPException(status_code=404, detail="Not found")


def issue_platform_token(admin_id: UUID) -> tuple[str, datetime]:
    """Signed platform-audience bearer token + its expiry."""
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.jwt_ttl_seconds)
    payload = {
        "sub": str(admin_id),
        "aud": PLATFORM_AUDIENCE,
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return encode_jwt(payload), expires_at


def authenticate_platform_request(
    request: Request, db: Session, *, token: str
) -> PlatformAdmin | None:
    """Pure predicate mirror of `dotmac_kernel.deps.authenticate_request` for the
    platform surface: returns the authenticated `PlatformAdmin` or `None` on
    ANY failure — host mismatch, bad signature/expiry, wrong/missing `aud`,
    no live `platform_sessions` row, or inactive admin. Never raises."""
    if _request_host(request) != _platform_root():
        return None

    payload = decode_access_token(token)
    if payload is None or payload.get("aud") != PLATFORM_AUDIENCE:
        return None

    try:
        admin_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        return None

    session = db.scalars(
        select(PlatformSession)
        .where(PlatformSession.token_hash == hash_token(token))
        .where(PlatformSession.revoked_at.is_(None))
        .where(PlatformSession.expires_at > datetime.now(UTC))
    ).first()
    if session is None or session.admin_id != admin_id:
        return None

    admin = db.get(PlatformAdmin, admin_id)
    if admin is None or not admin.is_active:
        return None
    return admin


def require_platform_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_platform_db),
) -> PlatformAdmin:
    """THE platform guard: host-exact (404 off the root host — the surface
    does not exist there), then bearer-token platform authentication (401)."""
    require_platform_host(request)
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    admin = authenticate_platform_request(request, db, token=token)
    if admin is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return admin


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


# ---------------------------------------------------------------------------
# Service functions (the only DB-touching logic; route handlers below stay
# thin, same contract as feature router/service splits).
# ---------------------------------------------------------------------------


def login(db: Session, *, email: str, password: str) -> tuple[str, datetime]:
    """Verify a platform admin's credentials and issue a session-backed
    token. Constant-work on the miss paths (see `_DUMMY_HASH`)."""
    normalized = email.strip().lower()
    admin = db.scalars(
        select(PlatformAdmin).where(func.lower(PlatformAdmin.email) == normalized)
    ).first()
    if admin is None or not admin.is_active:
        verify_password(password, _DUMMY_HASH)
        raise UnauthorizedError("Invalid credentials")
    if not verify_password(password, admin.password_hash):
        raise UnauthorizedError("Invalid credentials")

    token, expires_at = issue_platform_token(admin.id)
    db.add(
        PlatformSession(
            admin_id=admin.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return token, expires_at


def logout(db: Session, *, token: str | None) -> None:
    """Revoke the `PlatformSession` backing `token`, if any. No-ops on a
    missing/already-revoked token — logout always succeeds for the caller."""
    if not token:
        return
    session = db.scalars(
        select(PlatformSession)
        .where(PlatformSession.token_hash == hash_token(token))
        .where(PlatformSession.revoked_at.is_(None))
    ).first()
    if session is None:
        return
    session.revoked_at = datetime.now(UTC)
    db.flush()


# ---------------------------------------------------------------------------
# Routes — mounted directly in app/main.py (see module docstring DECISION).
# ---------------------------------------------------------------------------


class PlatformLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PlatformTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


platform_auth_router = APIRouter(prefix="/platform/auth", tags=["platform"])


@platform_auth_router.post(
    "/login",
    response_model=PlatformTokenResponse,
    dependencies=[Depends(require_platform_host)],
)
def platform_login(
    payload: PlatformLoginRequest, db: Session = Depends(get_platform_db)
) -> PlatformTokenResponse:
    token, _ = login(db, email=payload.email, password=payload.password)
    return PlatformTokenResponse(access_token=token)


@platform_auth_router.post("/logout", status_code=204)
def platform_logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_platform_db),
    _admin: PlatformAdmin = Depends(require_platform_admin),
) -> Response:
    logout(db, token=_bearer_token(authorization))
    return Response(status_code=204)


__all__ = [
    "PLATFORM_AUDIENCE",
    "platform_auth_router",
    "require_platform_admin",
]
