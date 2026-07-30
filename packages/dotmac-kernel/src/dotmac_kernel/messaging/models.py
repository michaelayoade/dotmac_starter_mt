"""Persisted state for the outbox/inbox subsystem (kernel WS3).

Two tenant-scoped, RLS-protected tables:

- ``inbox_records`` — the idempotency ledger: one row per processed command
  (`unique(tenant_id, command_id)`), storing the recorded outcome so a
  re-delivered command replays its result instead of re-running.
- ``outbox_events`` — the transactional outbox: an event row written IN THE SAME
  transaction as a state change, so the event exists iff that change commits. A
  relay (WS3 slice 2) drains `pending` rows and delivers them.

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
    UniqueConstraint,
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
    """Lifecycle of an outbox event (the relay in slice 2 advances it)."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class InboxStatus(str, Enum):
    """Terminal status recorded for a processed command."""

    PROCESSED = "processed"
    FAILED = "failed"


class InboxRecord(Base, TimestampMixin):
    """One row per processed command — the idempotency ledger. A second delivery
    of the same `(tenant_id, command_id)` finds this row and replays `result`."""

    __tablename__ = "inbox_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_inbox_records_tenant_command_id"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command_id: Mapped[str] = mapped_column(String(200), nullable=False)
    command_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200))


class OutboxEvent(Base, TimestampMixin):
    """A domain event enqueued in the same transaction as its state change. The
    relay (slice 2) claims `pending` rows whose `available_at` has passed,
    delivers them, and advances `status`/`attempts`/`sent_at`."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        # The relay scans by (status, available_at); index it for cheap claims.
        Index("ix_outbox_events_status_available_at", "status", "available_at"),
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


__all__ = [
    "OutboxStatus",
    "InboxStatus",
    "InboxRecord",
    "OutboxEvent",
]
