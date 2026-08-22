"""Service-access policy persistence contract."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_service_access_policy.contracts import AccessSignal, DesiredAccess

SCHEMA = module_schema("serviceaccess")


class ServiceAccessInput(Base, TimestampMixin):
    __tablename__ = "service_access_inputs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_access_inputs_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "service_reference",
            "signal",
            name="uq_service_access_inputs_tenant_service_signal",
        ),
        Index(
            "ix_service_access_inputs_tenant_service",
            "tenant_id",
            "service_reference",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    signal: Mapped[AccessSignal] = mapped_column(
        sa.Enum(
            AccessSignal,
            name="service_access_signal",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DesiredAccessDecision(Base, TimestampMixin):
    __tablename__ = "desired_access_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_desired_access_decisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "service_reference",
            name="uq_desired_access_decisions_tenant_service",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    desired_access: Mapped[DesiredAccess] = mapped_column(
        sa.Enum(
            DesiredAccess,
            name="desired_service_access",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES = ("service_access_inputs", "desired_access_decisions")
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (ServiceAccessInput, DesiredAccessDecision)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "DesiredAccessDecision",
    "ServiceAccessInput",
    "metadata_table",
]
