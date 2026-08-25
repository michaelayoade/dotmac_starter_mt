"""Create the tenant-controlled Documents owner.

Revision ID: dc_0001_documents
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

revision = "dc_0001_documents"
down_revision = None
branch_labels = ("documents",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_documents"


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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_documents;")
    op.execute("GRANT USAGE ON SCHEMA mod_documents TO app_user, app_admin;")

    op.create_table(
        "document_libraries",
        *_identity(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_libraries"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_document_libraries_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_libraries_tenant_id",
        "document_libraries",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_type_versions",
        *_identity(),
        sa.Column("type_code", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_schema", postgresql.JSONB(), nullable=False),
        sa.Column("required_fields", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_transitions", postgresql.JSONB(), nullable=False),
        sa.Column("approval_required_states", postgresql.JSONB(), nullable=False),
        sa.Column("major_minor", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_type_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "type_code",
            "version",
            name="uq_document_type_versions_code_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_document_type_versions_positive"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_type_versions_tenant_id",
        "document_type_versions",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "documents",
        *_identity(),
        sa.Column("library_id", sa.Uuid(), nullable=False),
        sa.Column("type_code", sa.String(80), nullable=False),
        sa.Column("type_version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("folder_path", sa.String(1000), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("sensitivity", sa.String(80), nullable=False),
        sa.Column("handling_instructions", postgresql.JSONB(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("documents"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "library_id"],
            [
                "mod_documents.document_libraries.tenant_id",
                "mod_documents.document_libraries.id",
            ],
            ondelete="RESTRICT",
            name="fk_documents_library",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "type_code", "type_version"],
            [
                "mod_documents.document_type_versions.tenant_id",
                "mod_documents.document_type_versions.type_code",
                "mod_documents.document_type_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_documents_type_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "library_id", "code", name="uq_documents_library_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_documents_tenant_id", "documents", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_documents_tenant_state", "documents", ["tenant_id", "state"], schema=_SCHEMA
    )

    op.create_table(
        "document_versions",
        *_identity(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("major_number", sa.Integer(), nullable=False),
        sa.Column("minor_number", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("checksum_sha256", sa.String(71), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("authored_by", sa.Uuid(), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="RESTRICT",
            name="fk_document_versions_document",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "ordinal",
            name="uq_document_versions_document_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "id",
            name="uq_document_versions_tenant_document_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "major_number",
            "minor_number",
            name="uq_document_versions_semantic_version",
        ),
        sa.CheckConstraint(
            "ordinal > 0 AND major_number > 0 AND minor_number >= 0",
            name="ck_document_versions_numbers",
        ),
        sa.CheckConstraint("byte_length >= 0", name="ck_document_versions_byte_length"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_versions_tenant_id",
        "document_versions",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_versions_document",
        "document_versions",
        ["tenant_id", "document_id", "ordinal"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["tenant_id", "id", "current_version_id"],
        ["tenant_id", "document_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "document_renditions",
        *_identity(),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("source_checksum_sha256", sa.String(71), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("output_checksum_sha256", sa.String(71), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("renderer_code", sa.String(120), nullable=False),
        sa.Column("renderer_version", sa.String(120), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_renditions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_version_id"],
            [
                "mod_documents.document_versions.tenant_id",
                "mod_documents.document_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_document_renditions_source_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_version_id",
            "kind",
            "renderer_code",
            "renderer_version",
            "output_checksum_sha256",
            name="uq_document_renditions_exact_output",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_renditions_tenant_id",
        "document_renditions",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_classifications",
        *_identity(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_code", sa.String(120), nullable=False),
        sa.Column("value_code", sa.String(120), nullable=False),
        sa.Column("hierarchy_path", sa.String(1000), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_classifications"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="CASCADE",
            name="fk_document_classifications_document",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "taxonomy_code",
            "value_code",
            name="uq_document_classifications_value",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_classifications_tenant_id",
        "document_classifications",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_relations",
        *_identity(),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("target_document_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(40), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_relations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="CASCADE",
            name="fk_document_relations_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="CASCADE",
            name="fk_document_relations_target",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_edge",
        ),
        sa.CheckConstraint(
            "source_document_id <> target_document_id",
            name="ck_document_relations_not_self",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_relations_tenant_id",
        "document_relations",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_checkouts",
        *_identity(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("release_reason", sa.String(500), nullable=True),
        sa.Column("break_glass", sa.Boolean(), nullable=False),
        *_tenant_constraints("document_checkouts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="CASCADE",
            name="fk_document_checkouts_document",
        ),
        sa.CheckConstraint(
            "expires_at > acquired_at", name="ck_document_checkouts_expiry"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_checkouts_tenant_id",
        "document_checkouts",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_document_checkouts_active",
        "document_checkouts",
        ["tenant_id", "document_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "document_annotations",
        *_identity(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("principal_ref", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("anchor", postgresql.JSONB(), nullable=False),
        sa.Column("finding_code", sa.String(120), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        *_tenant_constraints("document_annotations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [
                "mod_documents.document_versions.tenant_id",
                "mod_documents.document_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_document_annotations_version",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_annotations_tenant_id",
        "document_annotations",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_access_grants",
        *_identity(),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_ref", sa.String(1000), nullable=False),
        sa.Column("principal_kind", sa.String(32), nullable=False),
        sa.Column("principal_ref", sa.String(255), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("inherits", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_access_grants"),
        sa.UniqueConstraint(
            "tenant_id",
            "target_kind",
            "target_ref",
            "principal_kind",
            "principal_ref",
            "effect",
            name="uq_document_access_grants_rule",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_access_grants_tenant_id",
        "document_access_grants",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_acknowledgements",
        *_identity(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("principal_ref", sa.String(255), nullable=False),
        sa.Column("attestation_text", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_acknowledgements"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [
                "mod_documents.document_versions.tenant_id",
                "mod_documents.document_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_document_acknowledgements_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "principal_ref",
            name="uq_document_acknowledgements_principal_version",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_acknowledgements_tenant_id",
        "document_acknowledgements",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "document_events",
        *_identity(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("document_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["mod_documents.documents.tenant_id", "mod_documents.documents.id"],
            ondelete="RESTRICT",
            name="fk_document_events_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id", "version_id"],
            [
                "mod_documents.document_versions.tenant_id",
                "mod_documents.document_versions.document_id",
                "mod_documents.document_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_document_events_version",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_document_events_tenant_id", "document_events", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_document_events_timeline",
        "document_events",
        ["tenant_id", "document_id", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_documents.protect_document_version() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'document versions and definitions are immutable'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER document_type_versions_immutable BEFORE UPDATE OR DELETE ON mod_documents.document_type_versions FOR EACH ROW EXECUTE FUNCTION mod_documents.protect_document_version();"
    )
    op.execute(
        "CREATE TRIGGER document_versions_immutable BEFORE UPDATE OR DELETE ON mod_documents.document_versions FOR EACH ROW EXECUTE FUNCTION mod_documents.protect_document_version();"
    )
    op.execute(
        "CREATE TRIGGER document_renditions_immutable BEFORE UPDATE OR DELETE ON mod_documents.document_renditions FOR EACH ROW EXECUTE FUNCTION mod_documents.protect_document_version();"
    )
    op.execute(
        "CREATE TRIGGER document_acknowledgements_immutable BEFORE UPDATE OR DELETE ON mod_documents.document_acknowledgements FOR EACH ROW EXECUTE FUNCTION mod_documents.protect_document_version();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_documents.protect_document_event() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'document events are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER document_events_append_only BEFORE UPDATE OR DELETE ON mod_documents.document_events FOR EACH ROW EXECUTE FUNCTION mod_documents.protect_document_event();"
    )

    op.execute(
        "ALTER TABLE mod_documents.document_libraries ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_documents.document_libraries FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY document_libraries_tenant_isolation ON mod_documents.document_libraries USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_libraries TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_type_versions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_type_versions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_type_versions_tenant_isolation ON mod_documents.document_type_versions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_type_versions TO app_user;"
    )
    op.execute("ALTER TABLE mod_documents.documents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_documents.documents FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY documents_tenant_isolation ON mod_documents.documents USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.documents TO app_user;"
    )
    op.execute("ALTER TABLE mod_documents.document_versions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_documents.document_versions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY document_versions_tenant_isolation ON mod_documents.document_versions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_versions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_renditions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_renditions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_renditions_tenant_isolation ON mod_documents.document_renditions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_renditions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_classifications ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_classifications FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_classifications_tenant_isolation ON mod_documents.document_classifications USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_classifications TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_relations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_documents.document_relations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY document_relations_tenant_isolation ON mod_documents.document_relations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_relations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_checkouts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_documents.document_checkouts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY document_checkouts_tenant_isolation ON mod_documents.document_checkouts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_checkouts TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_annotations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_annotations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_annotations_tenant_isolation ON mod_documents.document_annotations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_annotations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_access_grants ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_access_grants FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_access_grants_tenant_isolation ON mod_documents.document_access_grants USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_access_grants TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_acknowledgements ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_documents.document_acknowledgements FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY document_acknowledgements_tenant_isolation ON mod_documents.document_acknowledgements USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_acknowledgements TO app_user;"
    )
    op.execute("ALTER TABLE mod_documents.document_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_documents.document_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY document_events_tenant_isolation ON mod_documents.document_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_documents.document_events TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_documents CASCADE;")
