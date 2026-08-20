"""Tenant-scoped persistence for opaque tracked-unit position evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("pos")
_JSON_VARIANT = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )


class TrackedUnit(Base, TimestampMixin):
    __tablename__ = "tracked_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tracked_units_tenant_id_id"),
        Index("ix_tracked_units_tenant_active", "tenant_id", "is_active"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SourceIdentity(Base, TimestampMixin):
    __tablename__ = "source_identities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_source_identities_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source",
            "source_unit_ref",
            name="uq_source_identities_source_ref",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_unit_ref: Mapped[str] = mapped_column(String(128), nullable=False)


class SourceAssignment(Base, TimestampMixin):
    __tablename__ = "source_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_source_assignments_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            [f"{SCHEMA}.source_identities.tenant_id", f"{SCHEMA}.source_identities.id"],
            name="fk_source_assignments_source_identity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_source_assignments_tracked_unit",
            ondelete="CASCADE",
        ),
        Index(
            "ix_source_assignments_tenant_source_ref",
            "tenant_id",
            "source_identity_id",
        ),
        Index(
            "ix_source_assignments_tenant_unit",
            "tenant_id",
            "tracked_unit_id",
        ),
        CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at > assigned_at",
            name="ck_source_assignments_time_order",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_identity_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CollectionGrant(Base, TimestampMixin):
    __tablename__ = "collection_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_collection_grants_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_collection_grants_tracked_unit",
            ondelete="CASCADE",
        ),
        Index(
            "ix_collection_grants_tenant_unit_purpose",
            "tenant_id",
            "tracked_unit_id",
            "purpose",
        ),
        CheckConstraint(
            "expires_at > granted_at",
            name="ck_collection_grants_expiry",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_collection_grants_revoke_time",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PositionObservation(Base, TimestampMixin):
    __tablename__ = "position_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_position_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_identity_id",
            "client_observation_id",
            name="uq_position_observations_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            [f"{SCHEMA}.source_identities.tenant_id", f"{SCHEMA}.source_identities.id"],
            name="fk_position_observations_source_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_position_observations_tracked_unit",
            ondelete="CASCADE",
        ),
        Index(
            "ix_position_observations_tenant_unit_captured",
            "tenant_id",
            "tracked_unit_id",
            "captured_at",
        ),
        Index(
            "ix_position_observations_tenant_received",
            "tenant_id",
            "received_at",
        ),
        CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name="ck_position_observations_latitude",
        ),
        CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name="ck_position_observations_longitude",
        ),
        CheckConstraint(
            "accuracy_m >= 0",
            name="ck_position_observations_accuracy",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_identity_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    client_observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_unit_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    context_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CurrentPosition(Base, TimestampMixin):
    __tablename__ = "current_positions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_current_positions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            name="uq_current_positions_tenant_unit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            [f"{SCHEMA}.source_identities.tenant_id", f"{SCHEMA}.source_identities.id"],
            name="fk_current_positions_source_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_current_positions_tracked_unit",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.position_observations.tenant_id",
                f"{SCHEMA}.position_observations.id",
            ],
            name="fk_current_positions_observation",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_current_positions_tenant_captured",
            "tenant_id",
            "captured_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source_identity_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_unit_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Geofence(Base, TimestampMixin):
    __tablename__ = "geofences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_geofences_tenant_id_id"),
        Index("ix_geofences_tenant_active", "tenant_id", "is_active"),
        CheckConstraint(
            "shape_kind IN ('circle', 'polygon')",
            name="ck_geofences_shape_kind",
        ),
        CheckConstraint(
            "(shape_kind = 'circle' AND center_latitude IS NOT NULL "
            "AND center_longitude IS NOT NULL AND radius_m > 0 "
            "AND polygon_points IS NULL) OR "
            "(shape_kind = 'polygon' AND center_latitude IS NULL "
            "AND center_longitude IS NULL AND radius_m IS NULL "
            "AND polygon_points IS NOT NULL)",
            name="ck_geofences_shape_payload",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    shape_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    center_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    polygon_points: Mapped[list[list[float]] | None] = mapped_column(
        _JSON_VARIANT, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GeofenceState(Base, TimestampMixin):
    __tablename__ = "geofence_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_geofence_states_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            "geofence_id",
            name="uq_geofence_states_tenant_unit_fence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_geofence_states_tracked_unit",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "geofence_id"],
            [f"{SCHEMA}.geofences.tenant_id", f"{SCHEMA}.geofences.id"],
            name="fk_geofence_states_geofence",
            ondelete="CASCADE",
        ),
        Index(
            "ix_geofence_states_tenant_unit",
            "tenant_id",
            "tracked_unit_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    geofence_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    is_inside: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class GeofenceFact(Base, TimestampMixin):
    __tablename__ = "geofence_facts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_geofence_facts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            "geofence_id",
            "observation_id",
            "transition",
            name="uq_geofence_facts_observation_transition",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            [f"{SCHEMA}.tracked_units.tenant_id", f"{SCHEMA}.tracked_units.id"],
            name="fk_geofence_facts_tracked_unit",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "geofence_id"],
            [f"{SCHEMA}.geofences.tenant_id", f"{SCHEMA}.geofences.id"],
            name="fk_geofence_facts_geofence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.position_observations.tenant_id",
                f"{SCHEMA}.position_observations.id",
            ],
            name="fk_geofence_facts_observation",
            ondelete="CASCADE",
        ),
        Index(
            "ix_geofence_facts_tenant_unit_occurred",
            "tenant_id",
            "tracked_unit_id",
            "occurred_at",
        ),
        CheckConstraint(
            "transition IN ('entry', 'exit')",
            name="ck_geofence_facts_transition",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    tracked_unit_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    geofence_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    transition: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (
    TrackedUnit,
    SourceIdentity,
    SourceAssignment,
    CollectionGrant,
    PositionObservation,
    CurrentPosition,
    Geofence,
    GeofenceState,
    GeofenceFact,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "CollectionGrant",
    "CurrentPosition",
    "Geofence",
    "GeofenceFact",
    "GeofenceState",
    "PositionObservation",
    "SourceAssignment",
    "SourceIdentity",
    "TrackedUnit",
]
