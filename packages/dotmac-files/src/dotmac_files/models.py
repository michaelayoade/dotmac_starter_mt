"""Stored-byte metadata on explicit tenant and platform planes (ADR-0023)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("files")


class _StoredFileColumns:
    """Plane-independent physical metadata, declared once for both tables."""

    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    detected_media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    missing_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TenantStoredFile(Base, _StoredFileColumns, TimestampMixin):
    """Physical identity and observations for one tenant-owned object."""

    __tablename__ = "stored_files"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_stored_files_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "provider_code",
            "storage_key",
            name="uq_stored_files_tenant_provider_key",
        ),
        Index("ix_stored_files_tenant_id", "tenant_id"),
        Index("ix_stored_files_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class PlatformStoredFile(Base, _StoredFileColumns, TimestampMixin):
    """Physical identity for a control-plane object; tenant-free by design."""

    __tablename__ = "platform_stored_files"
    __table_args__ = (
        UniqueConstraint(
            "provider_code",
            "storage_key",
            name="uq_platform_stored_files_provider_key",
        ),
        Index("ix_platform_stored_files_state", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


TENANT_TABLES: tuple[str, ...] = ("stored_files",)
PLATFORM_TABLES: tuple[str, ...] = ("platform_stored_files",)

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "PlatformStoredFile",
    "TenantStoredFile",
]
