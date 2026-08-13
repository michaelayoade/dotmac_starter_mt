"""Template Studio's schema and tables — the FIRST module lineage (ADR-0006 D1).

This is the lineage ROOT: `down_revision = None`, and `branch_labels` names the
owner. That label is how an `alembic_version` row is attributed to this module
rather than to the kernel or the assembly, and the composed gate reads it
statically to resolve this location's owner — there is no second location→owner
map to keep in sync.

`depends_on` (never `down_revision`) orders this lineage after the kernel's
`tenants` table, which both of these tables reference. Cross-lineage ordering is
`depends_on` by rule: a `down_revision` across owners would splice two
independently released lineages into one chain and make either un-releasable.

Everything is fully qualified to `mod_tstudio` — every `op.*` call carries
`schema=`, every raw statement names the schema, and both foreign keys spell
their referent's schema. `search_path` is connection state a pooler or another
module can change; a module that relied on it would write into whatever schema
happened to be first.

Hard rule 11 in one migration, for both tables: `tenant_id NOT NULL`, composite
uniques that include `tenant_id`, RLS ENABLEd *and* FORCEd, a tenant-isolation
policy, and the online-role grants. FORCE matters — without it the table owner
(which migrations run as) bypasses its own policy.

Revision ID: ts_0001_templates
Revises: (lineage root)
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ts_0001_templates"
down_revision = None
branch_labels = ("template_studio",)
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

# A literal, not `module_schema("tstudio")`. A migration is a frozen historical
# artifact: it must keep building the same schema even if a future kernel
# changed how a name is derived. The static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_tstudio"

_TEMPLATES = "templates"
_VERSIONS = "template_versions"


def upgrade() -> None:
    # A binding is a claim about the database, so it is checked against the
    # database before any DDL runs.
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_tstudio;")
    # The online roles need USAGE on the schema itself before any table grant
    # can take effect. `app_admin` is the migration role and owns the schema.
    op.execute("GRANT USAGE ON SCHEMA mod_tstudio TO app_user, platform_api;")

    op.create_table(
        _TEMPLATES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("channel", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_version", sa.Integer(), nullable=True),
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
        # Cross-schema and explicit: the tenant registry is the kernel's, in the
        # `public` compatibility namespace.
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_templates_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "kind", "slug", name="uq_templates_tenant_slug"
        ),
        # The composite-FK target for `template_versions` — see that table.
        sa.UniqueConstraint("tenant_id", "id", name="uq_templates_tenant_id_id"),
        sa.CheckConstraint(
            "kind IN ('notification', 'document')", name="ck_templates_kind"
        ),
        schema=_SCHEMA,
    )
    op.create_index("ix_templates_tenant_id", _TEMPLATES, ["tenant_id"], schema=_SCHEMA)

    op.create_table(
        _VERSIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(300), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("author_party_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_template_versions_tenant",
        ),
        # COMPOSITE, deliberately. A plain `template_id` reference would let one
        # tenant's version row point at another tenant's template whenever an id
        # leaked; carrying `tenant_id` into the reference makes that
        # unrepresentable at the database level.
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            # Literal referents, for the same reason the raw SQL below is
            # literal: the gate resolves these statically and an interpolated
            # name reads as unqualified, so it fails closed.
            ["mod_tstudio.templates.tenant_id", "mod_tstudio.templates.id"],
            ondelete="CASCADE",
            name="fk_template_versions_template",
        ),
        sa.UniqueConstraint(
            "tenant_id", "template_id", "version", name="uq_template_versions_number"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_template_versions_tenant_id", _VERSIONS, ["tenant_id"], schema=_SCHEMA
    )

    # Written out per table as LITERAL SQL rather than looped over an f-string.
    # The composed gate reads this file statically, without importing it: a
    # statement built from a loop variable is `<uninspectable dynamic SQL>` to
    # an AST scan, so the gate cannot confirm it names `mod_tstudio` and fails
    # closed — correctly. Literals are also the honest shape for a migration,
    # which is a frozen historical artifact rather than a program.
    op.execute("ALTER TABLE mod_tstudio.templates ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_tstudio.templates FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY templates_tenant_isolation ON mod_tstudio.templates
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tstudio.templates TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tstudio.templates "
        "TO platform_api;"
    )

    op.execute("ALTER TABLE mod_tstudio.template_versions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_tstudio.template_versions FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY template_versions_tenant_isolation
            ON mod_tstudio.template_versions
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tstudio.template_versions "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tstudio.template_versions "
        "TO platform_api;"
    )


def downgrade() -> None:
    # Versions first — it holds the composite FK into `templates`.
    op.drop_index("ix_template_versions_tenant_id", _VERSIONS, schema=_SCHEMA)
    op.drop_table(_VERSIONS, schema=_SCHEMA)
    op.drop_index("ix_templates_tenant_id", _TEMPLATES, schema=_SCHEMA)
    op.drop_table(_TEMPLATES, schema=_SCHEMA)
    # RESTRICT, not CASCADE: the schema is this module's namespace, and dropping
    # it should fail loudly if anything unexpected was created inside it rather
    # than silently taking that object with it.
    op.execute("DROP SCHEMA IF EXISTS mod_tstudio RESTRICT;")
