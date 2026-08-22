"""Link and rebuildable path persistence in ``mod_nettop``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("nettop")


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_nettop_links_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "left_ref",
            "right_ref",
            "kind",
            "source_ref",
            name="uq_nettop_link_identity",
        ),
        Index(
            "ix_nettop_links_endpoints", "tenant_id", "left_ref", "right_ref", "state"
        ),
        Index(
            "uq_nettop_observed_fingerprint",
            "tenant_id",
            "source_ref",
            "fingerprint",
            unique=True,
            postgresql_where=text("fingerprint IS NOT NULL"),
            sqlite_where=text("fingerprint IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    left_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    right_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    cost: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PathProjection(Base):
    __tablename__ = "path_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_nettop_paths_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "source_ref", "destination_ref", name="uq_nettop_path_identity"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    hop_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    link_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    total_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReachabilityProjection(Base):
    __tablename__ = "reachability_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_nettop_reachability_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "from_ref",
            name="uq_nettop_reachability_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "path_id"],
            [f"{SCHEMA}.path_projections.tenant_id", f"{SCHEMA}.path_projections.id"],
            name="fk_nettop_reachability_path",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    from_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    path_id: Mapped[uuid.UUID | None] = mapped_column()
    reason_code: Mapped[str | None] = mapped_column(String(120))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CoverageGapRow(Base):
    __tablename__ = "coverage_gaps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_nettop_gaps_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "scope_ref", "missing_ref", name="uq_nettop_gap_identity"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    scope_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    missing_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TopologyEvent(Base):
    __tablename__ = "topology_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_nettop_events_tenant_id_id"),
        Index(
            "ix_nettop_events_aggregate", "tenant_id", "aggregate_ref", "occurred_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    aggregate_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Link,
    PathProjection,
    ReachabilityProjection,
    CoverageGapRow,
    TopologyEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "CoverageGapRow",
    "Link",
    "PathProjection",
    "ReachabilityProjection",
    "SCHEMA",
    "TENANT_TABLES",
    "TopologyEvent",
]
