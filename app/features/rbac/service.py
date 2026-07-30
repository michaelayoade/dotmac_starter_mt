"""Tenant-scoped RBAC service.

All `select()`/session-mutation calls for the RBAC domain live here —
`app/features/rbac/router.py` (JSON API) and `app/features/rbac/web.py`
(admin portal, Task 6) only resolve dependencies, call these functions,
write the audit trail, and shape the response.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import Party, PartyRole, Role, Tenant
from dotmac_kernel.query import apply_pagination, escape_like
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import resolve_value
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.features.rbac.schemas import RoleCreate, RoleGrantRequest

# Fallback if the settings feature is disabled in this deployment — matches
# the `audit/retention_days` spec's own default (app/features/settings/spec.py).
_DEFAULT_AUDIT_RETENTION_DAYS = 365


def list_roles(db: Session, tenant: Tenant, *, limit: int, offset: int) -> list[Role]:
    # Explicit tenant filter (unlike list_parties' RLS-only approach) — RLS also
    # enforces this at the DB layer, but the scoping-convention triage calls for an
    # explicit filter here too: it keeps the query self-describing and correct even
    # if RLS were ever misconfigured for this table.
    stmt = (
        select(Role).where(Role.tenant_id == tenant.id).order_by(Role.created_at.desc())
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).all())


def count_roles(db: Session, tenant: Tenant) -> int:
    """Total role count for `tenant` — powers the `/admin/roles` screen's
    pagination the same way `parties.service.count_parties` powers parties'
    (Task 6).
    """
    return (
        db.scalar(
            select(func.count()).select_from(Role).where(Role.tenant_id == tenant.id)
        )
        or 0
    )


def create_role(db: Session, tenant: Tenant, payload: RoleCreate) -> Role:
    role = Role(tenant_id=tenant.id, slug=payload.slug, name=payload.name)
    # `db.add` happens INSIDE the savepoint, not before it: `Session.
    # begin_nested()` auto-flushes any already-pending changes as part of
    # establishing the SAVEPOINT (`_take_snapshot`) — adding `role` before
    # entering `conflict_savepoint` would let that pre-flush emit the
    # conflicting INSERT with no savepoint yet in place to protect the
    # outer transaction's `SET LOCAL` if it fails. See
    # `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            db.add(role)
            db.flush()
            db.refresh(role)
    except IntegrityError as exc:
        raise ConflictError("Role already exists") from exc
    return role


def list_grantable_parties(
    db: Session, tenant: Tenant, *, q: str | None, limit: int
) -> list[Party]:
    """Party lookup for the `/admin/role-grants` form's party picker (Task 6).

    `rbac`'s `web.py` cannot import `app.features.parties.service` (feature
    independence — features never import each other, per pyproject.toml's
    import-linter contract), so this queries the CORE `Party` model
    directly — legal, since `Party` lives in `dotmac_kernel.models` (see that
    module's docstring for why: it's the fleet-wide identity source of
    truth and both `deps`/`middleware` need to query it directly from core).

    Explicit tenant filter (same convention as `list_roles` above) plus RLS
    as the defense-in-depth backstop. LIKE-escaping via the shared
    `dotmac_kernel.query.escape_like` — see that helper's docstring for why it
    lives in core rather than being imported from parties' feature-private
    `_search_filter`.
    """
    stmt = select(Party).where(Party.tenant_id == tenant.id)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Party.display_name.ilike(like, escape="\\"),
                Party.email.ilike(like, escape="\\"),
            )
        )
    stmt = stmt.order_by(Party.display_name.asc())
    stmt = apply_pagination(stmt, limit=limit, offset=0)
    return list(db.scalars(stmt).all())


def assign_role(db: Session, tenant: Tenant, payload: RoleGrantRequest) -> PartyRole:
    party = db.scalars(
        select(Party)
        .where(Party.tenant_id == tenant.id)
        .where(Party.id == payload.party_id)
    ).first()
    role = db.scalars(
        select(Role)
        .where(Role.tenant_id == tenant.id)
        .where(Role.id == payload.role_id)
    ).first()
    if party is None or role is None:
        raise NotFoundError("Party or role not found")

    party_role = PartyRole(tenant_id=tenant.id, party_id=party.id, role_id=role.id)
    # See create_role's comment: `db.add` must happen INSIDE the savepoint,
    # not before it.
    try:
        with conflict_savepoint(db):
            db.add(party_role)
            db.flush()
    except IntegrityError as exc:
        raise ConflictError("Role already assigned") from exc
    return party_role


class GrantView(NamedTuple):
    """Read-model row for the `/admin/role-grants` recent-grants list — a
    joined projection (`PartyRole` + `Role.name`/`slug` + `Party.
    display_name`) so the template never has to resolve a bare `party_id`/
    `role_id` itself. There is no ORM relationship from `PartyRole` to
    `Party`/`Role` (plain FK columns by design — see `dotmac_kernel/models.py`),
    so `list_recent_grants` below does one explicit join, not lazy-loaded
    attribute access (no N+1).
    """

    party_id: UUID
    party_display_name: str
    role_id: UUID
    role_name: str
    role_slug: str
    created_at: datetime


def list_recent_grants(db: Session, tenant: Tenant, *, limit: int) -> list[GrantView]:
    stmt = (
        select(
            PartyRole.party_id,
            Party.display_name,
            PartyRole.role_id,
            Role.name,
            Role.slug,
            PartyRole.created_at,
        )
        .join(
            Party,
            (Party.id == PartyRole.party_id) & (Party.tenant_id == PartyRole.tenant_id),
        )
        .join(
            Role,
            (Role.id == PartyRole.role_id) & (Role.tenant_id == PartyRole.tenant_id),
        )
        .where(PartyRole.tenant_id == tenant.id)
        .order_by(PartyRole.created_at.desc())
        .limit(limit)
    )
    return [GrantView(*row) for row in db.execute(stmt).all()]


def _audit_retention_cutoff(db: Session, tenant: Tenant) -> datetime:
    """Shared retention-window computation for `list_audit_events`/
    `count_audit_events` — one place for the cutoff logic so the count and
    the page of rows it paginates can never drift apart (same convention as
    `parties.service._search_filter` powering both `search_parties` and
    `count_parties`).
    """
    retention_days = resolve_value(
        db,
        SettingDomain.audit,
        "retention_days",
        tenant_id=tenant.id,
        default=_DEFAULT_AUDIT_RETENTION_DAYS,
    )
    return datetime.now(UTC) - timedelta(days=retention_days)


def list_audit_events(
    db: Session, tenant: Tenant, *, limit: int, offset: int
) -> list[AuditEvent]:
    # Bound by the tenant's `audit/retention_days` setting (Task 5). This is
    # a LISTING-time filter only — rows older than the retention window are
    # excluded from what this function returns, but they are NOT deleted:
    # there is no purge job. The rows persist in `audit_events` indefinitely;
    # this `resolve_value` call (via `_audit_retention_cutoff`) is the
    # setting's only consumer.
    #
    # `limit`/`offset` (Task 6): this was the last unpaginated list in the
    # app — both the JSON API route (`app/features/rbac/router.py`) and the
    # web `/admin/audit` screen (`app/features/rbac/web.py`) call this SAME
    # function, so there is exactly one paginated-listing implementation for
    # audit events, not two that could drift.
    cutoff = _audit_retention_cutoff(db, tenant)
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant.id)
        .where(AuditEvent.created_at >= cutoff)
        .order_by(AuditEvent.created_at.desc())
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    return list(db.scalars(stmt).all())


def count_audit_events(db: Session, tenant: Tenant) -> int:
    """Total row count for `list_audit_events`' retention-bounded filter —
    powers the `/admin/audit` screen's pagination (page X of Y), computed
    with the SAME `_audit_retention_cutoff` so the count and the page it
    describes can never disagree.
    """
    cutoff = _audit_retention_cutoff(db, tenant)
    return (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.tenant_id == tenant.id)
            .where(AuditEvent.created_at >= cutoff)
        )
        or 0
    )


def resolve_actor_display_names(
    db: Session, tenant: Tenant, party_ids: Iterable[UUID | None]
) -> dict[UUID, str]:
    """One-query resolution of `AuditEvent.actor_party_id -> Party.
    display_name` for the `/admin/audit` screen (Task 6) — called ONCE per
    page with the distinct set of `actor_party_id`s on that page, not once
    per row (no N+1).

    A `None`/unknown `actor_party_id` (system action, or the actor party was
    later deleted) is simply absent from the returned dict; the caller
    (`app.features.rbac.web`) renders a fallback ("System"/"Unknown").
    """
    ids = {party_id for party_id in party_ids if party_id is not None}
    if not ids:
        return {}
    rows = db.execute(
        select(Party.id, Party.display_name).where(
            Party.tenant_id == tenant.id, Party.id.in_(ids)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


__all__ = [
    "GrantView",
    "assign_role",
    "count_audit_events",
    "count_roles",
    "create_role",
    "list_audit_events",
    "list_grantable_parties",
    "list_recent_grants",
    "list_roles",
    "resolve_actor_display_names",
]
