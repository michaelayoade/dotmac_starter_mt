"""Tenant-only immutable media facts and disposable current projections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("mediaobs")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NodeDefinition(Base):
    __tablename__ = "node_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_node_defs_tenant_id"),
        UniqueConstraint(
            "tenant_id", "code", "version", name="uq_media_node_defs_identity"
        ),
        Index("ix_media_node_defs_tenant_code", "tenant_id", "code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    traits: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    definition_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_metric_defs_tenant_id"),
        UniqueConstraint(
            "tenant_id", "code", "version", name="uq_media_metric_defs_identity"
        ),
        Index("ix_media_metric_defs_tenant_code", "tenant_id", "code"),
        CheckConstraint(
            "value_type IN ('count','decimal','money','duration','ratio')",
            name="ck_media_metric_defs_value_type",
        ),
        CheckConstraint(
            "observation_origin = 'provider_reported'",
            name="ck_media_metric_defs_provider_origin",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False)
    semantic: Mapped[str] = mapped_column(String(40), nullable=False)
    observation_origin: Mapped[str] = mapped_column(
        String(24), nullable=False, default="provider_reported"
    )
    definition_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class ObservationEnvelope(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_observations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "source_observation_id",
            name="uq_media_observations_source_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "restates_observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_observations_restates",
        ),
        CheckConstraint(
            "kind IN ('entity','hierarchy','metric')",
            name="ck_media_observations_kind",
        ),
        CheckConstraint(
            "normalization_version >= 1", name="ck_media_observations_normalization"
        ),
        CheckConstraint(
            "restatement_depth >= 0", name="ck_media_observations_restatement_depth"
        ),
        Index(
            "ix_media_observations_tenant_source_time",
            "tenant_id",
            "source_observed_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    source_observation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    restates_observation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    restatement_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created_at()


class ObservationReceipt(Base):
    __tablename__ = "observation_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_receipts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "transport_receipt_ref",
            name="uq_media_receipts_transport_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_receipts_observation",
        ),
        Index("ix_media_receipts_tenant_observation", "tenant_id", "observation_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    transport_receipt_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class EntityFact(Base):
    __tablename__ = "entity_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_entity_facts_tenant_id"),
        UniqueConstraint(
            "tenant_id", "observation_id", name="uq_media_entity_facts_observation"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_entity_facts_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_code", "node_version"],
            [
                f"{SCHEMA}.node_definitions.tenant_id",
                f"{SCHEMA}.node_definitions.code",
                f"{SCHEMA}.node_definitions.version",
            ],
            ondelete="RESTRICT",
            name="fk_media_entity_facts_node_definition",
        ),
        CheckConstraint(
            "disposition IN ('present','archived','deleted')",
            name="ck_media_entity_facts_disposition",
        ),
        Index(
            "ix_media_entity_facts_identity",
            "tenant_id",
            "external_account_ref",
            "entity_ref",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    node_code: Mapped[str] = mapped_column(String(80), nullable=False)
    node_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    properties: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class HierarchyFact(Base):
    __tablename__ = "hierarchy_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_hierarchy_facts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "observation_id",
            name="uq_media_hierarchy_facts_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_hierarchy_facts_observation",
        ),
        Index(
            "ix_media_hierarchy_facts_child",
            "tenant_id",
            "external_account_ref",
            "child_entity_ref",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    child_entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class MetricPeriod(Base):
    __tablename__ = "metric_periods"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_periods_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "entity_ref",
            "metric_code",
            "metric_version",
            "period_start",
            "period_end",
            name="uq_media_periods_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_code", "metric_version"],
            [
                f"{SCHEMA}.metric_definitions.tenant_id",
                f"{SCHEMA}.metric_definitions.code",
                f"{SCHEMA}.metric_definitions.version",
            ],
            ondelete="RESTRICT",
            name="fk_media_periods_metric_definition",
        ),
        CheckConstraint("period_start < period_end", name="ck_media_periods_order"),
        Index(
            "ix_media_periods_entity",
            "tenant_id",
            "external_account_ref",
            "entity_ref",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_version: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class MetricFact(Base):
    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_metric_facts_tenant_id"),
        UniqueConstraint(
            "tenant_id", "observation_id", name="uq_media_metric_facts_observation"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_metric_facts_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            [f"{SCHEMA}.metric_periods.tenant_id", f"{SCHEMA}.metric_periods.id"],
            ondelete="RESTRICT",
            name="fk_media_metric_facts_period",
        ),
        CheckConstraint(
            "value_type IN ('count','decimal','money','duration','ratio')",
            name="ck_media_metric_facts_value_type",
        ),
        CheckConstraint(
            "claim_status = 'provider_reported'",
            name="ck_media_metric_facts_claim_status",
        ),
        CheckConstraint(
            "(value_type='count' AND count_value IS NOT NULL AND decimal_value IS NULL "
            "AND money_amount IS NULL AND money_minor_units IS NULL "
            "AND money_currency IS NULL AND money_minor_unit IS NULL "
            "AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='decimal' AND count_value IS NULL "
            "AND decimal_value IS NOT NULL "
            "AND money_amount IS NULL AND money_minor_units IS NULL "
            "AND money_currency IS NULL AND money_minor_unit IS NULL "
            "AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='money' AND count_value IS NULL AND decimal_value IS NULL "
            "AND money_amount IS NOT NULL AND money_minor_units IS NOT NULL "
            "AND money_currency IS NOT NULL AND money_minor_unit IS NOT NULL "
            "AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='duration' AND count_value IS NULL AND decimal_value IS NULL "
            "AND money_amount IS NULL AND money_minor_units IS NULL "
            "AND money_currency IS NULL AND money_minor_unit IS NULL "
            "AND duration_value IS NOT NULL AND ratio_value IS NULL) OR "
            "(value_type='ratio' AND count_value IS NULL AND decimal_value IS NULL "
            "AND money_amount IS NULL AND money_minor_units IS NULL "
            "AND money_currency IS NULL AND money_minor_unit IS NULL "
            "AND duration_value IS NULL AND ratio_value IS NOT NULL)",
            name="ck_media_metric_facts_typed_value",
        ),
        CheckConstraint(
            "count_value IS NULL OR count_value >= 0",
            name="ck_media_metric_facts_count_nonnegative",
        ),
        CheckConstraint(
            "money_minor_unit IS NULL OR money_minor_unit BETWEEN 0 AND 9",
            name="ck_media_metric_facts_minor_unit",
        ),
        CheckConstraint(
            "money_currency IS NULL OR "
            "(length(money_currency) = 3 AND money_currency = upper(money_currency))",
            name="ck_media_metric_facts_currency",
        ),
        CheckConstraint(
            "(value_type!='money') OR (money_amount * CASE money_minor_unit "
            "WHEN 0 THEN 1 WHEN 1 THEN 10 WHEN 2 THEN 100 WHEN 3 THEN 1000 "
            "WHEN 4 THEN 10000 WHEN 5 THEN 100000 WHEN 6 THEN 1000000 "
            "WHEN 7 THEN 10000000 WHEN 8 THEN 100000000 "
            "WHEN 9 THEN 1000000000 END = money_minor_units)",
            name="ck_media_metric_facts_exact_money",
        ),
        Index("ix_media_metric_facts_period", "tenant_id", "period_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    period_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    count_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decimal_value: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18), nullable=True
    )
    money_amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    money_minor_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    money_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    money_minor_unit: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    duration_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ratio_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    claim_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="provider_reported"
    )
    created_at: Mapped[datetime] = _created_at()


class CurrentEntity(Base):
    __tablename__ = "current_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_current_entities_tid"),
        UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "entity_ref",
            name="uq_media_current_entities_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_entities_observation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    node_code: Mapped[str] = mapped_column(String(80), nullable=False)
    node_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    properties: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CurrentHierarchy(Base):
    __tablename__ = "current_hierarchy"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_current_hierarchy_tid"),
        UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "child_entity_ref",
            name="uq_media_current_hierarchy_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_hierarchy_observation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    installation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    child_entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    drift_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CurrentMetric(Base):
    __tablename__ = "current_metrics"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_current_metrics_tid"),
        UniqueConstraint(
            "tenant_id", "period_id", name="uq_media_current_metrics_period"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [f"{SCHEMA}.observations.tenant_id", f"{SCHEMA}.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_metrics_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            [f"{SCHEMA}.metric_periods.tenant_id", f"{SCHEMA}.metric_periods.id"],
            ondelete="RESTRICT",
            name="fk_media_current_metrics_period",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    period_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReconciliationEvidence(Base):
    __tablename__ = "reconciliation_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_reconcile_tenant_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    drift_count: Mapped[int] = mapped_column(Integer, nullable=False)
    before_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    applied: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


APPEND_ONLY_TABLES: tuple[sa.Table, ...] = (
    NodeDefinition.__table__,
    MetricDefinition.__table__,
    ObservationEnvelope.__table__,
    ObservationReceipt.__table__,
    EntityFact.__table__,
    HierarchyFact.__table__,
    MetricPeriod.__table__,
    MetricFact.__table__,
    ReconciliationEvidence.__table__,
)

PROJECTION_TABLES: tuple[sa.Table, ...] = (
    CurrentEntity.__table__,
    CurrentHierarchy.__table__,
    CurrentMetric.__table__,
)

ALL_TABLES: tuple[sa.Table, ...] = (
    NodeDefinition.__table__,
    MetricDefinition.__table__,
    ObservationEnvelope.__table__,
    ObservationReceipt.__table__,
    EntityFact.__table__,
    HierarchyFact.__table__,
    MetricPeriod.__table__,
    MetricFact.__table__,
    CurrentEntity.__table__,
    CurrentHierarchy.__table__,
    CurrentMetric.__table__,
    ReconciliationEvidence.__table__,
)

TENANT_TABLES: tuple[str, ...] = tuple(table.name for table in ALL_TABLES)

__all__ = [
    "ALL_TABLES",
    "APPEND_ONLY_TABLES",
    "PROJECTION_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "CurrentEntity",
    "CurrentHierarchy",
    "CurrentMetric",
    "EntityFact",
    "HierarchyFact",
    "MetricDefinition",
    "MetricFact",
    "MetricPeriod",
    "NodeDefinition",
    "ObservationEnvelope",
    "ObservationReceipt",
    "ReconciliationEvidence",
]
