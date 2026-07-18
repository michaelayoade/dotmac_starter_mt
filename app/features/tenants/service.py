"""Platform-admin tenant service — provision/list tenants.

Business logic and the only `select()`/session-mutation calls for the tenant
domain live here; `app/features/tenants/router.py` only resolves dependencies,
calls these functions, and shapes the response.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import conflict_savepoint
from app.core.exceptions import ConflictError, NotFoundError
from app.core.models import Tenant
from app.features.tenants.schemas import TenantCreate


def list_tenants(db: Session) -> list[Tenant]:
    return list(db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all())


def get_tenant(db: Session, tenant_id: UUID) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return tenant


def create_tenant(db: Session, payload: TenantCreate) -> Tenant:
    tenant = Tenant(slug=payload.slug, name=payload.name)
    # `db.add` happens INSIDE the savepoint, not before it: `Session.
    # begin_nested()` auto-flushes any already-pending changes as part of
    # establishing the SAVEPOINT (`_take_snapshot`) — adding `tenant` before
    # entering `conflict_savepoint` would let that pre-flush emit the
    # conflicting INSERT with no savepoint yet in place to protect the
    # outer transaction's `SET LOCAL` if it fails. See
    # `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            db.add(tenant)
            db.flush()
            db.refresh(tenant)
    except IntegrityError as exc:
        raise ConflictError("Slug already in use") from exc
    return tenant
