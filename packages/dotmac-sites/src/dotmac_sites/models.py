"""Five-table tenant persistence contract for immutable website revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_sites.lifecycle import SiteRevisionState, SiteState

SCHEMA = module_schema("sites")


class Site(Base, TimestampMixin):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sites_tenant_id_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_sites_tenant_slug"),
        Index("ix_sites_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[SiteState] = mapped_column(
        sa.Enum(
            SiteState,
            name="site_state",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=SiteState.ACTIVE,
        server_default=SiteState.ACTIVE.value,
    )
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class Page(Base, TimestampMixin):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pages_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "site_id", "id", name="uq_pages_tenant_site_id"
        ),
        UniqueConstraint(
            "tenant_id", "site_id", "page_key", name="uq_pages_tenant_site_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            [f"{SCHEMA}.sites.tenant_id", f"{SCHEMA}.sites.id"],
            ondelete="RESTRICT",
            name="fk_pages_tenant_site",
        ),
        Index("ix_pages_tenant_site", "tenant_id", "site_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    page_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class PageRevision(Base, TimestampMixin):
    __tablename__ = "page_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_page_revisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "page_id",
            "id",
            name="uq_page_revisions_tenant_site_page_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "page_id",
            "revision_number",
            name="uq_page_revisions_tenant_page_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "page_id"],
            [
                f"{SCHEMA}.pages.tenant_id",
                f"{SCHEMA}.pages.site_id",
                f"{SCHEMA}.pages.id",
            ],
            ondelete="RESTRICT",
            name="fk_page_revisions_tenant_site_page",
        ),
        CheckConstraint(
            "revision_number > 0", name="ck_page_revisions_positive_number"
        ),
        CheckConstraint(
            "length(content_digest) = 64", name="ck_page_revisions_digest"
        ),
        Index("ix_page_revisions_tenant_page", "tenant_id", "page_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    page_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    seo_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    form_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class SiteRevision(Base, TimestampMixin):
    __tablename__ = "site_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_site_revisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "id",
            name="uq_site_revisions_tenant_site_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "revision_number",
            name="uq_site_revisions_tenant_site_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            [f"{SCHEMA}.sites.tenant_id", f"{SCHEMA}.sites.id"],
            ondelete="RESTRICT",
            name="fk_site_revisions_tenant_site",
        ),
        CheckConstraint(
            "revision_number > 0", name="ck_site_revisions_positive_number"
        ),
        CheckConstraint(
            "length(snapshot_digest) = 64", name="ck_site_revisions_digest"
        ),
        Index(
            "uq_site_revisions_one_ready",
            "tenant_id",
            "site_id",
            unique=True,
            postgresql_where=sa.text("state = 'ready'"),
            sqlite_where=sa.text("state = 'ready'"),
        ),
        Index("ix_site_revisions_tenant_site", "tenant_id", "site_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[SiteRevisionState] = mapped_column(
        sa.Enum(
            SiteRevisionState,
            name="site_revision_state",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=SiteRevisionState.DRAFT,
        server_default=SiteRevisionState.DRAFT.value,
    )
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_ref: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SiteRevisionPage(Base, TimestampMixin):
    __tablename__ = "site_revision_pages"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_site_revision_pages_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "page_id",
            name="uq_site_revision_pages_tenant_revision_page",
        ),
        UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "path",
            name="uq_site_revision_pages_tenant_revision_path",
        ),
        UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "sort_order",
            name="uq_site_revision_pages_tenant_revision_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "site_revision_id"],
            [
                f"{SCHEMA}.site_revisions.tenant_id",
                f"{SCHEMA}.site_revisions.site_id",
                f"{SCHEMA}.site_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_site_revision_pages_tenant_site_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "page_id", "page_revision_id"],
            [
                f"{SCHEMA}.page_revisions.tenant_id",
                f"{SCHEMA}.page_revisions.site_id",
                f"{SCHEMA}.page_revisions.page_id",
                f"{SCHEMA}.page_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_site_revision_pages_tenant_site_page_revision",
        ),
        CheckConstraint(
            "sort_order >= 0", name="ck_site_revision_pages_nonnegative_order"
        ),
        Index(
            "ix_site_revision_pages_tenant_revision",
            "tenant_id",
            "site_revision_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    site_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    page_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    page_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


SITES_MODELS = (Site, Page, PageRevision, SiteRevision, SiteRevisionPage)
TENANT_TABLES = tuple(model.__tablename__ for model in SITES_MODELS)


def metadata_table(name: str) -> sa.Table:
    """Return one sites-owned table from shared metadata."""
    if name not in TENANT_TABLES:
        raise KeyError(name)
    return cast(sa.Table, Base.metadata.tables[f"{SCHEMA}.{name}"])


__all__ = [
    "Page",
    "PageRevision",
    "SCHEMA",
    "SITES_MODELS",
    "Site",
    "SiteRevision",
    "SiteRevisionPage",
    "TENANT_TABLES",
    "metadata_table",
]
