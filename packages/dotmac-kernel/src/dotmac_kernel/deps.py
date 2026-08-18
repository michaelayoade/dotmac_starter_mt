"""Shared route dependencies."""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.capabilities import CAPABILITY_CODE_ATTR, active_capabilities
from dotmac_kernel.entitlements import EntitlementDecision, is_entitled
from dotmac_kernel.models import (
    AuthSession,
    Party,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
)
from dotmac_kernel.permissions import PERMISSION_CODE_ATTR, active_permissions
from dotmac_kernel.security import decode_access_token, hash_token


def get_db(request: Request) -> Generator[Session, None, None]:
    """Thin FastAPI adapter over the database transaction owner.

    The function-local import is load-bearing. Route manifests are imported to
    discover and register modules before an assembly has resolved deployment
    configuration; importing ``dotmac_kernel.db`` here at module scope would
    construct an engine and make package discovery require ``DATABASE_URL``.
    The request still enters the one owner unchanged when FastAPI resolves this
    dependency.
    """
    from dotmac_kernel.db import get_db as transaction_owner

    yield from transaction_owner(request)


def get_platform_db() -> Generator[Session, None, None]:
    """Thin FastAPI adapter over the platform database transaction owner."""
    from dotmac_kernel.db import get_platform_db as transaction_owner

    yield from transaction_owner()


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
            select(PartyRoleGrant)
            .join(
                Role,
                (Role.id == PartyRoleGrant.role_id)
                & (Role.tenant_id == PartyRoleGrant.tenant_id),
            )
            .where(PartyRoleGrant.tenant_id == tenant.id)
            .where(PartyRoleGrant.party_id == party.id)
            .where(Role.tenant_id == tenant.id)
            .where(Role.slug.in_(tuple(role_slugs)))
        ).first()
        is not None
    )


def authorize_party(db: Session, *, tenant: Tenant, party: Party, code: str) -> bool:
    """The ONE permission decision — SoT for every surface, however the party
    was authenticated.

    Exactly the relationship `authenticate_request` has to the bearer and cookie
    auth flows, one layer up: that seam answers "who is this?" without caring
    which transport carried the credential, and this one answers "may they?"
    without caring how they were identified. A bearer route, a cookie-rendered
    admin page and a separate assembly's portal reach the SAME arithmetic, so a
    fix to the tenant scoping, the join, or the code→roles binding lands once.

    Authentication-neutral means the caller has ALREADY proved the party. This
    function never reads a header, a cookie or a session; it takes an
    established `(tenant, party)` and a declared `code`, and returns a bool.
    It raises `UndeclaredPermissionError` for a code no installed module
    declares — fail closed, never fail open (see `dotmac_kernel.permissions`).

    Prefer `permission_guard` on a route: it wraps this with the boot-time
    declaration check that makes a typo'd code stop the boot rather than
    surface as a mystery 403. Call this directly only where a decision is
    needed OUTSIDE a route dependency — inside a service, or a template's
    optional slot.
    """
    spec = active_permissions().require(code)
    return _holds_any_role(
        db, tenant=tenant, party=party, role_slugs=spec.default_roles
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


def _forbidden(request: Request) -> Exception:
    """The API plane's refusal: the same 403 `require_role` gives."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def permission_guard(
    code: str,
    *,
    authenticated_party: Callable[..., Party],
    denied: Callable[[Request], Exception] = _forbidden,
):
    """Return a route dependency enforcing `code`, over ANY authentication.

    The authentication-neutral half of `require_permission`. Two parameters are
    what a surface supplies, and they are the only two things the surfaces
    actually disagree about:

    - `authenticated_party` — a dependency that proves the actor and returns a
      `Party`. Bearer routes pass `require_user_auth`; a cookie surface passes
      its own guard, which reads ITS OWN cookie and calls
      `authenticate_request`. The kernel never learns the cookie's name.
    - `denied` — builds the refusal from the request. It answers the
      AUTHORIZATION question, so it is a permission-denied response (403) on
      every surface, including a rendered portal.

      **A denial must not redirect to login.** By the time `denied` is called
      the actor is already authenticated: `authenticated_party` succeeded. A
      redirect to login tells a signed-in user to sign in, which they cannot
      usefully act on — best case a confusing bounce, worst case a loop as the
      login sees a valid session and sends them back. Unauthenticated is the
      OTHER seam's job: `authenticated_party` raises its own redirect
      (`web_deps.WebAuthRedirect`) before authorization is ever consulted. Keep
      the two answers distinct — "who are you?" redirects, "may you?" refuses.
      A portal that wants a branded 403 renders one; that is still a refusal.

    Everything else — the declaration lookup, the code→roles binding and the
    membership query — is `authorize_party`, shared verbatim.

    **Why a factory rather than a documented recipe.** An assembly with its own
    session cookie (ADR-0021's third plane) must not re-implement the role query
    to get an authorization check; that is how a plane falls behind a kernel
    security fix. It must also not lose the boot-time declaration check, which
    is the whole reason a typo'd code is a startup failure instead of a mystery
    403. This factory hands over both: the stamp below is what `create_app`
    reads back off every mounted route, so a guard built here is validated at
    boot exactly like a first-party one.
    """

    def _dependency(
        request: Request,
        party: Party = Depends(authenticated_party),
        db: Session = Depends(get_db),
    ) -> Party:
        tenant = require_tenant(request)
        if not authorize_party(db, tenant=tenant, party=party, code=code):
            raise denied(request)
        return party

    setattr(_dependency, PERMISSION_CODE_ATTR, code)
    return _dependency


def require_permission(code: str):
    """Return a BEARER dependency requiring the declared permission `code`
    (module control-plane directive step 3).

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

    This is now `permission_guard` bound to the bearer flow, and holds no
    decision of its own — see that factory for a cookie-authenticated surface.
    """
    return permission_guard(code, authenticated_party=require_user_auth)


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


def idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    """The client-supplied `Idempotency-Key` header, or None when absent.

    Optional by design: the kernel takes no position on which routes require a
    key — that is the product's decision, expressed by raising when this returns
    None. What the kernel DOES own is the header's spelling and its length
    limit, so every Dotmac API answers the same way and a key too long for the
    ledger is a 400 at the seam rather than a database error mid-flush.

    Pass the result to a service that wraps its work in
    `dotmac_kernel.idempotency.execute_once`. Routes stay thin (ADR-0010): the
    dependency reads a header, it does not decide anything.
    """
    # Local for the same reason as the DB adapters above: the idempotency owner
    # imports `conflict_savepoint`, which imports the eager engine. Reading a
    # header must not make every package importing route guards require a DSN.
    from dotmac_kernel.idempotency import MAX_KEY_LENGTH

    if idempotency_key is None:
        return None
    trimmed = idempotency_key.strip()
    if not trimmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must not be blank",
        )
    if len(trimmed) > MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Idempotency-Key must be at most " f"{MAX_KEY_LENGTH} characters"),
        )
    return trimmed


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
    "authorize_party",
    "get_db",
    "get_platform_db",
    "idempotency_key",
    "permission_guard",
    "require_capability",
    "require_permission",
    "require_role",
    "require_tenant",
    "require_user_auth",
]
