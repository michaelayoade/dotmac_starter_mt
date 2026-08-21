"""Five-table tenant editorial persistence contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_content.lifecycle import ContentItemState, ContentPlanStatus

SCHEMA = module_schema("content")


class ContentPlan(Base, TimestampMixin):
    __tablename__ = "content_plans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_content_plans_tenant_id_id"),
        CheckConstraint(
            "ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on",
            name="ck_content_plans_date_order",
        ),
        Index("ix_content_plans_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ContentPlanStatus] = mapped_column(
        sa.Enum(
            ContentPlanStatus,
            name="content_plan_status",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=ContentPlanStatus.DRAFT,
        server_default=ContentPlanStatus.DRAFT.value,
    )
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_content_items_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "content_plan_id"],
            [f"{SCHEMA}.content_plans.tenant_id", f"{SCHEMA}.content_plans.id"],
            ondelete="CASCADE",
            name="fk_content_items_tenant_plan",
        ),
        Index("ix_content_items_tenant_plan", "tenant_id", "content_plan_id"),
        Index("ix_content_items_tenant_state", "tenant_id", "state"),
        Index("ix_content_items_tenant_planned", "tenant_id", "planned_for"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    content_plan_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ContentItemState] = mapped_column(
        sa.Enum(
            ContentItemState,
            name="content_item_state",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=ContentItemState.DRAFT,
        server_default=ContentItemState.DRAFT.value,
    )
    planned_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class ContentVariant(Base, TimestampMixin):
    __tablename__ = "content_variants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_content_variants_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "content_item_id",
            "variant_key",
            name="uq_content_variants_tenant_item_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_item_id"],
            [f"{SCHEMA}.content_items.tenant_id", f"{SCHEMA}.content_items.id"],
            ondelete="CASCADE",
            name="fk_content_variants_tenant_item",
        ),
        Index("ix_content_variants_tenant_item", "tenant_id", "content_item_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    variant_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title_override: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ContentPlanCreative(Base, TimestampMixin):
    __tablename__ = "content_plan_creatives"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_content_plan_creatives_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "content_plan_id",
            "file_ref",
            "role",
            name="uq_content_plan_creatives_tenant_plan_file_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_plan_id"],
            [f"{SCHEMA}.content_plans.tenant_id", f"{SCHEMA}.content_plans.id"],
            ondelete="CASCADE",
            name="fk_content_plan_creatives_tenant_plan",
        ),
        Index(
            "ix_content_plan_creatives_tenant_plan",
            "tenant_id",
            "content_plan_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    content_plan_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    file_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ContentItemCreative(Base, TimestampMixin):
    __tablename__ = "content_item_creatives"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_content_item_creatives_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "content_item_id",
            "file_ref",
            "role",
            name="uq_content_item_creatives_tenant_item_file_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "content_item_id"],
            [f"{SCHEMA}.content_items.tenant_id", f"{SCHEMA}.content_items.id"],
            ondelete="CASCADE",
            name="fk_content_item_creatives_tenant_item",
        ),
        Index(
            "ix_content_item_creatives_tenant_item",
            "tenant_id",
            "content_item_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    file_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


CONTENT_MODELS = (
    ContentPlan,
    ContentItem,
    ContentVariant,
    ContentPlanCreative,
    ContentItemCreative,
)

TENANT_TABLES: tuple[str, ...] = tuple(model.__tablename__ for model in CONTENT_MODELS)

_TABLE_BY_NAME: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__) for model in CONTENT_MODELS
}


def metadata_table(table_name: str) -> sa.Table:
    """Return one declared table for assembly and catalogue gates."""
    return _TABLE_BY_NAME[table_name]


__all__ = [
    "CONTENT_MODELS",
    "SCHEMA",
    "TENANT_TABLES",
    "ContentItem",
    "ContentItemCreative",
    "ContentPlan",
    "ContentPlanCreative",
    "ContentVariant",
    "metadata_table",
]
