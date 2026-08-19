"""Outside-plant identity and continuity evidence in ``mod_fiber``."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("fiber")


class Structure(Base):
    __tablename__ = "structures"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_structures_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fiber_structures_tenant_code"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    location_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_ref: Mapped[str | None] = mapped_column(String(200))
    source_ref: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Cable(Base):
    __tablename__ = "cables"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_cables_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fiber_cables_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "start_structure_id"],
            [f"{SCHEMA}.structures.tenant_id", f"{SCHEMA}.structures.id"],
            name="fk_fiber_cables_start",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "end_structure_id"],
            [f"{SCHEMA}.structures.tenant_id", f"{SCHEMA}.structures.id"],
            name="fk_fiber_cables_end",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    strand_count: Mapped[int] = mapped_column(Integer, nullable=False)
    start_structure_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    end_structure_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    route_ref: Mapped[str | None] = mapped_column(String(200))
    asset_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Strand(Base):
    __tablename__ = "strands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_strands_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "cable_id", "ordinal", name="uq_fiber_strand_ordinal"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cable_id"],
            [f"{SCHEMA}.cables.tenant_id", f"{SCHEMA}.cables.id"],
            name="fk_fiber_strands_cable",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    cable_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    colour_code: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Splice(Base):
    __tablename__ = "splices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_splices_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "left_strand_id",
            "right_strand_id",
            name="uq_fiber_splice_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            [f"{SCHEMA}.structures.tenant_id", f"{SCHEMA}.structures.id"],
            name="fk_fiber_splices_structure",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "left_strand_id"],
            [f"{SCHEMA}.strands.tenant_id", f"{SCHEMA}.strands.id"],
            name="fk_fiber_splices_left",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "right_strand_id"],
            [f"{SCHEMA}.strands.tenant_id", f"{SCHEMA}.strands.id"],
            name="fk_fiber_splices_right",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    structure_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    left_strand_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    right_strand_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    loss_db: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Termination(Base):
    __tablename__ = "terminations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_terminations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "strand_id",
            "endpoint_ref",
            name="uq_fiber_termination_endpoint",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            [f"{SCHEMA}.structures.tenant_id", f"{SCHEMA}.structures.id"],
            name="fk_fiber_terminations_structure",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "strand_id"],
            [f"{SCHEMA}.strands.tenant_id", f"{SCHEMA}.strands.id"],
            name="fk_fiber_terminations_strand",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    structure_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    strand_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    endpoint_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    port_ref: Mapped[str | None] = mapped_column(String(200))
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FieldObservation(Base):
    __tablename__ = "field_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "evidence_ref", name="uq_fiber_observation_evidence"
        ),
        Index(
            "ix_fiber_observations_subject", "tenant_id", "subject_ref", "observed_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    result_code: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actor_ref: Mapped[str | None] = mapped_column(String(200))


class Change(Base):
    __tablename__ = "changes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_changes_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fiber_changes_tenant_code"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    subject_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    desired_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    as_built_fingerprint: Mapped[str | None] = mapped_column(String(128))
    requested_by_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    approval_ref: Mapped[str | None] = mapped_column(String(240))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FiberEvent(Base):
    __tablename__ = "fiber_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fiber_events_tenant_id_id"),
        Index("ix_fiber_events_aggregate", "tenant_id", "aggregate_ref", "occurred_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    aggregate_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Structure,
    Cable,
    Strand,
    Splice,
    Termination,
    FieldObservation,
    Change,
    FiberEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "Cable",
    "Change",
    "FiberEvent",
    "FieldObservation",
    "SCHEMA",
    "Splice",
    "Strand",
    "Structure",
    "TENANT_TABLES",
    "Termination",
]
