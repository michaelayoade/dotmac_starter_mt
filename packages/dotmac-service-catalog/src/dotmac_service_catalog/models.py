"""Versioned, provider-neutral technical catalogue persistence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_service_catalog.contracts import CharacteristicKind

SCHEMA = module_schema("svc_cat")


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class PlanFamily(Base, TimestampMixin):
    """Stable technical grouping; names and availability live on versions."""

    __tablename__ = "plan_families"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_plan_families_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_plan_families_tenant_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(40), nullable=False)


class PlanFamilyVersion(Base, TimestampMixin):
    __tablename__ = "plan_family_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "plan_family_id"],
            [f"{SCHEMA}.plan_families.tenant_id", f"{SCHEMA}.plan_families.id"],
            ondelete="CASCADE",
            name="fk_plan_family_versions_family",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_plan_family_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "plan_family_id",
            name="uq_plan_family_versions_tenant_id_family",
        ),
        UniqueConstraint(
            "tenant_id",
            "plan_family_id",
            "version",
            name="uq_plan_family_versions_identity",
        ),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_plan_family_versions_command"
        ),
        CheckConstraint("version > 0", name="ck_plan_family_versions_version"),
        CheckConstraint(
            "source_version > 0", name="ck_plan_family_versions_source_version"
        ),
        CheckConstraint(
            "state IN ('published', 'superseded', 'withdrawn')",
            name="ck_plan_family_versions_state",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_plan_family_versions_interval",
        ),
        Index(
            "ix_plan_family_versions_effective",
            "tenant_id",
            "plan_family_id",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    plan_family_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class ServiceSpecification(Base, TimestampMixin):
    """Stable technical shape identity beneath one stable plan family."""

    __tablename__ = "service_specifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "plan_family_id"],
            [f"{SCHEMA}.plan_families.tenant_id", f"{SCHEMA}.plan_families.id"],
            ondelete="RESTRICT",
            name="fk_service_specifications_family",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_service_specifications_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "plan_family_id",
            name="uq_service_specifications_tenant_id_family",
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_service_specifications_tenant_code"
        ),
        Index("ix_service_specifications_tenant_family", "tenant_id", "plan_family_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    plan_family_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)


class ServiceSpecificationVersion(Base, TimestampMixin):
    __tablename__ = "service_specification_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "specification_id", "plan_family_id"],
            [
                f"{SCHEMA}.service_specifications.tenant_id",
                f"{SCHEMA}.service_specifications.id",
                f"{SCHEMA}.service_specifications.plan_family_id",
            ],
            ondelete="CASCADE",
            name="fk_service_specification_versions_specification",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "plan_family_version_id", "plan_family_id"],
            [
                f"{SCHEMA}.plan_family_versions.tenant_id",
                f"{SCHEMA}.plan_family_versions.id",
                f"{SCHEMA}.plan_family_versions.plan_family_id",
            ],
            ondelete="RESTRICT",
            name="fk_service_specification_versions_family_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_service_specification_versions_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "specification_id",
            name="uq_service_specification_versions_tenant_id_specification",
        ),
        UniqueConstraint(
            "tenant_id",
            "specification_id",
            "version",
            name="uq_service_specification_versions_identity",
        ),
        UniqueConstraint(
            "tenant_id",
            "command_id",
            name="uq_service_specification_versions_command",
        ),
        CheckConstraint(
            "version > 0", name="ck_service_specification_versions_version"
        ),
        CheckConstraint(
            "source_version > 0",
            name="ck_service_specification_versions_source_version",
        ),
        CheckConstraint(
            "state IN ('published', 'superseded', 'withdrawn')",
            name="ck_service_specification_versions_state",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_service_specification_versions_interval",
        ),
        Index(
            "ix_service_specification_versions_effective",
            "tenant_id",
            "specification_id",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    plan_family_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    plan_family_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class CharacteristicDefinition(Base, TimestampMixin):
    __tablename__ = "characteristic_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_characteristic_definitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "specification_id",
            name="uq_characteristic_definitions_tenant_id_specification",
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
    tenant_id: Mapped[UUID] = _tenant_id()
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


class ServiceSpecificationCharacteristic(Base, TimestampMixin):
    __tablename__ = "service_specification_characteristics"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_service_specification_characteristics_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "specification_version_id", "specification_id"],
            [
                f"{SCHEMA}.service_specification_versions.tenant_id",
                f"{SCHEMA}.service_specification_versions.id",
                f"{SCHEMA}.service_specification_versions.specification_id",
            ],
            ondelete="CASCADE",
            name="fk_service_specification_characteristics_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "definition_id", "specification_id"],
            [
                f"{SCHEMA}.characteristic_definitions.tenant_id",
                f"{SCHEMA}.characteristic_definitions.id",
                f"{SCHEMA}.characteristic_definitions.specification_id",
            ],
            ondelete="RESTRICT",
            name="fk_service_specification_characteristics_definition",
        ),
        UniqueConstraint(
            "tenant_id",
            "specification_version_id",
            "definition_id",
            name="uq_service_specification_characteristics_definition",
        ),
        CheckConstraint(
            "(CASE WHEN string_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN integer_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN decimal_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN boolean_value IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_service_specification_characteristics_one_value",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    specification_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    definition_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    string_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    integer_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decimal_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


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
    tenant_id: Mapped[UUID] = _tenant_id()
    specification_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )


TENANT_TABLES = (
    "plan_families",
    "plan_family_versions",
    "service_specifications",
    "service_specification_versions",
    "characteristic_definitions",
    "service_specification_characteristics",
    "eligibility_input_definitions",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        PlanFamily,
        PlanFamilyVersion,
        ServiceSpecification,
        ServiceSpecificationVersion,
        CharacteristicDefinition,
        ServiceSpecificationCharacteristic,
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
    "PlanFamilyVersion",
    "ServiceSpecification",
    "ServiceSpecificationCharacteristic",
    "ServiceSpecificationVersion",
    "metadata_table",
]
