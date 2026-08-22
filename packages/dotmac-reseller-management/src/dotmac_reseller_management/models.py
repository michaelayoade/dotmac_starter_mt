"""Tenant-only reseller persistence in ``mod_reseller``."""

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
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("reseller")


def tenant_id_column() -> MappedColumn[UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class ResellerAccount(Base, TimestampMixin):
    __tablename__ = "reseller_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_reseller_accounts_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_reseller_accounts_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "party_role_ref",
            name="uq_reseller_accounts_tenant_party_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_account_id"],
            [
                f"{SCHEMA}.reseller_accounts.tenant_id",
                f"{SCHEMA}.reseller_accounts.id",
            ],
            ondelete="RESTRICT",
            name="fk_reseller_accounts_parent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "current_authority_revision_id"],
            [
                f"{SCHEMA}.reseller_authority_revisions.tenant_id",
                f"{SCHEMA}.reseller_authority_revisions.id",
            ],
            name="fk_reseller_accounts_current_authority",
            use_alter=True,
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    party_role_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_account_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_authority_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")


class ResellerAuthorityRevision(Base):
    __tablename__ = "reseller_authority_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_reseller_authority_revisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "account_id",
            "version_number",
            name="uq_reseller_authority_revisions_tenant_account_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "account_id",
            "evidence_ref",
            name="uq_reseller_authority_revisions_tenant_account_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                f"{SCHEMA}.reseller_accounts.tenant_id",
                f"{SCHEMA}.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_authority_revisions_account",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    authority_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResellerMemberBinding(Base):
    __tablename__ = "reseller_member_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_reseller_member_bindings_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "member_ref", name="uq_reseller_member_bindings_tenant_ref"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                f"{SCHEMA}.reseller_accounts.tenant_id",
                f"{SCHEMA}.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_member_bindings_account",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    member_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResellerCustomerAccountBinding(Base):
    __tablename__ = "reseller_customer_account_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_reseller_customer_account_bindings_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "customer_account_ref",
            name="uq_reseller_customer_account_bindings_tenant_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                f"{SCHEMA}.reseller_accounts.tenant_id",
                f"{SCHEMA}.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_customer_account_bindings_account",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


ALL_MODELS = (
    ResellerAccount,
    ResellerAuthorityRevision,
    ResellerMemberBinding,
    ResellerCustomerAccountBinding,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "TABLES",
    "ResellerAccount",
    "ResellerAuthorityRevision",
    "ResellerCustomerAccountBinding",
    "ResellerMemberBinding",
]
