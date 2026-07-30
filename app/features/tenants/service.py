"""Platform-admin tenant service — atomic provisioning + listing.

Business logic and the only `select()`/session-mutation calls for the tenant
domain live here; `app/features/tenants/router.py` only resolves dependencies,
calls these functions, and shapes the response.

Provisioning (control-plane security Task 2) is ONE transaction on the
platform session (`get_platform_db` owns commit/rollback): tenant row →
`SET LOCAL app.current_tenant` → owner `Party(person)` + `PartyPerson` +
`UserCredential` → `admin` `Role` + `PartyRole` grant → two audit events.
Any failure anywhere rolls the WHOLE thing back — a tenant without a
login-able owner can never persist.

The `SET LOCAL` matters: `platform_api` has NO BYPASSRLS, and every
owner-side table is FORCE-RLS — the tenant context must be established
explicitly (same `set_config(..., true)` idiom as `get_db`) after the tenant
row is flushed and before any tenant-scoped write.

`UserCredential` is composed from `app.core.models` (moved there this task —
PORT-DELTA): `tenants` cannot import the `auth` feature, and core owns no
feature logic, so the model joined the other identity models under
ADR-0002's placement rule. Hashing stays in `app.core.security`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit_event
from app.core.db import conflict_savepoint
from app.core.exceptions import ConflictError, NotFoundError
from app.core.identity import normalize_email, person_display_name
from app.core.models import (
    Party,
    PartyPerson,
    PartyRole,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from app.core.security import hash_password
from app.features.tenants.schemas import TenantProvision

# Bound on the platform tenant listing (Task 2 step 4): closes the unbounded
# listing now rather than waiting for the list_query envelope. Requested
# limits are CLAMPED into [1, MAX_PAGE_SIZE], not rejected.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def list_tenants(
    db: Session, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
) -> list[Tenant]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    return list(
        db.scalars(
            select(Tenant)
            .order_by(Tenant.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def get_tenant(db: Session, tenant_id: UUID) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return tenant


def provision_tenant(
    db: Session, payload: TenantProvision, *, actor_email: str
) -> Tenant:
    """Create tenant + login-able owner + admin grant + audit trail,
    atomically, as the platform actor `actor_email` (recorded in both audit
    events' details — platform admins are not tenant parties, so
    `actor_party_id` stays NULL and the actor is named in `details`)."""
    tenant = Tenant(slug=payload.slug, name=payload.name)
    # `db.add` happens INSIDE the savepoint, not before it (see
    # `conflict_savepoint`'s docstring — begin_nested auto-flushes pending
    # state while establishing the SAVEPOINT).
    try:
        with conflict_savepoint(db):
            db.add(tenant)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("Slug already in use") from exc

    # Establish RLS tenant context ON THIS TRANSACTION before any
    # tenant-scoped write — platform_api has no BYPASSRLS and the owner-side
    # tables are FORCE-RLS. Same `set_config(..., is_local := true)` idiom
    # as `get_db`; SET LOCAL scope dies with the transaction either way.
    db.execute(
        select(func.set_config("app.current_tenant", str(tenant.id), True))
    ).scalar_one()

    email = normalize_email(str(payload.owner_email))
    owner = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=person_display_name(
            payload.owner_first_name, payload.owner_last_name
        ),
        email=email,
    )
    db.add(owner)
    db.flush()
    db.add(
        PartyPerson(
            party_id=owner.id,
            first_name=payload.owner_first_name,
            last_name=payload.owner_last_name,
        )
    )
    db.add(
        UserCredential(
            tenant_id=tenant.id,
            party_id=owner.id,
            password_hash=hash_password(payload.owner_password),
        )
    )
    role = Role(tenant_id=tenant.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRole(tenant_id=tenant.id, party_id=owner.id, role_id=role.id))
    db.flush()

    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=None,
        action="platform.tenant.create",
        entity_type="tenant",
        entity_id=str(tenant.id),
        details={"platform_actor": actor_email, "slug": tenant.slug},
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=None,
        action="platform.tenant.owner_provision",
        entity_type="party",
        entity_id=str(owner.id),
        details={"platform_actor": actor_email, "owner_email": email},
    )
    db.refresh(tenant)
    return tenant
