"""Approval state on explicit tenant and platform planes (ADR-0023, ADR-0026 § 5).

Six tables, three per plane, declared separately rather than shared with a
nullable tenant column or a `platform` flag. Both plane tuples are populated
because both planes exist in production today: ERP approves back-office subjects
for a tenant, and the vendor control plane approves fleet-plan subjects with no
tenant at all.

Three rules hold across every table here, and each is a finding from the audit:

- **No foreign key crosses the planes**, and none points into an adopting
  product's domain schema. `subject_id` is an opaque string reference, not a
  relation — a module that could join to `finance.invoices` would be a second
  reader of another owner's tables and un-installable beside a product that
  spells them differently.
- **The policy revision is immutable.** `(policy_code, version)` is unique and
  never updated. ERP's mutable workflow row let an edit reinterpret requests
  already in flight; nothing here can be rewritten after publication, and a
  request records the exact version it was opened against.
- **A duplicate decision is impossible, not merely refused.** The unique
  constraint on `(request_id, level, actor_id)` is what makes quorum count
  distinct people under concurrency, where an in-memory check alone would not.

`levels` is `sa.JSON` rather than `JSONB` deliberately: the unit suite runs on
SQLite and the shape is read whole, never queried into. Postgres stores it as
`json`, and no index or predicate here depends on the difference.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("approvals")

# `sha256:` + 64 hex. Sized once, used by every digest column on both planes.
_DIGEST_LENGTH = 71
# Long enough for a readable operator-chosen code, short enough to index. Matches
# the vendor control plane's own `String(120)`, which is the shape being ported.
_CODE_LENGTH = 120
# An opaque foreign identifier, printed as text. Vendor CP uses `String(200)`.
_SUBJECT_ID_LENGTH = 200


class _PolicyColumns:
    """Plane-independent policy revision, declared once for both tables."""

    policy_code: Mapped[str] = mapped_column(String(_CODE_LENGTH), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    levels: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    allow_self_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # The digest of the policy DOCUMENT — Vendor CP's "policy content hashes".
    # It is what lets a cutover prove an imported revision is byte-identical to
    # the one it replaced, rather than merely similarly named.
    document_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), nullable=False)


class _RequestColumns:
    """Plane-independent request, declared once for both tables."""

    policy_code: Mapped[str] = mapped_column(String(_CODE_LENGTH), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(_CODE_LENGTH), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(_SUBJECT_ID_LENGTH), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    current_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Command idempotency (Vendor CP delta). ERP had none on submit, so a retried
    # submission opened a second request against the same subject.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class _DecisionColumns:
    """Plane-independent decision, declared once for both tables."""

    level: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Delegation survives as PROVENANCE on an approve, which is what ERP's rows
    # actually recorded — never as a separate action that changes state.
    delegated_from: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# ── Tenant plane ────────────────────────────────────────────────────────────


class ApprovalPolicy(Base, _PolicyColumns, TimestampMixin):
    """An immutable tenant-scoped policy revision."""

    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_policies_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "policy_code",
            "version",
            name="uq_approval_policies_tenant_code_version",
        ),
        Index("ix_approval_policies_tenant_code", "tenant_id", "policy_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class ApprovalRequest(Base, _RequestColumns, TimestampMixin):
    """One tenant-scoped request, bound to an exact policy revision and digest."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_requests_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_approval_requests_tenant_idempotency",
        ),
        Index(
            "ix_approval_requests_tenant_subject",
            "tenant_id",
            "subject_type",
            "subject_id",
        ),
        Index("ix_approval_requests_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class ApprovalDecision(Base, _DecisionColumns, TimestampMixin):
    """One actor's decision on a tenant request. Append-only by contract."""

    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "level",
            "actor_id",
            name="uq_approval_decisions_one_vote",
        ),
        Index("ix_approval_decisions_tenant_request", "tenant_id", "request_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.approval_requests.id", ondelete="CASCADE"),
        nullable=False,
    )


# ── Platform plane ──────────────────────────────────────────────────────────


class PlatformApprovalPolicy(Base, _PolicyColumns, TimestampMixin):
    """An immutable control-plane policy revision; tenant-free by design."""

    __tablename__ = "platform_approval_policies"
    __table_args__ = (
        UniqueConstraint(
            "policy_code",
            "version",
            name="uq_platform_approval_policies_code_version",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformApprovalRequest(Base, _RequestColumns, TimestampMixin):
    """One control-plane request — a fleet plan, a release, a deployment."""

    __tablename__ = "platform_approval_requests"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_platform_approval_requests_idempotency",
        ),
        Index(
            "ix_platform_approval_requests_subject",
            "subject_type",
            "subject_id",
        ),
        Index("ix_platform_approval_requests_state", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformApprovalDecision(Base, _DecisionColumns, TimestampMixin):
    """One actor's decision on a control-plane request."""

    __tablename__ = "platform_approval_decisions"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "level",
            "actor_id",
            name="uq_platform_approval_decisions_one_vote",
        ),
        Index("ix_platform_approval_decisions_request", "request_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    request_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_approval_requests.id", ondelete="CASCADE"),
        nullable=False,
    )


TENANT_TABLES: tuple[str, ...] = (
    "approval_policies",
    "approval_requests",
    "approval_decisions",
)
PLATFORM_TABLES: tuple[str, ...] = (
    "platform_approval_policies",
    "platform_approval_requests",
    "platform_approval_decisions",
)

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalRequest",
    "PlatformApprovalDecision",
    "PlatformApprovalPolicy",
    "PlatformApprovalRequest",
]
