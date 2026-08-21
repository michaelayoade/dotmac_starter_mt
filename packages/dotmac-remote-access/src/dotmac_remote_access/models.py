"""Tenant-only request, grant and execution-observation evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("remoteaccess")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class RemoteAccessRequest(Base, TimestampMixin):
    __tablename__ = "remote_access_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remote_requests_tenant_id_id"),
        UniqueConstraint("tenant_id", "request_key", name="uq_remote_requests_key"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    requester_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_evidence_ref: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RemoteAccessGrant(Base, TimestampMixin):
    __tablename__ = "remote_access_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remote_grants_tenant_id_id"),
        UniqueConstraint("tenant_id", "request_id", name="uq_remote_grants_request"),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                f"{SCHEMA}.remote_access_requests.tenant_id",
                f"{SCHEMA}.remote_access_requests.id",
            ],
            name="fk_remote_grants_request",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)


class RemoteAccessObservation(Base):
    __tablename__ = "remote_access_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_remote_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "observation_key", name="uq_remote_observations_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "grant_id"],
            [
                f"{SCHEMA}.remote_access_grants.tenant_id",
                f"{SCHEMA}.remote_access_grants.id",
            ],
            name="fk_remote_observations_grant",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    grant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)


TENANT_MODELS = (RemoteAccessRequest, RemoteAccessGrant, RemoteAccessObservation)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "RemoteAccessGrant",
    "RemoteAccessObservation",
    "RemoteAccessRequest",
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
