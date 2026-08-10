"""Persisted state for at-most-once execution (ADR-0014).

Two ledgers, one per scoping level, holding the SAME shape:

- ``idempotency_records`` — tenant-scoped, RLS-protected. Unique on
  ``(tenant_id, scope, key)``.
- ``platform_idempotency_records`` — the platform peer. A platform command has
  no tenant, so the key is ``(scope, key)`` alone. A PLATFORM catalog table: no
  ``tenant_id``, no RLS, GRANTed to ``platform_api``/``app_admin`` (ADR-0004).

These replace the WS3 ``inbox_records``/``platform_inbox_records`` tables. The
rename is the point: the ledger was never specific to inbound transport
delivery, and having it live under "inbox" is what let five other idempotency
stores grow beside it across the fleet (see
``docs/inventories/idempotency-sources.md``).

Three columns carry the whole contract and are worth reading closely:

``scope``
    The caller-declared operation family. Opaque to the kernel — an open string,
    not an enum, following ADR-0008's registry principle. Deliberately NOT an
    HTTP endpoint: the same logical operation reached through a second route
    must land in the same ledger, which is why ERP's ``(org, endpoint, key)``
    shape was rejected (ADR-0014 § 3).

``fingerprint``
    A hash of the request that produced the key, or ``NULL`` meaning *the caller
    asserts the key alone identifies the request* — the correct reading for a
    transport-generated ``command_id``. It is its OWN column and never doubles
    as anything else. Sub's shared table overloads one untyped ``ref_id`` to
    mean a fingerprint in two services and a result id in five others; that
    defect is unrepresentable here.

``expires_at``
    NULLABLE, and the kernel sets no default. A payment replay window and a
    provisioning replay window are not the same duration, so retention is a
    product policy (ADR-0014 § 6) applied via ``purge_expired``.

Import-safe: this module touches only ``Base.metadata``, never the engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk

# JSONB on Postgres, generic JSON elsewhere (SQLite unit tests) — same variant the
# kernel core models use for JSON columns.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# The scope recorded for rows written through `messaging.process_once` /
# `process_once_platform`, which key on a transport-generated command id.
INBOX_SCOPE = "inbox"


class IdempotencyStatus(str, Enum):
    """Terminal status recorded for an executed operation.

    Only ``EXECUTED`` is ever written today: a handler that raises propagates,
    its work rolls back with the SAVEPOINT, and NO row is recorded — which is
    exactly what makes a retry re-drive cleanly (ADR-0014 § 5). ``FAILED`` is
    retained for a future caller that wants to record a terminal failure as
    non-retryable, and must not be inferred to exist in the ledger today.
    """

    EXECUTED = "executed"
    FAILED = "failed"


class IdempotencyRecord(Base, TimestampMixin):
    """One row per executed operation — the tenant-scoped ledger. A later
    attempt with the same ``(tenant_id, scope, key)`` finds this row and replays
    ``result`` instead of re-running the effect."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "scope", "key", name="uq_idempotency_records_tenant_scope_key"
        ),
        # `purge_expired` scans by expiry; index it so retention is cheap.
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformIdempotencyRecord(Base, TimestampMixin):
    """The PLATFORM-level ledger — the platform-scoped counterpart to
    ``IdempotencyRecord``. A platform operation has no tenant, so its key is
    ``(scope, key)`` alone (globally unique, not per-tenant). A PLATFORM catalog
    table: no ``tenant_id``, no RLS, GRANTed to ``platform_api``/``app_admin``,
    REVOKEd from ``app_user``."""

    __tablename__ = "platform_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "scope", "key", name="uq_platform_idempotency_records_scope_key"
        ),
        Index("ix_platform_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict] = mapped_column(
        _JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "INBOX_SCOPE",
    "IdempotencyStatus",
    "IdempotencyRecord",
    "PlatformIdempotencyRecord",
]
