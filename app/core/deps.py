"""Shared route dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db, get_platform_db
from app.core.models import AuthSession, Party, PartyRole, PartyType, Role, Tenant
from app.core.security import decode_access_token, hash_token


def require_tenant(request: Request) -> Tenant:
    """For routes that operate on a tenant-scoped resource."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def require_platform(request: Request) -> None:
    """For routes that operate platform-wide (no tenant context).

    Real implementations should additionally check a `platform_admin` role on the
    actor — stubbed here.
    """
    if getattr(request.state, "tenant", None) is not None:
        raise HTTPException(
            status_code=404,
            detail="Platform routes are not available on tenant subdomains",
        )


def authenticate_request(request: Request, db: Session, *, token: str) -> Party | None:
    """The ONE token+session+party validation path — SoT for both the bearer
    (API) and cookie (web) auth flows.

    Pure predicate: returns the authenticated `Party` or `None` on ANY
    failure — it never raises. Callers decide how a `None` becomes a
    response: `require_user_auth` (below) turns it into a 401
    `HTTPException`; `app.core.web_deps.require_web_auth` turns it into a
    `WebAuthRedirect` 302. This function does NOT resolve/require a tenant
    itself — it reads whatever `request.state.tenant` already holds (set by
    `TenantResolverMiddleware` before the route ever runs); if that's `None`
    it fails closed rather than raising, since a 404-vs-401-vs-redirect
    choice belongs to the caller, not this shared seam.

    Only `party_type == PartyType.person` parties can authenticate —
    organization parties have no credentials and can never be the `sub` of a
    session token, but the check is defense-in-depth against a stray/garbled
    token claiming an org party's id.
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return None

    payload = decode_access_token(token)
    if payload is None or payload.get("tenant_id") != str(tenant.id):
        return None

    try:
        party_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        return None

    session = db.scalars(
        select(AuthSession)
        .where(AuthSession.tenant_id == tenant.id)
        .where(AuthSession.token_hash == hash_token(token))
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at > datetime.now(UTC))
    ).first()
    if session is None:
        return None
    if session.party_id != party_id or session.tenant_id != tenant.id:
        return None

    party = db.get(Party, party_id)
    if party is None or party.party_type != PartyType.person:
        return None
    return party


def require_user_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Party:
    """Validate JWT/session (bearer header) and return the tenant-local party.

    Thin wrapper around `authenticate_request` (the shared validation seam)
    — behavior/signature unchanged from before the Task 3 refactor: same
    404 (missing tenant, via `require_tenant`) then 401 (everything else)
    ordering, proven by the existing auth unit+integration tests passing
    unmodified.
    """
    require_tenant(request)
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    party = authenticate_request(request, db, token=token)
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return party


def require_role(role_slug: str):
    """Return a dependency that requires the current party to hold `role_slug`."""

    def _dependency(
        request: Request,
        party: Party = Depends(require_user_auth),
        db: Session = Depends(get_db),
    ) -> Party:
        tenant = require_tenant(request)
        has_role = db.scalars(
            select(PartyRole)
            .join(
                Role,
                (Role.id == PartyRole.role_id)
                & (Role.tenant_id == PartyRole.tenant_id),
            )
            .where(PartyRole.tenant_id == tenant.id)
            .where(PartyRole.party_id == party.id)
            .where(Role.tenant_id == tenant.id)
            .where(Role.slug == role_slug)
        ).first()
        if has_role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return party

    return _dependency


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


__all__ = [
    "Depends",
    "authenticate_request",
    "get_db",
    "get_platform_db",
    "require_platform",
    "require_role",
    "require_tenant",
    "require_user_auth",
]
