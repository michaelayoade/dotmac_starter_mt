"""Per-plane helpers for adopter-owned product-semantic link tables.

The returned tables belong to the adopter's schema and migration lineage. They
are deliberately absent from the subscriptions manifest: this module cannot
own Vendor capability membership or Sub ISP service/access semantics.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

from dotmac_subscriptions.models import SCHEMA


def _names(product_schema: str, table_name: str) -> None:
    if not product_schema or not table_name:
        raise ValueError("product schema and link table name are required")
    if product_schema == SCHEMA:
        raise ValueError("product link tables cannot live in the module schema")


def link_tenant_offer_subject(
    metadata: MetaData, *, product_schema: str, table_name: str
) -> Table:
    _names(product_schema, table_name)
    return Table(
        table_name,
        metadata,
        Column("tenant_id", Uuid(), nullable=False),
        Column("offer_id", Uuid(), nullable=False),
        Column("subject_kind", String(120), nullable=False),
        Column("subject_ref", String(255), nullable=False),
        ForeignKeyConstraint(
            ["tenant_id", "offer_id"],
            [f"{SCHEMA}.offers.tenant_id", f"{SCHEMA}.offers.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "offer_id",
            "subject_kind",
            "subject_ref",
            name=f"uq_{table_name}_identity",
        ),
        schema=product_schema,
    )


def link_platform_offer_subject(
    metadata: MetaData, *, product_schema: str, table_name: str
) -> Table:
    _names(product_schema, table_name)
    return Table(
        table_name,
        metadata,
        Column(
            "offer_id",
            Uuid(),
            ForeignKey(f"{SCHEMA}.platform_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("subject_kind", String(120), nullable=False),
        Column("subject_ref", String(255), nullable=False),
        UniqueConstraint(
            "offer_id",
            "subject_kind",
            "subject_ref",
            name=f"uq_{table_name}_identity",
        ),
        schema=product_schema,
    )


def link_tenant_contract_subject(
    metadata: MetaData, *, product_schema: str, table_name: str
) -> Table:
    _names(product_schema, table_name)
    return Table(
        table_name,
        metadata,
        Column("tenant_id", Uuid(), nullable=False),
        Column("contract_id", Uuid(), nullable=False),
        Column("subject_kind", String(120), nullable=False),
        Column("subject_ref", String(255), nullable=False),
        ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            [
                f"{SCHEMA}.subscription_contracts.tenant_id",
                f"{SCHEMA}.subscription_contracts.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "contract_id",
            "subject_kind",
            "subject_ref",
            name=f"uq_{table_name}_identity",
        ),
        schema=product_schema,
    )


def link_platform_contract_subject(
    metadata: MetaData, *, product_schema: str, table_name: str
) -> Table:
    _names(product_schema, table_name)
    return Table(
        table_name,
        metadata,
        Column(
            "contract_id",
            Uuid(),
            ForeignKey(
                f"{SCHEMA}.platform_subscription_contracts.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        Column("subject_kind", String(120), nullable=False),
        Column("subject_ref", String(255), nullable=False),
        UniqueConstraint(
            "contract_id",
            "subject_kind",
            "subject_ref",
            name=f"uq_{table_name}_identity",
        ),
        schema=product_schema,
    )


__all__ = [
    "link_platform_contract_subject",
    "link_platform_offer_subject",
    "link_tenant_contract_subject",
    "link_tenant_offer_subject",
]
