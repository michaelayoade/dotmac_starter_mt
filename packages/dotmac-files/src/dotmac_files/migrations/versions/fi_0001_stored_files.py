"""Create the explicit tenant and platform stored-file planes (ADR-0023).

## What this lineage needs, and what it deliberately does not

Stored bytes need somewhere to hang a tenant foreign key and someone to grant
to. Nothing more. This revision therefore declares two logical prerequisites
rather than a physical edge to a foreign revision:

- `tenant_scope_catalog.v1` — the FK target `public.tenants.id` below, and the
  `public.app_current_tenant_id()` the RLS policy evaluates;
- `module_database_roles.v1` — `app_user`, `platform_api` and `app_admin`, which
  the schema and table grants below name.

It previously read `depends_on = ("0001_initial_tenant_schema",)`. That edge was
true in the Starter and false everywhere else: ERP hosts `public.tenants` in its
own lineage, so kernel `0001` — which creates that table unconditionally as its
first statement — can never run there. Naming the revision made stored bytes
un-installable in ERP unless ERP first converged its entire identity, RBAC and
audit estate onto the kernel's, which is a large amount of coupling to buy a
foreign-key target.

The assembly answers *who supplies these effects* (`install_prerequisite_bindings`
in its `env.py`), `resolve_depends_on` turns that answer back into a real
Alembic edge, and `require_prerequisites` proves the effects against the live
catalog before any DDL runs. See `dotmac_kernel.prerequisites`.

Revision ID: fi_0001_stored_files
Revises: (lineage root)
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "fi_0001_stored_files"
down_revision = None
branch_labels = ("files",)

# Written as literals, not as `TENANT_SCOPE_CATALOG_V1.name`, for the same
# reason this migration hard-codes every other constant it uses: a migration is
# a snapshot of an accepted decision, and importing a mutable runtime value
# would let a later edit silently change what an already-applied revision meant.
# It also keeps the composed gate able to READ this list statically and diff it
# against `dotmac_files.manifest`, which is the check that stops the migration
# and the manifest drifting apart.
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")

# Resolved from this assembly's installed bindings at script load, so Alembic
# still orders on a concrete revision id. An assembly that composes this module
# without binding both prerequisites fails here, before any DDL.
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_files"
_TABLE = "stored_files"
_PLATFORM_TABLE = "platform_stored_files"


def upgrade() -> None:
    # Before any DDL: the binding is a claim, so check it against the database.
    require_prerequisites(op.get_bind(), REQUIRES)
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
