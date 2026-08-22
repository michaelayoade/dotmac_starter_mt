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
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    component_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.health_components.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
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


PLATFORM_MODELS = (HealthComponent, HealthObservation, HealthProjection)
PLATFORM_TABLES = tuple(model.__tablename__ for model in PLATFORM_MODELS)

__all__ = [
    "HealthComponent",
    "HealthObservation",
    "HealthProjection",
    "PLATFORM_MODELS",
    "PLATFORM_TABLES",
    "SCHEMA",
]
