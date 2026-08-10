"""Persisted state for the outbox subsystem (kernel WS3).

- ``outbox_events`` — the transactional outbox: an event row written IN THE SAME
  transaction as a state change, so the event exists iff that change commits. A
  relay (WS3 slice 2) drains `pending` rows and delivers them. Tenant-scoped and
  RLS-protected.
- ``platform_outbox_events`` — its platform-scoped peer (no tenant, no RLS).

The idempotency ledger that used to live here as ``inbox_records`` moved to
`dotmac_kernel.idempotency_models` when ADR-0014 made at-most-once execution a
first-class kernel facility with one owner. It was never specific to inbound
transport delivery, and keeping it filed under "inbox" is part of why five other
idempotency stores grew beside it across the fleet.

Import-safe: this module touches only `Base.metadata`, never the engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk

# JSONB on Postgres, generic JSON elsewhere (SQLite unit tests) — same variant the
# kernel core models use for JSON columns.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class OutboxStatus(str, Enum):
    """Lifecycle of an outbox event, advanced by the relay (slice 2):
    ``pending`` → ``claimed`` (leased by a dispatcher worker) → ``sent`` on
    delivery, or back to ``pending`` for a retry (backoff), or ``dead`` after
    max attempts (a retained dead-letter). ``failed`` is the legacy terminal
    kept for compatibility."""

    PENDING = "pending"
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"


class OutboxEvent(Base, TimestampMixin):
    """A domain event enqueued in the same transaction as its state change. The
    relay (slice 2) claims `pending` rows whose `available_at` has passed,
    delivers them, and advances `status`/`attempts`/`sent_at`."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        # The relay scans by (status, available_at); index it for cheap claims.
        Index("ix_outbox_events_status_available_at", "status", "available_at"),
        # Stale-lease reclaim scans claimed rows by lease age.
        Index("ix_outbox_events_status_leased_at", "status", "leased_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OutboxStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Relay lease (slice 2): a dispatcher worker claims a row by stamping these;
    # a stale lease (leased_at older than the timeout) is reclaimable.
    leased_by: Mapped[str | None] = mapped_column(String(200))
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformOutboxEvent(Base, TimestampMixin):
    """A PLATFORM-scoped domain event — the platform peer of `OutboxEvent`.

    A platform event has NO tenant: it is a control-plane fact (e.g. a vendor
    contract transition) enqueued in the same transaction as its state change.
    So this is a PLATFORM catalog table — **no `tenant_id`, no tenant FK, no
    RLS** — GRANTed to `platform_api`/`app_admin`, REVOKEd from `app_user`
    (migration 0012). The platform relay drains it with the same
    leasing/backoff/dead-letter engine as the tenant relay, on a SEPARATE table
    and a separate dispatcher role (`platform_outbox_dispatcher`)."""

    __tablename__ = "platform_outbox_events"
    __table_args__ = (
        # The relay scans by (status, available_at); index it for cheap claims.
        Index(
            "ix_platform_outbox_events_status_available_at", "status", "available_at"
        ),
        # Stale-lease reclaim scans claimed rows by lease age.
        Index("ix_platform_outbox_events_status_leased_at", "status", "leased_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OutboxStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Relay lease (same engine as OutboxEvent): a platform dispatcher claims a
    # row by stamping these; a stale lease (older than the timeout) is reclaimable.
    leased_by: Mapped[str | None] = mapped_column(String(200))
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "OutboxEvent",
    "OutboxStatus",
    "PlatformOutboxEvent",
]
