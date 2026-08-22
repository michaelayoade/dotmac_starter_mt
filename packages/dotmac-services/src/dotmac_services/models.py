"""Service lifecycle persistence contract."""

from __future__ import annotations

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
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_services.contracts import ServiceStatus

SCHEMA = module_schema("services")


def _status(name: str) -> sa.Enum:
    return sa.Enum(
        ServiceStatus,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


class ServiceInstance(Base, TimestampMixin):
    __tablename__ = "service_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_service_instances_tenant_id_id"),
        Index(
            "ix_service_instances_tenant_customer", "tenant_id", "customer_reference"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    customer_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    specification_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    qualification_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    status: Mapped[ServiceStatus] = mapped_column(
        _status("service_instance_status"), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ServiceLifecycleEvent(Base, TimestampMixin):
    __tablename__ = "service_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_lifecycle_events_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "service_id"],
            [
                f"{SCHEMA}.service_instances.tenant_id",
                f"{SCHEMA}.service_instances.id",
            ],
            ondelete="CASCADE",
            name="fk_service_lifecycle_events_tenant_service",
        ),
        Index(
            "ix_service_lifecycle_events_tenant_service_time",
            "tenant_id",
            "service_id",
            "occurred_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    from_status: Mapped[ServiceStatus] = mapped_column(
        _status("service_event_from_status"), nullable=False
    )
    to_status: Mapped[ServiceStatus] = mapped_column(
        _status("service_event_to_status"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)


TENANT_TABLES = ("service_instances", "service_lifecycle_events")
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (ServiceInstance, ServiceLifecycleEvent)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "ServiceInstance",
    "ServiceLifecycleEvent",
    "metadata_table",
]
