"""Tenant-only declared-record persistence in ``mod_records``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("records")


def _tenant_id() -> MappedColumn[uuid.UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class RetentionScheduleVersion(Base):
    __tablename__ = "retention_schedule_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_retention_schedule_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "schedule_code",
            "version",
            name="uq_retention_schedule_versions_code_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    schedule_code: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    trigger_event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    duration_days: Mapped[int | None] = mapped_column(Integer())
    permanent: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    cutoff_rule: Mapped[str] = mapped_column(String(40), nullable=False)
    final_action: Mapped[str] = mapped_column(String(40), nullable=False)
    disposition_approval_policy: Mapped[str] = mapped_column(
        String(160), nullable=False
    )
    review_cadence_days: Mapped[int] = mapped_column(Integer(), nullable=False)
    authority: Mapped[str] = mapped_column(Text(), nullable=False)
    accountable_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RecordSeriesVersion(Base):
    __tablename__ = "record_series_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_record_series_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "series_code",
            "version",
            name="uq_record_series_versions_code_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "default_schedule_code", "default_schedule_version"],
            [
                f"{SCHEMA}.retention_schedule_versions.tenant_id",
                f"{SCHEMA}.retention_schedule_versions.schedule_code",
                f"{SCHEMA}.retention_schedule_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_record_series_versions_default_schedule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_series_code", "parent_series_version"],
            [
                f"{SCHEMA}.record_series_versions.tenant_id",
                f"{SCHEMA}.record_series_versions.series_code",
                f"{SCHEMA}.record_series_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_record_series_versions_parent",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    series_code: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    parent_series_code: Mapped[str | None] = mapped_column(String(120))
    parent_series_version: Mapped[int | None] = mapped_column(Integer())
    responsible_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    custodian: Mapped[str] = mapped_column(String(160), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(80), nullable=False)
    regulatory_basis: Mapped[str] = mapped_column(Text(), nullable=False)
    default_schedule_code: Mapped[str] = mapped_column(String(120), nullable=False)
    default_schedule_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    vital_record: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    confidentiality: Mapped[str] = mapped_column(String(80), nullable=False)
    transfer_destination: Mapped[str] = mapped_column(String(255), nullable=False)
    required_fields: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_records_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_type",
            "source_id",
            "source_version",
            name="uq_records_exact_source_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "series_code", "series_version"],
            [
                f"{SCHEMA}.record_series_versions.tenant_id",
                f"{SCHEMA}.record_series_versions.series_code",
                f"{SCHEMA}.record_series_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_records_series_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "schedule_code", "schedule_version"],
            [
                f"{SCHEMA}.retention_schedule_versions.tenant_id",
                f"{SCHEMA}.retention_schedule_versions.schedule_code",
                f"{SCHEMA}.retention_schedule_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_records_schedule_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_records_supersedes",
        ),
        Index("ix_records_tenant_state", "tenant_id", "state"),
        Index("ix_records_retention_due", "tenant_id", "retention_due_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    source_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(160), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(255), nullable=False)
    source_provenance: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column()
    checksum_sha256: Mapped[str | None] = mapped_column(String(71))
    media_type: Mapped[str | None] = mapped_column(String(200))
    byte_length: Mapped[int | None] = mapped_column(BigInteger())
    series_code: Mapped[str] = mapped_column(String(120), nullable=False)
    series_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    schedule_code: Mapped[str] = mapped_column(String(120), nullable=False)
    schedule_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    record_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON(), nullable=False
    )
    sensitivity: Mapped[str] = mapped_column(String(80), nullable=False)
    access_restrictions: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    declaration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    supersedes_record_id: Mapped[uuid.UUID | None] = mapped_column()
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    retention_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    retention_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_evidence_ref: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RecordTriggerObservation(Base):
    __tablename__ = "record_trigger_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_record_trigger_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_record_trigger_observations_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_record_trigger_observations_record",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    source_version: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provenance: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)


class LegalHoldCase(Base):
    __tablename__ = "legal_hold_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_legal_hold_cases_tenant_id_id"),
        UniqueConstraint("tenant_id", "case_code", name="uq_legal_hold_cases_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    case_code: Mapped[str] = mapped_column(String(120), nullable=False)
    authority: Mapped[str] = mapped_column(Text(), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    responsible_officer: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ongoing_capture_rule: Mapped[dict[str, object] | None] = mapped_column(JSON())
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[uuid.UUID | None] = mapped_column()
    release_reason: Mapped[str | None] = mapped_column(Text())


class LegalHoldTarget(Base):
    __tablename__ = "legal_hold_targets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_legal_hold_targets_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [f"{SCHEMA}.legal_hold_cases.tenant_id", f"{SCHEMA}.legal_hold_cases.id"],
            ondelete="RESTRICT",
            name="fk_legal_hold_targets_case",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_legal_hold_targets_record",
        ),
        Index("ix_legal_hold_targets_record", "tenant_id", "record_id", "released_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    record_id: Mapped[uuid.UUID | None] = mapped_column()
    series_code: Mapped[str | None] = mapped_column(String(120))
    series_version: Mapped[int | None] = mapped_column(Integer())
    cohort_fingerprint: Mapped[str | None] = mapped_column(String(64))
    cohort_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON())
    placed_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[uuid.UUID | None] = mapped_column()
    release_reason: Mapped[str | None] = mapped_column(Text())


class DispositionBatch(Base):
    __tablename__ = "disposition_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_disposition_batches_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "content_digest", name="uq_disposition_batches_digest"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column()
    approval_digest: Mapped[str | None] = mapped_column(String(71))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column()


class DispositionItem(Base):
    __tablename__ = "disposition_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_disposition_items_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "batch_id",
            "record_id",
            name="uq_disposition_items_batch_record",
        ),
        UniqueConstraint(
            "tenant_id", "authorization_id", name="uq_disposition_items_authorization"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                f"{SCHEMA}.disposition_batches.tenant_id",
                f"{SCHEMA}.disposition_batches.id",
            ],
            ondelete="RESTRICT",
            name="fk_disposition_items_batch",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_disposition_items_record",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    batch_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    eligibility_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON(), nullable=False
    )
    final_action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(40))
    authorization_id: Mapped[uuid.UUID | None] = mapped_column()
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    physical_state: Mapped[str | None] = mapped_column(String(80))
    provider_evidence_ref: Mapped[str | None] = mapped_column(String(500))


class CustodyTransfer(Base):
    __tablename__ = "custody_transfers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_custody_transfers_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_custody_transfers_record",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_custodian: Mapped[str] = mapped_column(String(255), nullable=False)
    to_custodian: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_file_id: Mapped[uuid.UUID | None] = mapped_column()
    manifest_checksum_sha256: Mapped[str | None] = mapped_column(String(71))
    transferred_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_evidence: Mapped[dict[str, object] | None] = mapped_column(JSON())


class PreservationCheck(Base):
    __tablename__ = "preservation_checks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_preservation_checks_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "record_id",
            "source_owner",
            "source_observation_id",
            name="uq_preservation_checks_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_preservation_checks_record",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    source_observation_id: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_checksum_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    observed_checksum_sha256: Mapped[str | None] = mapped_column(String(71))
    physical_state: Mapped[str] = mapped_column(String(80), nullable=False)
    storage_location_observation: Mapped[str | None] = mapped_column(String(500))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)


class RecordEvent(Base):
    __tablename__ = "record_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_record_events_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            [f"{SCHEMA}.records.tenant_id", f"{SCHEMA}.records.id"],
            ondelete="RESTRICT",
            name="fk_record_events_record",
        ),
        Index("ix_record_events_timeline", "tenant_id", "record_id", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    RetentionScheduleVersion,
    RecordSeriesVersion,
    Record,
    RecordTriggerObservation,
    LegalHoldCase,
    LegalHoldTarget,
    DispositionBatch,
    DispositionItem,
    CustodyTransfer,
    PreservationCheck,
    RecordEvent,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "TABLES",
    "CustodyTransfer",
    "DispositionBatch",
    "DispositionItem",
    "LegalHoldCase",
    "LegalHoldTarget",
    "PreservationCheck",
    "Record",
    "RecordEvent",
    "RecordSeriesVersion",
    "RecordTriggerObservation",
    "RetentionScheduleVersion",
    "SCHEMA",
]
