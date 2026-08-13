"""Create the tenant-scoped import run ledger (ADR-0025).

Revision ID: im_0001_import_runs
Revises: (lineage root)
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "im_0001_import_runs"
down_revision = None
branch_labels = ("imports",)
depends_on = ("0001_initial_tenant_schema",)

_SCHEMA = "mod_imports"
_RUNS = "import_runs"
_ROWS = "import_run_rows"


def upgrade() -> None:
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
            [f"{_SCHEMA}.{_RUNS}.tenant_id", f"{_SCHEMA}.{_RUNS}.id"],
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
            [f"{_SCHEMA}.{_RUNS}.tenant_id", f"{_SCHEMA}.{_RUNS}.id"],
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_imports.import_run_rows CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_imports.import_runs CASCADE;")
