"""Platform-only request, finite-grant and event evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_support_access.contracts import FiniteGrantDescriptor

SCHEMA = module_schema("supportaccess")


class SupportAccessRequest(Base, TimestampMixin):
    __tablename__ = "support_access_requests"
    __table_args__ = (
        UniqueConstraint("request_key", name="uq_support_requests_key"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    case_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    requester_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_evidence_ref: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportAccessGrant(Base, TimestampMixin):
    __tablename__ = "support_access_grants"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_support_grants_request"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.support_access_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    requester_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    consent_evidence_ref: Mapped[str | None] = mapped_column(String(200))
    break_glass_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)

    def descriptor(self) -> FiniteGrantDescriptor:
        return FiniteGrantDescriptor(
            self.id,
            self.case_ref,
            self.purpose,
            self.target_ref,
            self.requester_ref,
            tuple(self.capabilities),
            self.mode,
            self.issued_at,
            self.expires_at,
        )


class SupportAccessEvent(Base):
    __tablename__ = "support_access_events"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_support_events_sequence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.support_access_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.support_access_grants.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_ref: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


PLATFORM_MODELS = (SupportAccessRequest, SupportAccessGrant, SupportAccessEvent)
PLATFORM_TABLES = tuple(model.__tablename__ for model in PLATFORM_MODELS)

__all__ = [
    "PLATFORM_MODELS",
    "PLATFORM_TABLES",
    "SCHEMA",
    "SupportAccessEvent",
    "SupportAccessGrant",
    "SupportAccessRequest",
]
