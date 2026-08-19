"""Tenant-scoped persistence for individual durable assets."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("assets")


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_assets_tenant_code"),
        UniqueConstraint("tenant_id", "serial_number", name="uq_assets_tenant_serial"),
        UniqueConstraint("tenant_id", "tag", name="uq_assets_tenant_tag"),
        CheckConstraint(
            "state IN ('registered', 'in_service', 'out_of_service', "
            "'retired', 'disposed')",
            name="ck_assets_state",
        ),
        CheckConstraint(
            "condition IN ('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_assets_condition",
        ),
        Index("ix_assets_tenant_state", "tenant_id", "state"),
        Index("ix_assets_tenant_kind", "tenant_id", "kind"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acquired_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    condition: Mapped[str] = mapped_column(String(24), nullable=False)
    location_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class AssetAssignment(Base, TimestampMixin):
    __tablename__ = "asset_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [f"{SCHEMA}.assets.tenant_id", f"{SCHEMA}.assets.id"],
            name="fk_asset_assignments_asset",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "preceding_assignment_id"],
            [
                f"{SCHEMA}.asset_assignments.tenant_id",
                f"{SCHEMA}.asset_assignments.id",
            ],
            name="fk_asset_assignments_preceding",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_asset_assignments_tenant_id_id"),
        Index(
            "uq_asset_assignments_one_active",
            "tenant_id",
            "asset_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_asset_assignments_custodian",
            "tenant_id",
            "custodian_id",
            "status",
        ),
        CheckConstraint(
            "preceding_assignment_id IS NULL OR preceding_assignment_id <> id",
            name="ck_asset_assignments_no_self_predecessor",
        ),
        CheckConstraint(
            "status IN ('active', 'returned', 'transferred', 'lost')",
            name="ck_asset_assignments_status",
        ),
        CheckConstraint(
            "condition_on_issue IS NULL OR condition_on_issue IN "
            "('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_asset_assignments_issue_condition",
        ),
        CheckConstraint(
            "condition_on_return IS NULL OR condition_on_return IN "
            "('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_asset_assignments_return_condition",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    custodian_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    preceding_assignment_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_return_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    condition_on_issue: Mapped[str | None] = mapped_column(String(24), nullable=True)
    condition_on_return: Mapped[str | None] = mapped_column(String(24), nullable=True)
    location_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    ended_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class AssetMaintenance(Base, TimestampMixin):
    __tablename__ = "asset_maintenance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [f"{SCHEMA}.assets.tenant_id", f"{SCHEMA}.assets.id"],
            name="fk_asset_maintenance_asset",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_asset_maintenance_tenant_id_id"),
        Index(
            "uq_asset_maintenance_one_in_progress",
            "tenant_id",
            "asset_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
            sqlite_where=text("status = 'in_progress'"),
        ),
        Index("ix_asset_maintenance_due", "tenant_id", "status", "scheduled_for"),
        CheckConstraint(
            "kind IN ('preventive', 'corrective', 'inspection', 'other')",
            name="ck_asset_maintenance_kind",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'completed', 'cancelled')",
            name="ck_asset_maintenance_status",
        ),
        CheckConstraint(
            "asset_state_before IS NULL OR asset_state_before IN "
            "('registered', 'in_service', 'out_of_service', 'retired', 'disposed')",
            name="ck_asset_maintenance_prior_state",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    scheduled_for: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    asset_state_before: Mapped[str | None] = mapped_column(String(24), nullable=True)
    work_performed: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    completed_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class AssetDisposal(Base, TimestampMixin):
    __tablename__ = "asset_disposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [f"{SCHEMA}.assets.tenant_id", f"{SCHEMA}.assets.id"],
            name="fk_asset_disposals_asset",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_asset_disposals_tenant_id_id"),
        Index(
            "uq_asset_disposals_one_open",
            "tenant_id",
            "asset_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved')"),
            sqlite_where=text("status IN ('requested', 'approved')"),
        ),
        Index("ix_asset_disposals_status", "tenant_id", "status"),
        CheckConstraint(
            "method IN ('sale', 'scrap', 'donation', 'theft', 'insurance', "
            "'trade_in', 'transfer')",
            name="ck_asset_disposals_method",
        ),
        CheckConstraint(
            "status IN ('requested', 'approved', 'completed', 'cancelled')",
            name="ck_asset_disposals_status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_on: Mapped[date] = mapped_column(Date, nullable=False)
    requested_by_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    approved_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disposed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    external_authorization_ref: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    external_finance_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssetLifecycleEvent(Base):
    __tablename__ = "asset_lifecycle_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [f"{SCHEMA}.assets.tenant_id", f"{SCHEMA}.assets.id"],
            name="fk_asset_lifecycle_events_asset",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_asset_lifecycle_events_tenant_id_id"
        ),
        Index(
            "ix_asset_lifecycle_events_order",
            "tenant_id",
            "asset_id",
            "occurred_at",
            "id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    previous_custodian_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    new_custodian_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    previous_location_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    new_location_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


TENANT_MODELS = (
    Asset,
    AssetAssignment,
    AssetMaintenance,
    AssetDisposal,
    AssetLifecycleEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Asset",
    "AssetAssignment",
    "AssetDisposal",
    "AssetLifecycleEvent",
    "AssetMaintenance",
]
