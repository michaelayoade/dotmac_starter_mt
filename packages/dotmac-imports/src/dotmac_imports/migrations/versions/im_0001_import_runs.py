"""Create the tenant-scoped import run ledger (ADR-0025).

Revision ID: im_0001_import_runs
Revises: (lineage root)
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "im_0001_import_runs"
down_revision = None
branch_labels = ("imports",)
# This lineage needs a tenant catalogue to point its foreign keys at and roles to
# grant to — NOT the kernel's identity, RBAC or audit estate. Naming those
# EFFECTS instead of kernel revision `0001_initial_tenant_schema` is what lets
# this module install into an assembly that supplies them from its own lineage;
# ERP hosts `public.tenants` itself and can never run kernel 0001 (ADR-0006 D1
# amendment). Literals, not imported constants, so the composed gate can read
# them statically and diff them against the manifest.
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")

# Resolved from this assembly's installed bindings, so Alembic still orders on a
# concrete revision id.
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_imports"
_RUNS = "import_runs"
_ROWS = "import_run_rows"
_PARTITIONS = "import_partitions"


def upgrade() -> None:
    # A binding is a claim about the database, so it is checked against the
    # database before any DDL runs.
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_imports;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_imports TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        _RUNS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("source_file_id", sa.Uuid(), nullable=False),
        sa.Column("source_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("source_layout", sa.String(20), nullable=False),
        sa.Column("source_delimiter", sa.String(4), nullable=False),
        sa.Column("source_encoding", sa.String(40), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("error_code", sa.String(63), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_import_runs_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_import_runs_tenant_id_id"),
        # One validated run promotes into at most one apply run. The constraint
        # is what makes "applied exactly once" a database fact.
        sa.UniqueConstraint(
            "tenant_id", "source_run_id", name="uq_import_runs_tenant_source_run"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_run_id"],
            [
                "mod_imports.import_runs.tenant_id",
                "mod_imports.import_runs.id",
            ],
            ondelete="RESTRICT",
            name="fk_import_runs_source_run",
        ),
        schema=_SCHEMA,
    )
    op.create_index("ix_import_runs_tenant_id", _RUNS, ["tenant_id"], schema=_SCHEMA)
    op.create_index(
        "ix_import_runs_tenant_status", _RUNS, ["tenant_id", "status"], schema=_SCHEMA
    )
    op.create_index(
        "ix_import_runs_tenant_kind", _RUNS, ["tenant_id", "kind"], schema=_SCHEMA
    )

    op.create_table(
        _ROWS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(63), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
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
            name="fk_import_run_rows_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_import_run_rows_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "row_number",
            name="uq_import_run_rows_tenant_run_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [
                "mod_imports.import_runs.tenant_id",
                "mod_imports.import_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_import_run_rows_run",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_import_run_rows_tenant_run", _ROWS, ["tenant_id", "run_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_import_run_rows_tenant_status",
        _ROWS,
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        _PARTITIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_row", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("partition_file_id", sa.Uuid(), nullable=False),
        sa.Column("partition_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_import_partitions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [
                "mod_imports.import_runs.tenant_id",
                "mod_imports.import_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_import_partitions_run",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_import_partitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "ordinal",
            name="uq_import_partitions_tenant_run_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "partition_file_id",
            name="uq_import_partitions_tenant_run_file",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_import_partitions_tenant_run",
        _PARTITIONS,
        ["tenant_id", "run_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_import_partitions_tenant_claim",
        _PARTITIONS,
        ["tenant_id", "run_id", "status", "ordinal"],
        schema=_SCHEMA,
    )

    # Written out per table rather than looped: the composed migration gate reads
    # this SQL statically, so a schema name assembled at runtime would be a
    # schema name it cannot see.
    op.execute("ALTER TABLE mod_imports.import_runs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_imports.import_runs FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY import_runs_tenant_isolation
            ON mod_imports.import_runs
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_runs TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_runs "
        "TO platform_api;"
    )

    op.execute("ALTER TABLE mod_imports.import_run_rows ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_imports.import_run_rows FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY import_run_rows_tenant_isolation
            ON mod_imports.import_run_rows
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_run_rows "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_run_rows "
        "TO platform_api;"
    )

    op.execute("ALTER TABLE mod_imports.import_partitions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_imports.import_partitions FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY import_partitions_tenant_isolation
            ON mod_imports.import_partitions
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_partitions "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_imports.import_partitions "
        "TO platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_imports.import_partitions CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_imports.import_run_rows CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_imports.import_runs CASCADE;")
