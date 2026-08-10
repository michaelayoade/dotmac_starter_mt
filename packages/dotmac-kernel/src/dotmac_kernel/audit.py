"""Audit event model + write-side helpers.

Cross-cutting: the audit trail is written from every domain (rbac, auth, ...),
so the model and the write helper live in dotmac_kernel. The audit *read* endpoint
stays in app.features.rbac.

**`action` is a declared code, not free text** (module control-plane directive
step 3). `write_audit_event` validates it against the process-active
`dotmac_kernel.audit_actions.AuditActionRegistry` — built from the installed
manifests' `audit_actions` — before it writes anything, so a typo cannot quietly
create a second near-identical action nobody queries for. `write_platform_audit_event`
is deliberately NOT validated the same way: platform actions are written by the
kernel's own control plane, which has no module manifest to declare them on;
that is a separate authority and a later step.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column

from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.models import Base, uuid_pk
from dotmac_kernel.models_platform import PlatformAuditEvent


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_party_id: Mapped[UUID | None] = mapped_column(Uuid(), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[dict[str, object]] = mapped_column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def write_audit_event(
    db: Session,
    *,
    tenant_id: UUID,
    actor_party_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    """Record a tenant-scoped audit event.

    `action` MUST be declared by an installed module's manifest
    (`audit_actions`) — raises `UndeclaredAuditActionError` otherwise, BEFORE
    anything is added to the session, so a rejected write leaves no partial
    state. See this module's docstring for why the trail's vocabulary is a
    declaration rather than free text.
    """
    active_audit_actions().require(action)
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_party_id=actor_party_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


def write_platform_audit_event(
    db: Session,
    *,
    actor_admin_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: dict[str, object] | None = None,
) -> PlatformAuditEvent:
    """Record a PLATFORM-level audit event (no tenant context). Same add/flush
    contract as `write_audit_event`; the actor is a `PlatformAdmin`."""
    event = PlatformAuditEvent(
        actor_admin_id=actor_admin_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


__all__ = [
    "AuditEvent",
    "PlatformAuditEvent",
    "write_audit_event",
    "write_platform_audit_event",
]
