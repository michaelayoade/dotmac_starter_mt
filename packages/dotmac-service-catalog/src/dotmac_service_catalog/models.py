"""Technical catalogue persistence."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_service_catalog.contracts import CharacteristicKind

SCHEMA = module_schema("svc_cat")


class ServiceSpecification(Base, TimestampMixin):
    __tablename__ = "service_specifications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_specifications_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_service_specifications_tenant_code"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class PlanFamily(Base, TimestampMixin):
    __tablename__ = "plan_families"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plan_families_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_plan_families_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                f"{SCHEMA}.service_specifications.tenant_id",
                f"{SCHEMA}.service_specifications.id",
            ],
            ondelete="RESTRICT",
            name="fk_plan_families_tenant_specification",
        ),
        Index("ix_plan_families_tenant_specification", "tenant_id", "specification_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class CharacteristicDefinition(Base, TimestampMixin):
    __tablename__ = "characteristic_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_characteristic_definitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "specification_id",
            "code",
            name="uq_characteristic_definitions_tenant_spec_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                f"{SCHEMA}.service_specifications.tenant_id",
                f"{SCHEMA}.service_specifications.id",
            ],
            ondelete="CASCADE",
            name="fk_characteristic_definitions_tenant_specification",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[CharacteristicKind] = mapped_column(
        sa.Enum(
            CharacteristicKind,
            name="service_catalog_characteristic_kind",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EligibilityInputDefinition(Base, TimestampMixin):
    __tablename__ = "eligibility_input_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_eligibility_input_definitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "specification_id",
            "code",
            name="uq_eligibility_input_definitions_tenant_spec_code",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                f"{SCHEMA}.service_specifications.tenant_id",
                f"{SCHEMA}.service_specifications.id",
            ],
            ondelete="CASCADE",
            name="fk_eligibility_input_definitions_tenant_specification",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )


TENANT_TABLES = (
    "service_specifications",
    "plan_families",
    "characteristic_definitions",
    "eligibility_input_definitions",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        ServiceSpecification,
        PlanFamily,
        CharacteristicDefinition,
        EligibilityInputDefinition,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "CharacteristicDefinition",
    "EligibilityInputDefinition",
    "PlanFamily",
    "ServiceSpecification",
    "metadata_table",
]
