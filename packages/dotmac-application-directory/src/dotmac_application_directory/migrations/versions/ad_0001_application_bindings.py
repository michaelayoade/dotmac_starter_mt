"""The directory's schema and table — the third module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and
`depends_on` (never `down_revision`) orders this after the kernel's `tenants`
table, which the binding references. Cross-lineage ordering is `depends_on` by
rule — a `down_revision` across owners would splice two independently released
lineages into one chain and make either un-releasable.

Everything is fully qualified to `mod_appdir`. Hard rule 11: `tenant_id NOT
NULL`, composite uniques including `tenant_id`, RLS ENABLEd *and* FORCEd, a
tenant-isolation policy, and the online-role grants. FORCE matters — without it
the table owner, which migrations run as, bypasses its own policy.

## No CHECK constraint on `state`, `source` or `reconciliation_status`

All three are closed vocabularies in `dotmac_application_directory.lifecycle`,
and none gets a database CHECK, for the reason ADR-0008 records and
`tk_0001_tickets` follows: a CHECK is an `ALTER TABLE` to change, so the day a
sixth lifecycle state is justified it becomes a migration on every deployment
rather than a released module version. The vocabularies are closed in Python,
enforced on the way in, and testable there.

## No authorization column

There is no column naming a person, member, group, role, grant or permission,
and there must never be one — ADR-0021 §3. A directory that acquires one has
become an access control list no target application agreed to. The static half
of that rule is
`tests/architecture/test_application_directory_module.py
::test_the_directory_holds_no_authorization_column`; this docstring is the
reminder for whoever writes `ad_0002`.

Revision ID: ad_0001_application_bindings
Revises: (lineage root)
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ad_0001_application_bindings"
down_revision = None
branch_labels = ("application_directory",)
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

# A literal, not `module_schema("appdir")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_appdir"

_BINDINGS = "application_bindings"

# `sha256:` + 64 hex characters, matching `descriptor.py`.
_DIGEST_LENGTH = 71


def upgrade() -> None:
    # A binding is a claim about the database, so it is checked against the
    # database before any DDL runs.
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_appdir;")
    op.execute("GRANT USAGE ON SCHEMA mod_appdir TO app_user, platform_api;")

    op.create_table(
        _BINDINGS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        # Identity of the connected application.
        sa.Column("application_code", sa.String(64), nullable=False),
        sa.Column("instance_ref", sa.String(200), nullable=False),
        sa.Column("local_tenant_ref", sa.String(200), nullable=False),
        # The descriptor copy.
        sa.Column("admin_url", sa.String(500), nullable=False),
        sa.Column("api_audience", sa.String(200), nullable=False),
        sa.Column("descriptor_version", sa.Integer(), nullable=False),
        sa.Column("descriptor_digest", sa.String(_DIGEST_LENGTH), nullable=False),
        # Lifecycle and provenance.
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        # Freshness.
        sa.Column("descriptor_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_status", sa.String(16), nullable=False),
        sa.Column("reconciliation_error", sa.Text(), nullable=True),
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
            name="fk_application_bindings_tenant",
        ),
        # One binding per application INSTANCE per tenant. A tenant may hold
        # several instances of the same application, so the uniqueness is over
        # the pair rather than over `application_code` alone.
        sa.UniqueConstraint(
            "tenant_id",
            "application_code",
            "instance_ref",
            name="uq_application_bindings_tenant_application_instance",
        ),
        # The composite-FK target for anything that later references a binding.
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_application_bindings_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_application_bindings_tenant_id",
        _BINDINGS,
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_application_bindings_tenant_state",
        _BINDINGS,
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    # Literal SQL, never built from a loop variable: the composed gate reads
    # this file statically without importing it, so a computed statement is
    # uninspectable and fails closed — correctly.
    op.execute(
        "ALTER TABLE mod_appdir.application_bindings " "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_appdir.application_bindings " "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY application_bindings_tenant_isolation
            ON mod_appdir.application_bindings
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_appdir.application_bindings TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_appdir.application_bindings TO platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_appdir.application_bindings CASCADE;")
    # The schema itself is left in place, matching `tk_0001`'s reasoning: a
    # namespace is an operator-visible allocation, and dropping it here would
    # CASCADE into anything else that had come to live in it. Removing the
    # namespace is a deliberate operator act.
