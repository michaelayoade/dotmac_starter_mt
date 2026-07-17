"""Tenant-scoped RBAC service.

All `select()`/session-mutation calls for the RBAC domain live here —
`app/features/rbac/router.py` only resolves dependencies, calls these
functions, writes the audit trail, and shapes the response.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent
from app.core.exceptions import ConflictError, NotFoundError
from app.core.models import Person, PersonRole, Role, Tenant
from app.core.query import apply_pagination
from app.features.rbac.schemas import RoleCreate, RoleGrantRequest


def list_roles(db: Session, tenant: Tenant, *, limit: int, offset: int) -> list[Role]:
    # Explicit tenant filter (unlike list_persons' RLS-only approach) — RLS also
    # enforces this at the DB layer, but the scoping-convention triage calls for an
    # explicit filter here too: it keeps the query self-describing and correct even
    # if RLS were ever misconfigured for this table.
    stmt = (
        select(Role).where(Role.tenant_id == tenant.id).order_by(Role.created_at.desc())
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).all())


def create_role(db: Session, tenant: Tenant, payload: RoleCreate) -> Role:
    role = Role(tenant_id=tenant.id, slug=payload.slug, name=payload.name)
    db.add(role)
    try:
        db.flush()
        db.refresh(role)
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Role already exists") from exc
    return role


def assign_role(db: Session, tenant: Tenant, payload: RoleGrantRequest) -> PersonRole:
    person = db.scalars(
        select(Person)
        .where(Person.tenant_id == tenant.id)
        .where(Person.id == payload.person_id)
    ).first()
    role = db.scalars(
        select(Role)
        .where(Role.tenant_id == tenant.id)
        .where(Role.id == payload.role_id)
    ).first()
    if person is None or role is None:
        raise NotFoundError("Person or role not found")

    person_role = PersonRole(tenant_id=tenant.id, person_id=person.id, role_id=role.id)
    db.add(person_role)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Role already assigned") from exc
    return person_role


def list_audit_events(db: Session, tenant: Tenant) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant.id)
            .order_by(AuditEvent.created_at.desc())
        ).all()
    )


__all__ = [
    "assign_role",
    "create_role",
    "list_audit_events",
    "list_roles",
]
