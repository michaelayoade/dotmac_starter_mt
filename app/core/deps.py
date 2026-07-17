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


def require_user_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Party:
    """Validate JWT/session and return the tenant-local party.

    Only `party_type == PartyType.person` parties can authenticate —
    organization parties have no credentials and can never be the `sub` of a
    session token, but the check is defense-in-depth against a stray/garbled
    token claiming an org party's id.
    """
    tenant = require_tenant(request)
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    payload = decode_access_token(token)
    if payload is None or payload.get("tenant_id") != str(tenant.id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    try:
        party_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        ) from None

    session = db.scalars(
        select(AuthSession)
        .where(AuthSession.tenant_id == tenant.id)
        .where(AuthSession.token_hash == hash_token(token))
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at > datetime.now(UTC))
    ).first()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    if session.party_id != party_id or session.tenant_id != tenant.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    party = db.get(Party, party_id)
    if party is None or party.party_type != PartyType.person:
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
    "get_db",
    "get_platform_db",
    "require_platform",
    "require_role",
    "require_tenant",
    "require_user_auth",
]
