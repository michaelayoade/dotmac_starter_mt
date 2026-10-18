"""Academy 096 assessment storage with source logical learner/actor references."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import SCHEMA


class ActivityAttempt(Base, TimestampMixin):
    """Fixed question draw for one sitting; at most one open sitting per learner."""

    __tablename__ = "activity_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_activity_attempts_tenant_id_id"),
        Index(
            "uq_activity_attempts_open",
            "tenant_id",
            "activity_id",
            "person_id",
            unique=True,
            postgresql_where=text("submitted_at IS NULL"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_activity_attempts_tenant_activity",
        ),
        Index("ix_activity_attempts_tenant_id", "tenant_id"),
        Index("ix_activity_attempts_activity_id", "activity_id"),
        Index("ix_activity_attempts_person_id", "person_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    activity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    question_ext_ids: Mapped[list[object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
        # The PostgreSQL JSONB column supplies the implicit cast; the lineage
        # retains its explicit PostgreSQL cast and catalog default.
        server_default=text("'[]'"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptGrant(Base, TimestampMixin):
    """Additive per-learner allowance with its source reason and logical actor UUID."""

    __tablename__ = "attempt_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_attempt_grants_tenant_id_id"),
        Index("ix_attempt_grants_lookup", "tenant_id", "activity_id", "person_id"),
        ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_attempt_grants_tenant_activity",
        ),
        Index("ix_attempt_grants_tenant_id", "tenant_id"),
        Index("ix_attempt_grants_activity_id", "activity_id"),
        Index("ix_attempt_grants_person_id", "person_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    activity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    extra_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


ATTEMPT_MODELS = (ActivityAttempt, AttemptGrant)
ATTEMPT_TABLES = tuple(model.__tablename__ for model in ATTEMPT_MODELS)

__all__ = ["ATTEMPT_MODELS", "ATTEMPT_TABLES", "ActivityAttempt", "AttemptGrant"]
