"""Shared route dependencies."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.capabilities import CAPABILITY_CODE_ATTR, active_capabilities
from dotmac_kernel.db import get_db, get_platform_db
from dotmac_kernel.entitlements import EntitlementDecision, is_entitled
from dotmac_kernel.models import AuthSession, Party, PartyRole, PartyType, Role, Tenant
from dotmac_kernel.permissions import PERMISSION_CODE_ATTR, active_permissions
from dotmac_kernel.security import decode_access_token, hash_token


def require_tenant(request: Request) -> Tenant:
    """For routes that operate on a tenant-scoped resource."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def authenticate_request(request: Request, db: Session, *, token: str) -> Party | None:
    """The ONE token+session+party validation path — SoT for both the bearer
    (API) and cookie (web) auth flows.

    Pure predicate: returns the authenticated `Party` or `None` on ANY
    failure — it never raises. Callers decide how a `None` becomes a
    response: `require_user_auth` (below) turns it into a 401
    `HTTPException`; `dotmac_kernel.web_deps.require_web_auth` turns it into a
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


def _holds_any_role(
    db: Session, *, tenant: Tenant, party: Party, role_slugs: Sequence[str]
) -> bool:
    """The ONE role-membership query — shared by `require_role` (one slug) and
    `require_permission` (the declared permission's `default_roles`). One
    implementation on purpose: a fix to the tenant scoping or the join lands
    once and both guards get it, exactly as `authenticate_request` is the one
    token/session seam behind both the bearer and cookie flows."""
    return (
        db.scalars(
            select(PartyRole)
            .join(
                Role,
                (Role.id == PartyRole.role_id)
                & (Role.tenant_id == PartyRole.tenant_id),
            )
            .where(PartyRole.tenant_id == tenant.id)
            .where(PartyRole.party_id == party.id)
            .where(Role.tenant_id == tenant.id)
            .where(Role.slug.in_(tuple(role_slugs)))
        ).first()
        is not None
    )


def require_role(role_slug: str):
    """Return a dependency that requires the current party to hold `role_slug`.

    The RAW role check. Prefer `require_permission` for a new route: it names
    the authorization DECISION (a declared, owned permission code) rather than
    one role slug that happens to satisfy it today.
    """

    def _dependency(
        request: Request,
        party: Party = Depends(require_user_auth),
        db: Session = Depends(get_db),
    ) -> Party:
        tenant = require_tenant(request)
        if not _holds_any_role(db, tenant=tenant, party=party, role_slugs=(role_slug,)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return party

    return _dependency


def require_permission(code: str):
    """Return a dependency that requires the current party to hold the declared
    permission `code` (module control-plane directive step 3).

    The declaration-driven guard, layered over `require_role`'s role check:
    `code` must be declared by an installed module's manifest
    (`permissions=(PermissionSpec(...),)`), and the actor satisfies it by
    holding any of that spec's `default_roles`. The route therefore names the
    DECISION, and the owning module — not the call site — decides which roles
    satisfy it.

    Two independent failure modes, deliberately at different times:

    - **Undeclared code → boot failure.** The returned dependency is stamped
      with `code` (`PERMISSION_CODE_ATTR`), and `create_app` walks every mounted
      route and validates each stamped code against the catalogue. A typo stops
      the boot, not the first request that reaches the route.
    - **Actor lacks the permission → 403** at request time, the same response
      `require_role` gives, so migrating a route from one to the other changes
      no observable behavior while the declared `default_roles` match.

    A code that is somehow undeclared at request time (no catalogue installed —
    see `dotmac_kernel.permissions`) raises `UndeclaredPermissionError` rather
    than allowing the request: fail closed, never fail open.
    """

    def _dependency(
        request: Request,
        party: Party = Depends(require_user_auth),
        db: Session = Depends(get_db),
    ) -> Party:
        tenant = require_tenant(request)
        spec = active_permissions().require(code)
        if not _holds_any_role(
            db, tenant=tenant, party=party, role_slugs=spec.default_roles
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return party

    setattr(_dependency, PERMISSION_CODE_ATTR, code)
    return _dependency


def require_capability(code: str):
    """Return a dependency that requires the TENANT to be entitled to `code`
    (module control-plane directive step 4).

    **A different question from `require_permission`, and the two compose.**
    A permission asks "may this ACTOR do it?"; a capability asks "does this
    TENANT have the feature at all?". An admin of a tenant that never bought
    custom fields holds every relevant permission and is still not entitled, and
    a viewer in a tenant that did buy them is entitled and still not permitted.
    Collapsing them would make one decision answer for both, which is how a
    plan-name check ends up hardcoded inside a route.

    The decision is LOCAL and explainable: `is_entitled` is a pure read of the
    tenant's grant store (`dotmac_kernel.entitlements`). A request-time check
    never calls a payment provider and never validates a licence over the
    network — ADR-0003 is explicit about that, and it is why the signed-licence
    receiver PROJECTS into grants rather than being consulted per request.

    Two failure modes, deliberately at different times, mirroring
    `require_permission`:

    - **Undeclared code → boot failure.** The returned dependency is stamped
      with `code` (`CAPABILITY_CODE_ATTR`) and `create_app` validates every
      mounted route's stamped codes against the installed catalogue. A typo
      stops the boot rather than silently denying every request to that route
      forever — which would read as an entitlement bug, not a declaration one.
    - **Tenant not entitled → 403** at request time, carrying the decision's
      stable `reason` code (`not_granted` / `revoked`) so an operator can tell
      "never had it" from "had it and lost it" without reading the database.

    Deny-by-default: no grant row means not entitled. A capability whose
    catalogue was never installed raises `UndeclaredCapabilityError` rather than
    allowing the request — fail closed, never fail open.
    """

    def _dependency(
        request: Request,
        db: Session = Depends(get_db),
    ) -> EntitlementDecision:
        tenant = require_tenant(request)
        active_capabilities().require(code)
        decision = is_entitled(db, tenant_id=tenant.id, capability_code=code)
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                # The reason is a stable, language-neutral code — never a
                # payment or licence status, which a request-time check does
                # not know and must not imply.
                detail={
                    "error": "not_entitled",
                    "capability": code,
                    "reason": decision.reason,
                },
            )
        # Returned, not discarded: a route that needs the grant's `limits` (a
        # seat count, a quota) reads them from the same decision that admitted
        # it, rather than issuing a second, possibly-disagreeing read.
        return decision

    setattr(_dependency, CAPABILITY_CODE_ATTR, code)
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
    "require_capability",
    "require_permission",
    "require_role",
    "require_tenant",
    "require_user_auth",
]
