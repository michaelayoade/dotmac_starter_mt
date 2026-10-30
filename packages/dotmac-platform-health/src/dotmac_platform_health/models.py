"""Platform-only persistence for bounded runtime-health evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("health")


class HealthComponent(Base, TimestampMixin):
    __tablename__ = "health_components"
    __table_args__ = (
        UniqueConstraint("code", name="uq_health_components_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class HealthObservation(Base):
    __tablename__ = "health_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_ref", "observation_key", name="uq_health_observations_source_key"
        ),
        ForeignKeyConstraint(
            ["grant_id", "component_id"],
            [
                f"{SCHEMA}.health_grant_components.grant_id",
                f"{SCHEMA}.health_grant_components.component_id",
            ],
            name="fk_health_observations_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "grant_id"],
            [
                f"{SCHEMA}.health_eligibility_assessments.id",
                f"{SCHEMA}.health_eligibility_assessments.grant_id",
            ],
            name="fk_health_observations_assessment_grant",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_grants.id", ondelete="RESTRICT"), nullable=True
    )
    assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_eligibility_assessments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    component_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_components.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    # Snapshot of the component's freshness policy AT ACCEPTANCE. A projection's
    # freshness_deadline is derived from THIS column, never from the component's
    # current `freshness_seconds` — the latter can change after the observation
    # was accepted, and a rebuild must reproduce the same classification the
    # observation was accepted under, not whatever policy happens to be live
    # when the rebuild runs. See `service.py`'s `record_observation` and
    # `rebuild_projections`.
    freshness_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)


class HealthProjection(Base, TimestampMixin):
    __tablename__ = "health_projections"
    __table_args__ = (
        UniqueConstraint("component_id", name="uq_health_projections_component"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    component_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_components.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    freshness_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HealthGrant(Base):
    __tablename__ = "health_grants"
    __table_args__ = (
        UniqueConstraint("enrollment_ref", name="uq_health_grants_enrollment"),
        UniqueConstraint("installation_ref", name="uq_health_grants_installation"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    enrollment_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class HealthEligibilitySnapshot(Base):
    """Append-only exact result from the required current authority."""

    __tablename__ = "health_eligibility_assessments"
    __table_args__ = (
        UniqueConstraint("assessment_ref", name="uq_health_assessment_ref"),
        UniqueConstraint("id", "grant_id", name="uq_health_assessment_grant"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_grants.id", ondelete="RESTRICT"), nullable=False
    )
    assessment_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    assessment: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class HealthGrantComponent(Base):
    __tablename__ = "health_grant_components"
    __table_args__ = (
        UniqueConstraint("component_id", name="uq_health_grant_components_permanent"),
        UniqueConstraint(
            "grant_id", "component_id", name="uq_health_grant_components_pair"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_grants.id", ondelete="RESTRICT"), nullable=False
    )
    component_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_components.id", ondelete="RESTRICT"),
        nullable=False,
    )


class HealthGrantClosure(Base):
    __tablename__ = "health_grant_closures"
    __table_args__ = (
        UniqueConstraint("grant_id", name="uq_health_grant_closures_grant"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_grants.id", ondelete="RESTRICT"), nullable=False
    )
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)


PLATFORM_MODELS = (
    HealthComponent,
    HealthObservation,
    HealthProjection,
    HealthGrant,
    HealthGrantComponent,
    HealthGrantClosure,
    HealthEligibilitySnapshot,
)
PLATFORM_TABLES = tuple(model.__tablename__ for model in PLATFORM_MODELS)

__all__ = [
    "HealthComponent",
    "HealthEligibilitySnapshot",
    "HealthGrant",
    "HealthGrantComponent",
    "HealthGrantClosure",
    "HealthObservation",
    "HealthProjection",
    "PLATFORM_MODELS",
    "PLATFORM_TABLES",
    "SCHEMA",
]
