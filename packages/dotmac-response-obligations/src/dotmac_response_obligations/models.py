"""Response-obligation persistence contract.

Five tables on one tenant-only forced-RLS plane. The source (Sub's
`sla_policies`/`sla_targets`/`sla_clocks`/`sla_breaches`) had no tenant column
at all — it is a single-schema application — so tenancy, composite parent keys
and forced RLS exist here from revision 1 rather than being retrofitted.

Two deliberate departures from the source, both about answering questions the
source cannot:

* **Paused time is itemised, not just totalled.** Sub keeps
  `total_paused_seconds` and nothing else, so "why was this clock stopped for
  fourteen hours" has no answer. `sla_clock_pauses` records each interval with
  its reason.
* **A breach is an observation, not a record with a lifecycle.** Sub's
  `sla_breaches` carries open/acknowledged/resolved — which is the escalation
  decision `dotmac-operational-escalations` owns. Here the observation is
  append-only and has no status.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_response_obligations.contracts import (
    ClockStatus,
    ObligationKind,
    ObservationKind,
    PauseReason,
)

SCHEMA = module_schema("sla")


def _enum(python_type: type, name: str) -> sa.Enum:
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


def _tenant_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class ResponsePolicy(Base, TimestampMixin):
    """A named promise set for one open, product-declared subject type."""

    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sla_policies_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_sla_policies_tenant_code"),
        Index("ix_sla_policies_tenant_subject_type", "tenant_id", "subject_type"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(60), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class ResponseTarget(Base, TimestampMixin):
    """One promise: this kind of response, at this priority, within this long.

    `priority` NULL is the default row. The partial unique index below is why
    it can be: a plain UNIQUE over a nullable column permits many NULLs in
    PostgreSQL, so "one default per policy and kind" needs saying explicitly.
    """

    __tablename__ = "sla_targets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sla_targets_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "policy_id",
            "kind",
            "priority",
            name="uq_sla_targets_tenant_policy_kind_priority",
        ),
        Index(
            "uq_sla_targets_default_per_kind",
            "tenant_id",
            "policy_id",
            "kind",
            unique=True,
            postgresql_where=sa.text("priority IS NULL"),
            sqlite_where=sa.text("priority IS NULL"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.sla_policies.tenant_id", f"{SCHEMA}.sla_policies.id"],
            ondelete="CASCADE",
            name="fk_sla_targets_tenant_policy",
        ),
        sa.CheckConstraint("target_seconds > 0", name="ck_sla_targets_target_positive"),
        sa.CheckConstraint(
            "warning_seconds IS NULL OR "
            "(warning_seconds > 0 AND warning_seconds < target_seconds)",
            name="ck_sla_targets_warning_inside_target",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    kind: Mapped[ObligationKind] = mapped_column(
        _enum(ObligationKind, "sla_obligation_kind"), nullable=False
    )
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_seconds: Mapped[int] = mapped_column(Integer(), nullable=False)
    # Measured BACK from due_at, so it keeps meaning when the target changes.
    warning_seconds: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class ResponseClock(Base, TimestampMixin):
    """One running promise against one opaque subject.

    `due_at` is a stored instant, not a computation over `started_at`: pausing
    moves it, and a due time derived at read time would silently disagree with
    the timer the assembly already scheduled against it.
    """

    __tablename__ = "sla_clocks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sla_clocks_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_sla_clocks_tenant_dedup_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [f"{SCHEMA}.sla_policies.tenant_id", f"{SCHEMA}.sla_policies.id"],
            name="fk_sla_clocks_tenant_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_id"],
            [f"{SCHEMA}.sla_targets.tenant_id", f"{SCHEMA}.sla_targets.id"],
            name="fk_sla_clocks_tenant_target",
        ),
        # One live clock per subject per kind. A second first-response clock
        # would be measured from the wrong instant and breach on its own.
        Index(
            "uq_sla_clocks_live_subject_kind",
            "tenant_id",
            "subject_reference",
            "kind",
            unique=True,
            postgresql_where=sa.text("status IN ('RUNNING', 'PAUSED')"),
            sqlite_where=sa.text("status IN ('RUNNING', 'PAUSED')"),
        ),
        # The sweep reads the front of this, never the table.
        Index("ix_sla_clocks_tenant_status_due", "tenant_id", "status", "due_at"),
        Index(
            "ix_sla_clocks_tenant_subject_started",
            "tenant_id",
            "subject_reference",
            "started_at",
        ),
        sa.CheckConstraint(
            "total_paused_seconds >= 0", name="ck_sla_clocks_paused_not_negative"
        ),
        sa.CheckConstraint(
            "(status = 'PAUSED') = (paused_at IS NOT NULL)",
            name="ck_sla_clocks_paused_coherence",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(180), nullable=False)
    kind: Mapped[ObligationKind] = mapped_column(
        _enum(ObligationKind, "sla_clock_kind"), nullable=False
    )
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[ClockStatus] = mapped_column(
        _enum(ClockStatus, "sla_clock_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_paused_seconds: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    breached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResponseClockPause(Base, TimestampMixin):
    """One interval a clock did not count, and why.

    Append-only except for `resumed_at`, which is written exactly once when the
    interval closes. A pause with no recorded reason cannot answer the first
    question asked about any disputed breach.
    """

    __tablename__ = "sla_clock_pauses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sla_clock_pauses_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "clock_id"],
            [f"{SCHEMA}.sla_clocks.tenant_id", f"{SCHEMA}.sla_clocks.id"],
            ondelete="CASCADE",
            name="fk_sla_clock_pauses_tenant_clock",
        ),
        Index(
            "uq_sla_clock_pauses_open_clock",
            "tenant_id",
            "clock_id",
            unique=True,
            postgresql_where=sa.text("resumed_at IS NULL"),
            sqlite_where=sa.text("resumed_at IS NULL"),
        ),
        sa.CheckConstraint(
            "resumed_at IS NULL OR resumed_at >= paused_at",
            name="ck_sla_clock_pauses_ordered",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    clock_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    reason: Mapped[PauseReason] = mapped_column(
        _enum(PauseReason, "sla_pause_reason"), nullable=False
    )
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actor_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResponseObservation(Base, TimestampMixin):
    """A threshold the clock crossed — a fact, with no lifecycle.

    The source gave breaches open/acknowledged/resolved, which is the
    escalation decision `dotmac-operational-escalations` owns for every
    subject. There is no status column here, and nothing to settle: whether
    this deserves an escalation, at what level, and who answered it is that
    module's answer, reached through the request this module returns.
    """

    __tablename__ = "sla_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sla_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_sla_observations_tenant_dedup_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "clock_id"],
            [f"{SCHEMA}.sla_clocks.tenant_id", f"{SCHEMA}.sla_clocks.id"],
            ondelete="CASCADE",
            name="fk_sla_observations_tenant_clock",
        ),
        Index(
            "ix_sla_observations_tenant_clock_time",
            "tenant_id",
            "clock_id",
            "observed_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    clock_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[ObservationKind] = mapped_column(
        _enum(ObservationKind, "sla_observation_kind"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES = (
    "sla_policies",
    "sla_targets",
    "sla_clocks",
    "sla_clock_pauses",
    "sla_observations",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        ResponsePolicy,
        ResponseTarget,
        ResponseClock,
        ResponseClockPause,
        ResponseObservation,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ResponseClock",
    "ResponseClockPause",
    "ResponseObservation",
    "ResponsePolicy",
    "ResponseTarget",
    "metadata_table",
]
