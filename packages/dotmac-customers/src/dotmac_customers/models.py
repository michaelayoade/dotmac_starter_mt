"""Customer-account persistence contract."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_customers.contracts import AccountStatus, PartyReferenceRole

SCHEMA = module_schema("customers")


class CustomerAccount(Base, TimestampMixin):
    __tablename__ = "customer_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customer_accounts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "account_number", name="uq_customer_accounts_tenant_number"
        ),
        Index("ix_customer_accounts_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    account_number: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        sa.Enum(
            AccountStatus,
            name="customers_account_status",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=AccountStatus.PROSPECT,
        server_default=AccountStatus.PROSPECT.value,
    )


class CustomerProfile(Base, TimestampMixin):
    __tablename__ = "customer_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customer_profiles_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "account_id", name="uq_customer_profiles_tenant_account"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.customer_accounts.tenant_id", f"{SCHEMA}.customer_accounts.id"],
            ondelete="CASCADE",
            name="fk_customer_profiles_tenant_account",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerPartyReference(Base, TimestampMixin):
    __tablename__ = "customer_party_references"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_customer_party_references_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "account_id",
            "party_system",
            "party_reference",
            "role",
            name="uq_customer_party_references_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [f"{SCHEMA}.customer_accounts.tenant_id", f"{SCHEMA}.customer_accounts.id"],
            ondelete="CASCADE",
            name="fk_customer_party_references_tenant_account",
        ),
        Index(
            "ix_customer_party_references_tenant_party",
            "tenant_id",
            "party_system",
            "party_reference",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    party_system: Mapped[str] = mapped_column(String(80), nullable=False)
    party_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[PartyReferenceRole] = mapped_column(
        sa.Enum(
            PartyReferenceRole,
            name="customers_party_reference_role",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
    )


TENANT_TABLES: tuple[str, ...] = (
    "customer_accounts",
    "customer_profiles",
    "customer_party_references",
)
_TABLE_BY_NAME: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (CustomerAccount, CustomerProfile, CustomerPartyReference)
}


def metadata_table(table_name: str) -> sa.Table:
    return _TABLE_BY_NAME[table_name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "CustomerAccount",
    "CustomerPartyReference",
    "CustomerProfile",
    "metadata_table",
]
