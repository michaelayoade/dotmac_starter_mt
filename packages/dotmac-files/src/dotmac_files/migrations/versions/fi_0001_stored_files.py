"""Create the explicit tenant and platform stored-file planes (ADR-0023).

Revision ID: fi_0001_stored_files
Revises: (lineage root)
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "fi_0001_stored_files"
down_revision = None
branch_labels = ("files",)
depends_on = ("0001_initial_tenant_schema",)

_SCHEMA = "mod_files"
_TABLE = "stored_files"
_PLATFORM_TABLE = "platform_stored_files"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_files;")
    op.execute("GRANT USAGE ON SCHEMA mod_files TO app_user, platform_api, app_admin;")
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_media_type", sa.String(200), nullable=False),
        sa.Column("detected_media_type", sa.String(200), nullable=False),
        sa.Column("checksum_sha256", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("missing_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_stored_files_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_stored_files_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_code",
            "storage_key",
            name="uq_stored_files_tenant_provider_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index("ix_stored_files_tenant_id", _TABLE, ["tenant_id"], schema=_SCHEMA)
    op.create_index(
        "ix_stored_files_tenant_state",
        _TABLE,
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_files.stored_files ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_files.stored_files FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY stored_files_tenant_isolation
            ON mod_files.stored_files
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_files.stored_files TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_files.stored_files TO platform_api;"
    )

    op.create_table(
        _PLATFORM_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_media_type", sa.String(200), nullable=False),
        sa.Column("detected_media_type", sa.String(200), nullable=False),
        sa.Column("checksum_sha256", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("missing_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider_code",
            "storage_key",
            name="uq_platform_stored_files_provider_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_stored_files_state",
        _PLATFORM_TABLE,
        ["state"],
        schema=_SCHEMA,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_files.platform_stored_files TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_files.platform_stored_files TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_files.platform_stored_files FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_files.platform_stored_files CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_files.stored_files CASCADE;")
