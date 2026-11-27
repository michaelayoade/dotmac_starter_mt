"""Audit-trail reads for the reference app's remaining RBAC surface.

Role and grant administration moved to `dotmac-access-admin`; only the audit
listing (`/rbac/audit-events`, `/admin/audit`) is left here until the audit
viewer lane takes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.query import apply_pagination
from dotmac_kernel.settings_resolver import resolve
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.rbac.spec import AUDIT_RETENTION_DAYS

# Fallback if the settings feature is disabled in this deployment — matches
# the `audit/retention_days` spec's own default (app/features/rbac/spec.py).


def _audit_retention_cutoff(db: Session, tenant: Tenant) -> datetime:
    """Shared retention-window computation for `list_audit_events`/
    `count_audit_events` — one place for the cutoff logic so the count and
    the page of rows it paginates can never drift apart (same convention as
    `parties.service._search_filter` powering both `search_parties` and
    `count_parties`).
    """
    # `int` from the spec, so the `timedelta` below is a checked call.
    retention_days = resolve(db, AUDIT_RETENTION_DAYS, tenant_id=tenant.id)
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
    "count_audit_events",
    "list_audit_events",
    "resolve_actor_display_names",
]
