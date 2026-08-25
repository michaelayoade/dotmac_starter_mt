"""Create the tenant-only Digital Media owner.

Revision ID: dm_0001_digital_media
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "dm_0001_digital_media"
down_revision = None
branch_labels = ("digital_media",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_digitalmedia"


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _timestamps() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_digitalmedia;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_digitalmedia "
        "TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "media_libraries",
        *_identity(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_libraries"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_media_libraries_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_libraries_tenant",
        "media_libraries",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_assets",
        *_identity(),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_alt_text", sa.String(1000), nullable=True),
        sa.Column("creator_credit", sa.String(300), nullable=True),
        sa.Column("photographer_credit", sa.String(300), nullable=True),
        sa.Column("producer_credit", sa.String(300), nullable=True),
        sa.Column(
            "contributor_credits",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("capture_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supplied_location", sa.String(500), nullable=True),
        sa.Column("sensitivity", sa.String(80), nullable=True),
        sa.Column(
            "lifecycle", sa.String(24), nullable=False, server_default="ingesting"
        ),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("current_rights_version_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_assets"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_assets_library",
        ),
        sa.CheckConstraint(
            "kind IN ('image','video','audio','rich_media')",
            name="ck_media_assets_kind",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('ingesting','quarantined','available','restricted',"
            "'expired','withdrawn','archived')",
            name="ck_media_assets_lifecycle",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_assets_tenant_library",
        "media_assets",
        ["tenant_id", "library_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_assets_tenant_lifecycle",
        "media_assets",
        ["tenant_id", "lifecycle"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_revisions",
        *_identity(),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=True),
        sa.Column("author_ref", sa.String(255), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.Column("perceptual_hash", sa.String(256), nullable=True),
        sa.Column("perceptual_hash_algorithm", sa.String(80), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_revisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_revisions_asset",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "revision_number",
            name="uq_media_revisions_asset_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "id",
            name="uq_media_revisions_tenant_asset_id",
        ),
        sa.CheckConstraint("revision_number > 0", name="ck_media_revisions_number"),
        sa.CheckConstraint("byte_length >= 0", name="ck_media_revisions_byte_length"),
        sa.CheckConstraint(
            "source_kind IN ('upload','scan','import','api','generated','migration')",
            name="ck_media_revisions_source_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_revisions_asset",
        "media_revisions",
        ["tenant_id", "asset_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_revisions_checksum",
        "media_revisions",
        ["tenant_id", "checksum"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_revisions_perceptual",
        "media_revisions",
        ["tenant_id", "perceptual_hash_algorithm", "perceptual_hash"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_media_assets_current_revision",
        "media_assets",
        "media_revisions",
        ["tenant_id", "id", "current_revision_id"],
        ["tenant_id", "asset_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "media_metadata_observations",
        *_identity(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_checksum", sa.String(128), nullable=False),
        sa.Column("extractor_code", sa.String(120), nullable=False),
        sa.Column("extractor_version", sa.String(80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("frame_rate", sa.Float(), nullable=True),
        sa.Column("bitrate", sa.BigInteger(), nullable=True),
        sa.Column("codec", sa.String(120), nullable=True),
        sa.Column("colour_profile", sa.String(200), nullable=True),
        sa.Column("orientation", sa.String(80), nullable=True),
        sa.Column(
            "accessibility",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "exif",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "iptc",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "xmp",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_timestamps(),
        *_tenant_constraints("media_metadata_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_metadata_observations_revision",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_metadata_observations_revision",
        "media_metadata_observations",
        ["tenant_id", "revision_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_collections",
        *_identity(),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "selection_kind",
            sa.String(24),
            nullable=False,
            server_default="collection",
        ),
        *_timestamps(),
        *_tenant_constraints("media_collections"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collections_library",
        ),
        sa.UniqueConstraint(
            "tenant_id", "library_id", "code", name="uq_media_collections_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_collections_library",
        "media_collections",
        ["tenant_id", "library_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_collection_items",
        *_identity(),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_featured", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        *_timestamps(),
        *_tenant_constraints("media_collection_items"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            [
                "mod_digitalmedia.media_collections.tenant_id",
                "mod_digitalmedia.media_collections.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collection_items_collection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_collection_items_asset",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "collection_id",
            "asset_id",
            name="uq_media_collection_items_asset",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_media_collection_items_order"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_collection_items_collection",
        "media_collection_items",
        ["tenant_id", "collection_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_media_collection_items_default",
        "media_collection_items",
        ["tenant_id", "collection_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "media_classification_assignments",
        *_identity(),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_kind", sa.String(24), nullable=False),
        sa.Column("vocabulary_ref", sa.String(255), nullable=False),
        sa.Column("code", sa.String(255), nullable=False),
        sa.Column("hierarchy_path", sa.String(1000), nullable=True),
        sa.Column("source_owner", sa.String(120), nullable=True),
        sa.Column("source_type", sa.String(120), nullable=True),
        sa.Column("source_ref", sa.String(500), nullable=False, server_default=""),
        *_timestamps(),
        *_tenant_constraints("media_classification_assignments"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_classification_assignments_asset",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "assignment_kind",
            "vocabulary_ref",
            "code",
            "source_ref",
            name="uq_media_classification_assignment",
        ),
        sa.CheckConstraint(
            "assignment_kind IN ('classification','tag','association')",
            name="ck_media_classification_assignment_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_classification_asset",
        "media_classification_assignments",
        ["tenant_id", "asset_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_relationships",
        *_identity(),
        sa.Column("from_revision_id", sa.Uuid(), nullable=False),
        sa.Column("to_revision_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(40), nullable=False),
        sa.Column("language_code", sa.String(24), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_relationships"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "from_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_relationships_from_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "to_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_relationships_to_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "from_revision_id",
            "to_revision_id",
            "relation",
            name="uq_media_relationships_edge",
        ),
        sa.CheckConstraint(
            "relation IN ('derived_from','parent_of','alternate_language_of','related')",
            name="ck_media_relationships_relation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_relationships_from",
        "media_relationships",
        ["tenant_id", "from_revision_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_relationships_to",
        "media_relationships",
        ["tenant_id", "to_revision_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_rights_versions",
        *_identity(),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("rights_holder", sa.String(500), nullable=False),
        sa.Column("copyright_notice", sa.Text(), nullable=True),
        sa.Column("licence_id", sa.String(255), nullable=False),
        sa.Column("licence_version", sa.String(120), nullable=False),
        sa.Column(
            "territories",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "purposes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required_credit", sa.String(1000), nullable=True),
        sa.Column("commercial_use_allowed", sa.Boolean(), nullable=False),
        sa.Column("modification_allowed", sa.Boolean(), nullable=False),
        sa.Column(
            "release_references",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("release_evidence_ref", sa.String(500), nullable=True),
        sa.Column("release_evidence_valid", sa.Boolean(), nullable=False),
        sa.Column("sensitivity", sa.String(80), nullable=True),
        sa.Column("embargo_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_rights_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_rights_versions_asset",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "version_number",
            name="uq_media_rights_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "id",
            name="uq_media_rights_versions_tenant_asset_id",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_media_rights_version_number"),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at < ends_at",
            name="ck_media_rights_window",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_rights_asset",
        "media_rights_versions",
        ["tenant_id", "asset_id"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_media_assets_current_rights",
        "media_assets",
        "media_rights_versions",
        ["tenant_id", "id", "current_rights_version_id"],
        ["tenant_id", "asset_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "media_renditions",
        *_identity(),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_checksum", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("recipe_code", sa.String(120), nullable=False),
        sa.Column("recipe_version", sa.String(80), nullable=False),
        sa.Column("engine_code", sa.String(120), nullable=False),
        sa.Column("engine_version", sa.String(80), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "focal_point",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("requested_width", sa.Integer(), nullable=True),
        sa.Column("requested_height", sa.Integer(), nullable=True),
        sa.Column("output_media_type", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(24), nullable=False, server_default="requested"),
        sa.Column("output_file_id", sa.Uuid(), nullable=True),
        sa.Column("output_checksum", sa.String(128), nullable=True),
        sa.Column(
            "output_byte_length", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("output_width", sa.Integer(), nullable=True),
        sa.Column("output_height", sa.Integer(), nullable=True),
        sa.Column("output_duration_seconds", sa.Float(), nullable=True),
        sa.Column("output_codec", sa.String(120), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(120), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_renditions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_renditions_source_revision",
        ),
        sa.UniqueConstraint(
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
        sa.CheckConstraint(
            "state IN ('requested','ready','failed')",
            name="ck_media_renditions_state",
        ),
        sa.CheckConstraint(
            "output_byte_length >= 0", name="ck_media_renditions_length"
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_media_renditions_attempt"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_renditions_source",
        "media_renditions",
        ["tenant_id", "source_revision_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_renditions_state",
        "media_renditions",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_access_grants",
        *_identity(),
        sa.Column("library_id", sa.Uuid(), nullable=True),
        sa.Column("collection_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("principal_type", sa.String(40), nullable=False),
        sa.Column("principal_ref", sa.String(500), nullable=False),
        sa.Column("permission", sa.String(40), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_access_grants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_library",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            [
                "mod_digitalmedia.media_collections.tenant_id",
                "mod_digitalmedia.media_collections.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_collection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            [
                "mod_digitalmedia.media_assets.tenant_id",
                "mod_digitalmedia.media_assets.id",
            ],
            ondelete="CASCADE",
            name="fk_media_access_grants_asset",
        ),
        sa.CheckConstraint(
            "(CASE WHEN library_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN collection_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN asset_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_media_access_grants_one_scope",
        ),
        sa.CheckConstraint(
            "effect IN ('allow','deny')", name="ck_media_access_grants_effect"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_access_grants_principal",
        "media_access_grants",
        ["tenant_id", "principal_ref"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_media_access_grants_library",
        "media_access_grants",
        ["tenant_id", "library_id", "principal_ref", "permission"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("library_id IS NOT NULL"),
    )
    op.create_index(
        "uq_media_access_grants_collection",
        "media_access_grants",
        ["tenant_id", "collection_id", "principal_ref", "permission"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("collection_id IS NOT NULL"),
    )
    op.create_index(
        "uq_media_access_grants_asset",
        "media_access_grants",
        ["tenant_id", "asset_id", "principal_ref", "permission"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("asset_id IS NOT NULL"),
    )

    op.create_table(
        "media_annotations",
        *_identity(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("author_ref", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "anchor",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_ref", sa.String(255), nullable=True),
        *_timestamps(),
        *_tenant_constraints("media_annotations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_media_annotations_revision",
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','dismissed')",
            name="ck_media_annotations_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_annotations_revision",
        "media_annotations",
        ["tenant_id", "revision_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_saved_selections",
        *_identity(),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("owner_ref", sa.String(255), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column(
            "criteria",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_timestamps(),
        *_tenant_constraints("media_saved_selections"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_digitalmedia.media_libraries.tenant_id",
                "mod_digitalmedia.media_libraries.id",
            ],
            ondelete="CASCADE",
            name="fk_media_saved_selections_library",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_ref",
            "name",
            name="uq_media_saved_selection_name",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_saved_selections_owner",
        "media_saved_selections",
        ["tenant_id", "owner_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_usage_observations",
        *_identity(),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(120), nullable=False),
        sa.Column("source_id", sa.String(500), nullable=False),
        sa.Column("source_version", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(120), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("rendition_id", sa.Uuid(), nullable=True),
        sa.Column("source_event_id", sa.String(500), nullable=False),
        sa.Column("source_fingerprint", sa.String(128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        *_tenant_constraints("media_usage_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_digitalmedia.media_revisions.tenant_id",
                "mod_digitalmedia.media_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_usage_observations_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rendition_id"],
            [
                "mod_digitalmedia.media_renditions.tenant_id",
                "mod_digitalmedia.media_renditions.id",
            ],
            ondelete="RESTRICT",
            name="fk_media_usage_observations_rendition",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_media_usage_observations_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_usage_revision",
        "media_usage_observations",
        ["tenant_id", "revision_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_usage_source",
        "media_usage_observations",
        ["tenant_id", "source_owner", "source_type", "source_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "media_events",
        *_identity(),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("event_identity", sa.String(500), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_timestamps(),
        *_tenant_constraints("media_events"),
        sa.UniqueConstraint(
            "tenant_id", "event_identity", name="uq_media_events_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_events_aggregate",
        "media_events",
        ["tenant_id", "aggregate_type", "aggregate_id", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_digitalmedia.raise_immutable_digital_media_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' is immutable evidence';
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER media_revisions_immutable BEFORE UPDATE OR DELETE "
        "ON mod_digitalmedia.media_revisions FOR EACH ROW EXECUTE FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence();"
    )
    op.execute(
        "CREATE TRIGGER media_metadata_observations_immutable "
        "BEFORE UPDATE OR DELETE ON mod_digitalmedia.media_metadata_observations "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence();"
    )
    op.execute(
        "CREATE TRIGGER media_rights_versions_immutable BEFORE UPDATE OR DELETE "
        "ON mod_digitalmedia.media_rights_versions FOR EACH ROW EXECUTE FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence();"
    )
    op.execute(
        "CREATE TRIGGER media_usage_observations_immutable "
        "BEFORE UPDATE OR DELETE ON mod_digitalmedia.media_usage_observations "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence();"
    )
    op.execute(
        "CREATE TRIGGER media_events_immutable BEFORE UPDATE OR DELETE "
        "ON mod_digitalmedia.media_events FOR EACH ROW EXECUTE FUNCTION "
        "mod_digitalmedia.raise_immutable_digital_media_evidence();"
    )

    op.execute(
        "ALTER TABLE mod_digitalmedia.media_libraries ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_digitalmedia.media_libraries FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY media_libraries_tenant_isolation ON "
        "mod_digitalmedia.media_libraries USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_libraries TO app_user;"
    )
    op.execute("ALTER TABLE mod_digitalmedia.media_assets ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_digitalmedia.media_assets FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY media_assets_tenant_isolation ON "
        "mod_digitalmedia.media_assets USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_assets TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_revisions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_digitalmedia.media_revisions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY media_revisions_tenant_isolation ON "
        "mod_digitalmedia.media_revisions USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_digitalmedia.media_revisions TO app_user;")
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_metadata_observations "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_metadata_observations "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_metadata_observations_tenant_isolation ON "
        "mod_digitalmedia.media_metadata_observations USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_digitalmedia.media_metadata_observations "
        "TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_collections ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_collections FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_collections_tenant_isolation ON "
        "mod_digitalmedia.media_collections USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_collections TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_collection_items "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_collection_items "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_collection_items_tenant_isolation ON "
        "mod_digitalmedia.media_collection_items USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_collection_items TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_classification_assignments "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_classification_assignments "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_classification_assignments_tenant_isolation ON "
        "mod_digitalmedia.media_classification_assignments USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_classification_assignments TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_relationships " "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_relationships " "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_relationships_tenant_isolation ON "
        "mod_digitalmedia.media_relationships USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_relationships TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_rights_versions "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_rights_versions "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_rights_versions_tenant_isolation ON "
        "mod_digitalmedia.media_rights_versions USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_digitalmedia.media_rights_versions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_renditions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_renditions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_renditions_tenant_isolation ON "
        "mod_digitalmedia.media_renditions USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_renditions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_access_grants " "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_access_grants " "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_access_grants_tenant_isolation ON "
        "mod_digitalmedia.media_access_grants USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_access_grants TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_annotations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_annotations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_annotations_tenant_isolation ON "
        "mod_digitalmedia.media_annotations USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_annotations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_saved_selections "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_saved_selections "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_saved_selections_tenant_isolation ON "
        "mod_digitalmedia.media_saved_selections USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_digitalmedia.media_saved_selections TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_usage_observations "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_digitalmedia.media_usage_observations "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY media_usage_observations_tenant_isolation ON "
        "mod_digitalmedia.media_usage_observations USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_digitalmedia.media_usage_observations "
        "TO app_user;"
    )
    op.execute("ALTER TABLE mod_digitalmedia.media_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_digitalmedia.media_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY media_events_tenant_isolation ON "
        "mod_digitalmedia.media_events USING "
        "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
        "(tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_digitalmedia.media_events TO app_user;")


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "mod_digitalmedia.raise_immutable_digital_media_evidence() CASCADE;"
    )
    for table in (
        "media_events",
        "media_usage_observations",
        "media_saved_selections",
        "media_annotations",
        "media_access_grants",
        "media_renditions",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.drop_constraint(
        "fk_media_assets_current_rights",
        "media_assets",
        type_="foreignkey",
        schema=_SCHEMA,
    )
    for table in (
        "media_rights_versions",
        "media_relationships",
        "media_classification_assignments",
        "media_collection_items",
        "media_collections",
        "media_metadata_observations",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.drop_constraint(
        "fk_media_assets_current_revision",
        "media_assets",
        type_="foreignkey",
        schema=_SCHEMA,
    )
    for table in ("media_revisions", "media_assets", "media_libraries"):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_digitalmedia;")
