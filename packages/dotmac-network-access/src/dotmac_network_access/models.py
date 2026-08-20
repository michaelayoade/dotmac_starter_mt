"""Provider-neutral access projection and session persistence in ``mod_netaccess``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("netaccess")


class NasAttachment(Base):
    __tablename__ = "nas_attachments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netaccess_nas_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "nas_ref",
            "access_server_ref",
            name="uq_netaccess_nas_attachment",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    nas_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    access_server_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capability_code: Mapped[str] = mapped_column(String(160), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)


class AccessProjectionRow(Base):
    __tablename__ = "access_projections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_projections_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "subject_ref", name="uq_netaccess_projection_subject"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    desired_state: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_code: Mapped[str] = mapped_column(String(120), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    attributes: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)
    decision_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    desired_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_fingerprint: Mapped[str | None] = mapped_column(String(128))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AuthenticationObservation(Base):
    __tablename__ = "authentication_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netaccess_auth_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netaccess_auth_fingerprint",
        ),
        Index("ix_netaccess_auth_subject", "tenant_id", "subject_ref", "observed_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    nas_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    session_ref: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(120))
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)


class AccountingObservation(Base):
    __tablename__ = "accounting_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_accounting_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netaccess_accounting_fingerprint",
        ),
        Index(
            "ix_netaccess_accounting_session", "tenant_id", "session_ref", "observed_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    nas_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    session_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    input_octets: Mapped[int] = mapped_column(Integer, nullable=False)
    output_octets: Mapped[int] = mapped_column(Integer, nullable=False)
    session_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)


class AccessSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netaccess_sessions_tenant_id_id"),
        UniqueConstraint("tenant_id", "session_ref", name="uq_netaccess_session_ref"),
        Index(
            "ix_netaccess_sessions_subject_state", "tenant_id", "subject_ref", "state"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    nas_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    session_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_reason_code: Mapped[str | None] = mapped_column(String(120))
    close_source_ref: Mapped[str | None] = mapped_column(String(240))
    input_octets: Mapped[int] = mapped_column(Integer, nullable=False)
    output_octets: Mapped[int] = mapped_column(Integer, nullable=False)


class AccessReconciliation(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_reconciliation_tenant_id_id"
        ),
        Index(
            "ix_netaccess_reconciliation_subject",
            "tenant_id",
            "subject_ref",
            "reconciled_at",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    expected_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    drifted: Mapped[bool] = mapped_column(nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(120))
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccessEvent(Base):
    __tablename__ = "access_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netaccess_events_tenant_id_id"),
        Index(
            "ix_netaccess_events_aggregate",
            "tenant_id",
            "aggregate_ref",
            "occurred_at",
        ),
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
    NasAttachment,
    AccessProjectionRow,
    AuthenticationObservation,
    AccountingObservation,
    AccessSession,
    AccessReconciliation,
    AccessEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "AccessEvent",
    "AccessProjectionRow",
    "AccessReconciliation",
    "AccessSession",
    "AccountingObservation",
    "AuthenticationObservation",
    "NasAttachment",
    "SCHEMA",
    "TENANT_TABLES",
]
