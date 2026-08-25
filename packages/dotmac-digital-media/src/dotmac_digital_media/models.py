"""Tenant-scoped Digital Media persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("digitalmedia")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_fk() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class MediaLibrary(Base, TimestampMixin):
    __tablename__ = "media_libraries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_libraries_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_media_libraries_tenant_code"),
        Index("ix_media_libraries_tenant", "tenant_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_assets_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_assets_library",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.asset_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_assets_current_revision",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_rights_version_id"],
            [
                "mod_digitalmedia.media_rights_versions.tenant_id",
                "mod_digitalmedia.media_rights_versions.asset_id",
                "mod_digitalmedia.media_rights_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_assets_current_rights",
            use_alter=True,
        ),
        CheckConstraint(
            "lifecycle IN ('ingesting','quarantined','available','restricted',"
            "'expired','withdrawn','archived')",
            name="ck_media_assets_lifecycle",
        ),
        CheckConstraint(
            "kind IN ('image','video','audio','rich_media')",
            name="ck_media_assets_kind",
        ),
        Index("ix_media_assets_tenant_library", "tenant_id", "library_id"),
        Index("ix_media_assets_tenant_lifecycle", "tenant_id", "lifecycle"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    library_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_alt_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    creator_credit: Mapped[str | None] = mapped_column(String(300), nullable=True)
    photographer_credit: Mapped[str | None] = mapped_column(String(300), nullable=True)
    producer_credit: Mapped[str | None] = mapped_column(String(300), nullable=True)
    contributor_credits: Mapped[list[str]] = mapped_column(
        _JSON, nullable=False, default=list
    )
    capture_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supplied_location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lifecycle: Mapped[str] = mapped_column(
        String(24), nullable=False, default="ingesting"
    )
    current_revision_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    current_rights_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(), nullable=True
    )


class MediaRevision(Base, TimestampMixin):
    __tablename__ = "media_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_revisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "asset_id",
            "id",
            name="uq_media_revisions_tenant_asset_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "asset_id",
            "revision_number",
            name="uq_media_revisions_asset_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_revisions_asset",
        ),
        CheckConstraint("revision_number > 0", name="ck_media_revisions_number"),
        CheckConstraint("byte_length >= 0", name="ck_media_revisions_byte_length"),
        CheckConstraint(
            "source_kind IN ('upload','scan','import','api','generated','migration')",
            name="ck_media_revisions_source_kind",
        ),
        Index("ix_media_revisions_asset", "tenant_id", "asset_id"),
        Index("ix_media_revisions_checksum", "tenant_id", "checksum"),
        Index(
            "ix_media_revisions_perceptual",
            "tenant_id",
            "perceptual_hash_algorithm",
            "perceptual_hash",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    perceptual_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    perceptual_hash_algorithm: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )


class MediaMetadataObservation(Base, TimestampMixin):
    __tablename__ = "media_metadata_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_media_metadata_observations_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_metadata_observations_revision",
        ),
        Index("ix_media_metadata_observations_revision", "tenant_id", "revision_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    extractor_code: Mapped[str] = mapped_column(String(120), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    bitrate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(120), nullable=True)
    colour_profile: Mapped[str | None] = mapped_column(String(200), nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    accessibility: Mapped[dict[str, Any]] = mapped_column(
        _JSON, nullable=False, default=dict
    )
    exif: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    iptc: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    xmp: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)


class MediaCollection(Base, TimestampMixin):
    __tablename__ = "media_collections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_collections_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "library_id", "code", name="uq_media_collections_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collections_library",
        ),
        Index("ix_media_collections_library", "tenant_id", "library_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    library_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="collection"
    )


class MediaCollectionItem(Base, TimestampMixin):
    __tablename__ = "media_collection_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_media_collection_items_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "collection_id",
            "asset_id",
            name="uq_media_collection_items_asset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            [
                "mod_digitalmedia.media_collections.tenant_id",
                "mod_digitalmedia.media_collections.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collection_items_collection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collection_items_asset",
        ),
        CheckConstraint("sort_order >= 0", name="ck_media_collection_items_order"),
        Index("ix_media_collection_items_collection", "tenant_id", "collection_id"),
        Index(
            "uq_media_collection_items_default",
            "tenant_id",
            "collection_id",
            unique=True,
            postgresql_where=sa.text("is_default"),
        ).ddl_if(dialect="postgresql"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    collection_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MediaClassificationAssignment(Base, TimestampMixin):
    __tablename__ = "media_classification_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_media_classification_assignments_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_classification_assignments_asset",
        ),
        UniqueConstraint(
            "tenant_id",
            "asset_id",
            "assignment_kind",
            "vocabulary_ref",
            "code",
            "source_ref",
            name="uq_media_classification_assignment",
        ),
        CheckConstraint(
            "assignment_kind IN ('classification','tag','association')",
            name="ck_media_classification_assignment_kind",
        ),
        Index("ix_media_classification_asset", "tenant_id", "asset_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assignment_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    vocabulary_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(255), nullable=False)
    hierarchy_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False, default="")


class MediaRelationship(Base, TimestampMixin):
    __tablename__ = "media_relationships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_relationships_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "from_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_relationships_from_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_relationships_to_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "from_revision_id",
            "to_revision_id",
            "relation",
            name="uq_media_relationships_edge",
        ),
        CheckConstraint(
            "relation IN ('derived_from','parent_of',"
            "'alternate_language_of','related')",
            name="ck_media_relationships_relation",
        ),
        Index("ix_media_relationships_from", "tenant_id", "from_revision_id"),
        Index("ix_media_relationships_to", "tenant_id", "to_revision_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    from_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    to_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    relation: Mapped[str] = mapped_column(String(40), nullable=False)
    language_code: Mapped[str | None] = mapped_column(String(24), nullable=True)


class MediaRightsVersion(Base, TimestampMixin):
    __tablename__ = "media_rights_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_media_rights_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "asset_id",
            "id",
            name="uq_media_rights_versions_tenant_asset_id",
        ),
        UniqueConstraint(
            "tenant_id", "asset_id", "version_number", name="uq_media_rights_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_rights_versions_asset",
        ),
        CheckConstraint("version_number > 0", name="ck_media_rights_version_number"),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at < ends_at",
            name="ck_media_rights_window",
        ),
        Index("ix_media_rights_asset", "tenant_id", "asset_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    asset_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rights_holder: Mapped[str] = mapped_column(String(500), nullable=False)
    copyright_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    licence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    licence_version: Mapped[str] = mapped_column(String(120), nullable=False)
    territories: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    channels: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    purposes: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_credit: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    modification_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    release_references: Mapped[list[str]] = mapped_column(
        _JSON, nullable=False, default=list
    )
    release_evidence_ref: Mapped[str | None] = mapped_column(String(500))
    release_evidence_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensitivity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    embargo_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaRendition(Base, TimestampMixin):
    __tablename__ = "media_renditions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_renditions_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "source_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_renditions_source_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_revision_id",
            "kind",
            "recipe_code",
            "recipe_version",
            "engine_code",
            "engine_version",
            "request_fingerprint",
            name="uq_media_renditions_recipe_request",
        ),
        CheckConstraint(
            "state IN ('requested','ready','failed')",
            name="ck_media_renditions_state",
        ),
        CheckConstraint("output_byte_length >= 0", name="ck_media_renditions_length"),
        CheckConstraint("attempt_number > 0", name="ck_media_renditions_attempt"),
        Index("ix_media_renditions_source", "tenant_id", "source_revision_id"),
        Index("ix_media_renditions_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    source_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    recipe_code: Mapped[str] = mapped_column(String(120), nullable=False)
    recipe_version: Mapped[str] = mapped_column(String(80), nullable=False)
    engine_code: Mapped[str] = mapped_column(String(120), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        _JSON, nullable=False, default=dict
    )
    focal_point: Mapped[dict[str, float]] = mapped_column(
        _JSON, nullable=False, default=dict
    )
    requested_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    output_file_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    output_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_byte_length: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    output_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_codec: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)


class MediaAccessGrant(Base, TimestampMixin):
    __tablename__ = "media_access_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_access_grants_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_library",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            [
                "mod_digitalmedia.media_collections.tenant_id",
                "mod_digitalmedia.media_collections.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_collection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_asset",
        ),
        CheckConstraint(
            "((library_id IS NOT NULL)::int + (collection_id IS NOT NULL)::int + "
            "(asset_id IS NOT NULL)::int) = 1",
            name="ck_media_access_grants_one_scope",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "effect IN ('allow','deny')", name="ck_media_access_grants_effect"
        ),
        Index(
            "uq_media_access_grants_library",
            "tenant_id",
            "library_id",
            "principal_ref",
            "permission",
            unique=True,
            postgresql_where=sa.text("library_id IS NOT NULL"),
        ),
        Index(
            "uq_media_access_grants_collection",
            "tenant_id",
            "collection_id",
            "principal_ref",
            "permission",
            unique=True,
            postgresql_where=sa.text("collection_id IS NOT NULL"),
        ),
        Index(
            "uq_media_access_grants_asset",
            "tenant_id",
            "asset_id",
            "principal_ref",
            "permission",
            unique=True,
            postgresql_where=sa.text("asset_id IS NOT NULL"),
        ),
        Index("ix_media_access_grants_principal", "tenant_id", "principal_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    library_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    collection_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    asset_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    principal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    principal_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    permission: Mapped[str] = mapped_column(String(40), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaAnnotation(Base, TimestampMixin):
    __tablename__ = "media_annotations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_annotations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_annotations_revision",
        ),
        CheckConstraint(
            "status IN ('open','resolved','dismissed')",
            name="ck_media_annotations_status",
        ),
        Index("ix_media_annotations_revision", "tenant_id", "revision_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    author_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    anchor: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_ref: Mapped[str | None] = mapped_column(String(255))


class MediaSavedSelection(Base, TimestampMixin):
    __tablename__ = "media_saved_selections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_media_saved_selections_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_saved_selections_library",
        ),
        UniqueConstraint(
            "tenant_id", "owner_ref", "name", name="uq_media_saved_selection_name"
        ),
        Index("ix_media_saved_selections_owner", "tenant_id", "owner_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    library_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    owner_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(
        _JSON, nullable=False, default=dict
    )


class MediaUsageObservation(Base, TimestampMixin):
    __tablename__ = "media_usage_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_media_usage_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_media_usage_observations_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_usage_observations_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rendition_id"],
            [
                "mod_digitalmedia.media_renditions.tenant_id",
                "mod_digitalmedia.media_renditions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_usage_observations_rendition",
        ),
        Index("ix_media_usage_revision", "tenant_id", "revision_id"),
        Index(
            "ix_media_usage_source",
            "tenant_id",
            "source_owner",
            "source_type",
            "source_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(120), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    rendition_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MediaEvent(Base, TimestampMixin):
    __tablename__ = "media_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "event_identity", name="uq_media_events_identity"
        ),
        Index(
            "ix_media_events_aggregate",
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            "occurred_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    event_identity: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False, default=dict)


ALL_MODELS = (
    MediaLibrary,
    MediaAsset,
    MediaRevision,
    MediaMetadataObservation,
    MediaCollection,
    MediaCollectionItem,
    MediaClassificationAssignment,
    MediaRelationship,
    MediaRightsVersion,
    MediaRendition,
    MediaAccessGrant,
    MediaAnnotation,
    MediaSavedSelection,
    MediaUsageObservation,
    MediaEvent,
)

TABLES: tuple[str, ...] = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "SCHEMA",
    "TABLES",
    "MediaAccessGrant",
    "MediaAnnotation",
    "MediaAsset",
    "MediaClassificationAssignment",
    "MediaCollection",
    "MediaCollectionItem",
    "MediaEvent",
    "MediaLibrary",
    "MediaMetadataObservation",
    "MediaRelationship",
    "MediaRendition",
    "MediaRevision",
    "MediaRightsVersion",
    "MediaSavedSelection",
    "MediaUsageObservation",
]
