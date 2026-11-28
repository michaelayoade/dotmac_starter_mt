"""Append-only timer generations and trigger-acceptance evidence.

The two planes share a lifecycle but never a row. Tenant tables carry a
non-null tenant id and composite keys; platform tables carry no tenant column.
Status remains a string so the module can extend its own vocabulary without a
database enum migration.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

SCHEMA = module_schema("timers")

TENANT_TABLES: tuple[str, ...] = (
    "timers",
    "timer_acceptances",
    "timer_rejections",
    "job_states",
)
PLATFORM_TABLES: tuple[str, ...] = (
    "platform_timers",
    "platform_timer_acceptances",
    "platform_timer_rejections",
)


class _TimerColumns:
    @declared_attr
    def owner(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def entity_kind(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def entity_id(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    @declared_attr
    def purpose(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def generation(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False)

    @declared_attr
    def status(cls) -> Mapped[str]:
        return mapped_column(String(20), nullable=False)

    @declared_attr
    def due_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)

    @declared_attr
    def output_event_type(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def expected_source_version(cls) -> Mapped[int | None]:
        return mapped_column(Integer)

    @declared_attr
    def outbox_event_id(cls) -> Mapped[UUID]:
        return mapped_column(Uuid(), nullable=False)

    @declared_attr
    def recorded_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)

    @declared_attr
    def superseded_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True))

    @declared_attr
    def canceled_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True))

    @declared_attr
    def fired_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True))

    @declared_attr
    def expires_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True))


class _AcceptanceColumns:
    @declared_attr
    def timer_id(cls) -> Mapped[UUID]:
        return mapped_column(Uuid(), nullable=False)

    @declared_attr
    def accepted_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)


class _RejectionColumns:
    @declared_attr
    def timer_id(cls) -> Mapped[UUID]:
        return mapped_column(Uuid(), nullable=False)

    @declared_attr
    def owner(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def entity_kind(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def entity_id(cls) -> Mapped[str]:
        return mapped_column(String(255), nullable=False)

    @declared_attr
    def purpose(cls) -> Mapped[str]:
        return mapped_column(String(120), nullable=False)

    @declared_attr
    def observed_generation(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False)

    @declared_attr
    def current_generation(cls) -> Mapped[int | None]:
        return mapped_column(Integer)

    @declared_attr
    def expected_source_version(cls) -> Mapped[int | None]:
        return mapped_column(Integer)

    @declared_attr
    def rejected_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), nullable=False)


class Timer(Base, _TimerColumns):
    """One tenant timer generation."""

    __tablename__ = "timers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_timers_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            "generation",
            name="uq_timers_identity_generation",
        ),
        Index(
            "uq_timers_current_identity",
            "tenant_id",
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            unique=True,
            postgresql_where=text("status = 'scheduled'"),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class TimerAcceptance(Base, _AcceptanceColumns):
    """Append-only evidence that a tenant timer generation was accepted."""

    __tablename__ = "timer_acceptances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "timer_id"],
            [f"{SCHEMA}.timers.tenant_id", f"{SCHEMA}.timers.id"],
            ondelete="CASCADE",
            name="fk_timer_acceptances_timer",
        ),
        UniqueConstraint(
            "tenant_id", "timer_id", name="uq_timer_acceptances_tenant_timer"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class TimerRejection(Base, _RejectionColumns):
    """Append-only evidence that a tenant trigger was stale when observed."""

    __tablename__ = "timer_rejections"
    __table_args__ = (
        Index(
            "uq_timer_rejections_observed_current",
            "tenant_id",
            "timer_id",
            text("COALESCE(current_generation, 0)"),
            unique=True,
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class PlatformTimer(Base, _TimerColumns):
    """One platform timer generation."""

    __tablename__ = "platform_timers"
    __table_args__ = (
        UniqueConstraint(
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            "generation",
            name="uq_platform_timers_identity_generation",
        ),
        Index(
            "uq_platform_timers_current_identity",
            "owner",
            "entity_kind",
            "entity_id",
            "purpose",
            unique=True,
            postgresql_where=text("status = 'scheduled'"),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformTimerAcceptance(Base, _AcceptanceColumns):
    """Append-only evidence that a platform timer generation was accepted."""

    __tablename__ = "platform_timer_acceptances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["timer_id"],
            [f"{SCHEMA}.platform_timers.id"],
            ondelete="CASCADE",
            name="fk_platform_timer_acceptances_timer",
        ),
        UniqueConstraint("timer_id", name="uq_platform_timer_acceptances_timer"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformTimerRejection(Base, _RejectionColumns):
    """Append-only evidence that a platform trigger was stale when observed."""

    __tablename__ = "platform_timer_rejections"
    __table_args__ = (
        Index(
            "uq_platform_timer_rejections_observed_current",
            "timer_id",
            text("COALESCE(current_generation, 0)"),
            unique=True,
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class JobState(Base):
    """Operator and outcome state of one declared job in one tenant.

    The timer rows own the SCHEDULE (generation, due instant). This row owns
    what a timer cannot: whether staff paused the job, and the outcome of its
    last run, so the staff surface never has to infer either from outbox rows
    (the dispatcher credential cannot read them and staff should not need to).
    It is state, not identity: a deleted row means "never run, not paused".
    """

    __tablename__ = "job_states"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "module_code", "job_code", name="uq_job_states_tenant_job"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    module_code: Mapped[str] = mapped_column(String(120), nullable=False)
    job_code: Mapped[str] = mapped_column(String(120), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_by: Mapped[str | None] = mapped_column(String(255))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_generation: Mapped[int | None] = mapped_column(Integer)
    last_outcome: Mapped[str | None] = mapped_column(String(20))
    last_detail: Mapped[str | None] = mapped_column(String(500))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    #: The event whose final attempt failed (committed with the failure record,
    #: before the dead-letter hook runs): a re-delivery never runs the handler.
    exhausted_event_id: Mapped[UUID | None] = mapped_column(Uuid())
    dead_letter_event_id: Mapped[UUID | None] = mapped_column(Uuid())
    dead_letter_generation: Mapped[int | None] = mapped_column(Integer)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Timer,
    TimerAcceptance,
    TimerRejection,
    JobState,
    PlatformTimer,
    PlatformTimerAcceptance,
    PlatformTimerRejection,
)

__all__ = [
    "ALL_MODELS",
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "JobState",
    "PlatformTimer",
    "PlatformTimerAcceptance",
    "PlatformTimerRejection",
    "Timer",
    "TimerAcceptance",
    "TimerRejection",
]
