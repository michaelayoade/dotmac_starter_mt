"""Service-change request persistence contract."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_service_changes.contracts import (
    CheckpointDomain,
    ExecutionState,
    ServiceChangeStatus,
    ServiceChangeType,
)

SCHEMA = module_schema("servicechanges")


def _enum(python_type: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class ServiceChangeRequest(Base, TimestampMixin):
    __tablename__ = "service_change_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_change_requests_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "confirmation_key",
            name="uq_service_change_requests_tenant_confirmation",
        ),
        Index(
            "ix_service_change_requests_tenant_subject",
            "tenant_id",
            "subject_reference",
        ),
        Index("ix_service_change_requests_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    confirmation_key: Mapped[str] = mapped_column(String(240), nullable=False)
    change_type: Mapped[ServiceChangeType] = mapped_column(
        _enum(ServiceChangeType, "service_change_type"), nullable=False
    )
    status: Mapped[ServiceChangeStatus] = mapped_column(
        _enum(ServiceChangeStatus, "service_change_status"), nullable=False
    )
    execution_state: Mapped[ExecutionState | None] = mapped_column(
        _enum(ExecutionState, "service_change_execution_state"), nullable=True
    )
    current_offer_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    requested_offer_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    target_location_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ServiceChangeCheckpoint(Base, TimestampMixin):
    """Append-only evidence that one crossed owner reached a named point.

    Sub carried each crossed owner as its own nullable FK column on the request
    (`service_qualification_id`, `field_fee_invoice_id`, `field_fee_payment_id`,
    `service_order_id`, `work_order_id`, ...). That shape cannot record WHEN a
    domain was reached, cannot hold two observations for one domain, and grows a
    column per new collaborator. One typed row per (request, domain, evidence)
    records the same facts without a schema change per owner.
    """

    __tablename__ = "service_change_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_change_checkpoints_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "domain",
            "evidence_reference",
            name="uq_service_change_checkpoints_tenant_request_domain_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                f"{SCHEMA}.service_change_requests.tenant_id",
                f"{SCHEMA}.service_change_requests.id",
            ],
            ondelete="CASCADE",
            name="fk_service_change_checkpoints_tenant_request",
        ),
        Index(
            "ix_service_change_checkpoints_tenant_request_observed",
            "tenant_id",
            "request_id",
            "observed_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    domain: Mapped[CheckpointDomain] = mapped_column(
        _enum(CheckpointDomain, "service_change_checkpoint_domain"), nullable=False
    )
    evidence_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ServiceChangeCheckpointImmutableError(RuntimeError):
    """Raised when append-only checkpoint evidence is updated or deleted."""


@event.listens_for(ServiceChangeCheckpoint, "before_update")
@event.listens_for(ServiceChangeCheckpoint, "before_delete")
def _reject_checkpoint_mutation(*_args: object) -> None:
    raise ServiceChangeCheckpointImmutableError(
        "service-change checkpoints are append-only"
    )


TENANT_TABLES = ("service_change_requests", "service_change_checkpoints")
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (ServiceChangeRequest, ServiceChangeCheckpoint)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ServiceChangeCheckpoint",
    "ServiceChangeCheckpointImmutableError",
    "ServiceChangeRequest",
    "metadata_table",
]
