"""Service-delivery order persistence contract.

Three tenant tables. The order is mutable identity and lifecycle; the decision
and its checks are APPEND-ONLY evidence, enforced here by ORM events as well as
by the service having no update path — the same shape Sub's
`provisioning_readiness_decisions` uses, ported with its immutability rule
rather than without it.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
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

from dotmac_service_orders.contracts import (
    ReadinessCheckKind,
    ReadinessCheckResult,
    ReadinessDecisionStatus,
    ServiceOrderStatus,
    ServiceOrderType,
)

SCHEMA = module_schema("serviceorders")


def _enum(python_type: type[enum.StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class ServiceOrder(Base, TimestampMixin):
    __tablename__ = "service_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_service_orders_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "request_key", name="uq_service_orders_tenant_request_key"
        ),
        Index("ix_service_orders_tenant_customer", "tenant_id", "customer_reference"),
        Index("ix_service_orders_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    customer_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    commercial_order_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    specification_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    service_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_key: Mapped[str] = mapped_column(String(240), nullable=False)
    order_type: Mapped[ServiceOrderType] = mapped_column(
        _enum(ServiceOrderType, "service_order_type"), nullable=False
    )
    status: Mapped[ServiceOrderStatus] = mapped_column(
        _enum(ServiceOrderStatus, "service_order_status"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ServiceOrderReadinessDecision(Base, TimestampMixin):
    __tablename__ = "service_order_readiness_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_readiness_decisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_readiness_decisions_tenant_command"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "service_order_id"],
            [f"{SCHEMA}.service_orders.tenant_id", f"{SCHEMA}.service_orders.id"],
            ondelete="CASCADE",
            name="fk_readiness_decisions_tenant_order",
        ),
        Index(
            "ix_readiness_decisions_tenant_order_decided",
            "tenant_id",
            "service_order_id",
            "decided_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[ReadinessDecisionStatus] = mapped_column(
        _enum(ReadinessDecisionStatus, "readiness_decision_status"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ServiceOrderReadinessCheck(Base, TimestampMixin):
    __tablename__ = "service_order_readiness_checks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_readiness_checks_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "decision_id",
            "kind",
            name="uq_readiness_checks_tenant_decision_kind",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "decision_id"],
            [
                f"{SCHEMA}.service_order_readiness_decisions.tenant_id",
                f"{SCHEMA}.service_order_readiness_decisions.id",
            ],
            ondelete="CASCADE",
            name="fk_readiness_checks_tenant_decision",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    kind: Mapped[ReadinessCheckKind] = mapped_column(
        _enum(ReadinessCheckKind, "readiness_check_kind"), nullable=False
    )
    result: Mapped[ReadinessCheckResult] = mapped_column(
        _enum(ReadinessCheckResult, "readiness_check_result"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReadinessEvidenceImmutableError(RuntimeError):
    """Raised when append-only readiness evidence is updated or deleted."""


@event.listens_for(ServiceOrderReadinessDecision, "before_update")
@event.listens_for(ServiceOrderReadinessDecision, "before_delete")
@event.listens_for(ServiceOrderReadinessCheck, "before_update")
@event.listens_for(ServiceOrderReadinessCheck, "before_delete")
def _reject_readiness_mutation(*_args: object) -> None:
    raise ReadinessEvidenceImmutableError(
        "service-order readiness evidence is append-only"
    )


TENANT_TABLES = (
    "service_orders",
    "service_order_readiness_decisions",
    "service_order_readiness_checks",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        ServiceOrder,
        ServiceOrderReadinessDecision,
        ServiceOrderReadinessCheck,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ReadinessEvidenceImmutableError",
    "ServiceOrder",
    "ServiceOrderReadinessCheck",
    "ServiceOrderReadinessDecision",
    "metadata_table",
]
