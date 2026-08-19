"""Tenant-only persistence for first-party web analytics (ADR-0035)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("webanalytics")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), sa.ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class AnalyticsProperty(Base, TimestampMixin):
    __tablename__ = "analytics_properties"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analytics_properties_tenant_id"),
        UniqueConstraint(
            "tenant_id", "code", name="uq_analytics_properties_tenant_code"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    allowed_origins: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_evidence_days: Mapped[int] = mapped_column(Integer, nullable=False)
    active_generation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class AnalyticsStream(Base, TimestampMixin):
    __tablename__ = "analytics_streams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_analytics_streams_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "id",
            name="uq_analytics_streams_property_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            name="uq_analytics_streams_property_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            [
                f"{SCHEMA}.analytics_properties.tenant_id",
                f"{SCHEMA}.analytics_properties.id",
            ],
            ondelete="CASCADE",
            name="fk_analytics_streams_property",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    accepted_protocol_versions: Mapped[list[int]] = mapped_column(_JSON, nullable=False)


class EventObservation(Base):
    """Immutable accepted content. No ``updated_at`` and no aggregate counter."""

    __tablename__ = "event_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_event_observations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            name="uq_event_observations_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            [
                f"{SCHEMA}.analytics_properties.tenant_id",
                f"{SCHEMA}.analytics_properties.id",
            ],
            ondelete="CASCADE",
            name="fk_event_observations_property",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "property_id", "stream_id"],
            [
                f"{SCHEMA}.analytics_streams.tenant_id",
                f"{SCHEMA}.analytics_streams.property_id",
                f"{SCHEMA}.analytics_streams.id",
            ],
            ondelete="CASCADE",
            name="fk_event_observations_stream",
        ),
        Index(
            "ix_event_observations_property_time",
            "tenant_id",
            "property_id",
            "occurred_at",
        ),
        Index("ix_event_observations_expiry", "tenant_id", "property_id", "expires_at"),
        Index(
            "ix_event_observations_visitor",
            "tenant_id",
            "property_id",
            "visitor_digest",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    stream_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_code: Mapped[str] = mapped_column(String(96), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    visitor_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    pseudonym_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    referrer_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    referrer_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    acquisition_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acquisition_medium: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acquisition_campaign: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acquisition_term: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acquisition_content: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_class: Mapped[str] = mapped_column(String(16), nullable=False)
    attributes_json: Mapped[list[list[str | int | bool]]] = mapped_column(
        _JSON, nullable=False
    )
    privacy_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    consent_state: Mapped[str] = mapped_column(String(16), nullable=False)
    global_privacy_control: Mapped[bool] = mapped_column(Boolean, nullable=False)
    do_not_track: Mapped[bool] = mapped_column(Boolean, nullable=False)
    privacy_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    adapter_code: Mapped[str] = mapped_column(String(96), nullable=False)
    admission_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    admission_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    transport_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_system: Mapped[str] = mapped_column(String(96), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EventReplayTombstone(Base):
    __tablename__ = "event_replay_tombstones"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_event_replay_tombstones_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            name="uq_event_replay_tombstones_identity",
        ),
        Index(
            "ix_event_replay_tombstones_expiry",
            "tenant_id",
            "property_id",
            "expires_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    stream_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class EventConflictEvidence(Base):
    __tablename__ = "event_conflict_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_event_conflicts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            "presented_fingerprint",
            name="uq_event_conflicts_presented",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    stream_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    existing_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    presented_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(96), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EventClassificationEvidence(Base):
    __tablename__ = "event_classification_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_event_classifications_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "observation_id",
            "classifier_code",
            "classifier_version",
            name="uq_event_classifications_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.event_observations.tenant_id",
                f"{SCHEMA}.event_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_event_classifications_observation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    classifier_code: Mapped[str] = mapped_column(String(96), nullable=False)
    classifier_version: Mapped[int] = mapped_column(Integer, nullable=False)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    analytically_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(_JSON, nullable=False)


class SessionizationRuleRow(Base):
    __tablename__ = "sessionization_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sessionization_rules_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            "version",
            name="uq_sessionization_rules_version",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    inactivity_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectionGeneration(Base):
    __tablename__ = "projection_generations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_projection_generations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "id",
            name="uq_projection_generations_property_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_rule_code: Mapped[str] = mapped_column(String(96), nullable=False)
    session_rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    authoritative_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    projection_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class VisitorProjectionRow(Base):
    __tablename__ = "visitor_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_visitor_projections_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "visitor_digest",
            name="uq_visitor_projections_identity",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    visitor_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    pseudonym_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SessionProjectionRow(Base):
    __tablename__ = "session_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_session_projections_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "session_key",
            name="uq_session_projections_identity",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    session_key: Mapped[str] = mapped_column(String(71), nullable=False)
    visitor_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(96), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SessionEventLink(Base):
    __tablename__ = "session_event_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_session_event_links_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generation_id",
            "observation_id",
            name="uq_session_event_links_observation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    session_key: Mapped[str] = mapped_column(String(71), nullable=False)


class AggregateMetric(Base):
    __tablename__ = "aggregate_metrics"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_aggregate_metrics_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "bucket_start",
            "dimension",
            "dimension_key",
            name="uq_aggregate_metrics_bucket",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    dimension_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    visitor_count: Mapped[int] = mapped_column(Integer, nullable=False)
    session_count: Mapped[int] = mapped_column(Integer, nullable=False)


class FunnelDefinitionRow(Base):
    __tablename__ = "funnel_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_funnel_definitions_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            "version",
            name="uq_funnel_definitions_version",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    steps_json: Mapped[list[list[str | int]]] = mapped_column(_JSON, nullable=False)
    within_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class FunnelResultRow(Base):
    __tablename__ = "funnel_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_funnel_results_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "definition_code",
            "definition_version",
            name="uq_funnel_results_generation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    definition_code: Mapped[str] = mapped_column(String(96), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entrants: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_by_step_json: Mapped[list[int]] = mapped_column(_JSON, nullable=False)


class PrivacyDeletionEvidence(Base):
    __tablename__ = "privacy_deletion_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_privacy_deletions_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "request_id",
            name="uq_privacy_deletions_request",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deleted_observations: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class ProjectionDriftEvidence(Base):
    __tablename__ = "projection_drift_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_projection_drift_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "detected_at",
            "projection_digest",
            name="uq_projection_drift_observation",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    property_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    generation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    authoritative_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    projection_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    repaired_generation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


TENANT_MODELS = (
    AnalyticsProperty,
    AnalyticsStream,
    EventObservation,
    EventReplayTombstone,
    EventConflictEvidence,
    EventClassificationEvidence,
    SessionizationRuleRow,
    ProjectionGeneration,
    VisitorProjectionRow,
    SessionProjectionRow,
    SessionEventLink,
    AggregateMetric,
    FunnelDefinitionRow,
    FunnelResultRow,
    PrivacyDeletionEvidence,
    ProjectionDriftEvidence,
)
TENANT_TABLES: tuple[str, ...] = tuple(model.__tablename__ for model in TENANT_MODELS)
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "event_observations",
    "event_replay_tombstones",
    "event_conflict_evidence",
    "event_classification_evidence",
    "privacy_deletion_evidence",
    "projection_drift_evidence",
)

__all__ = [
    "APPEND_ONLY_TABLES",
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "AggregateMetric",
    "AnalyticsProperty",
    "AnalyticsStream",
    "EventClassificationEvidence",
    "EventConflictEvidence",
    "EventObservation",
    "EventReplayTombstone",
    "FunnelDefinitionRow",
    "FunnelResultRow",
    "PrivacyDeletionEvidence",
    "ProjectionDriftEvidence",
    "ProjectionGeneration",
    "SessionEventLink",
    "SessionProjectionRow",
    "SessionizationRuleRow",
    "VisitorProjectionRow",
]
