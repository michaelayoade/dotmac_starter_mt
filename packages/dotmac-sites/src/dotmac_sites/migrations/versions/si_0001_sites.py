"""Create the tenant website composition plane.

Revision ID: si_0001_sites
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "si_0001_sites"
down_revision = None
branch_labels = ("sites",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_sites"


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_sites;")
    op.execute("REVOKE ALL ON SCHEMA mod_sites FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_sites TO app_user, app_admin;")

    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sites_tenant",
        ),
        sa.CheckConstraint("state IN ('active', 'archived')", name="ck_sites_state"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sites_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_sites_tenant_slug"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sites_tenant_state", "sites", ["tenant_id", "state"], schema=_SCHEMA
    )

    op.create_table(
        "pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("page_key", sa.String(120), nullable=False),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_pages_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["mod_sites.sites.tenant_id", "mod_sites.sites.id"],
            ondelete="RESTRICT",
            name="fk_pages_tenant_site",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pages_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name="uq_pages_tenant_site_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "page_key", name="uq_pages_tenant_site_key"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_pages_tenant_site", "pages", ["tenant_id", "site_id"], schema=_SCHEMA
    )

    op.create_table(
        "page_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("seo_payload", sa.JSON(), nullable=False),
        sa.Column("file_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("form_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_page_revisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "page_id"],
            [
                "mod_sites.pages.tenant_id",
                "mod_sites.pages.site_id",
                "mod_sites.pages.id",
            ],
            ondelete="RESTRICT",
            name="fk_page_revisions_tenant_site_page",
        ),
        sa.CheckConstraint(
            "revision_number > 0", name="ck_page_revisions_positive_number"
        ),
        sa.CheckConstraint(
            "length(content_digest) = 64", name="ck_page_revisions_digest"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_page_revisions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "page_id",
            "id",
            name="uq_page_revisions_tenant_site_page_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "page_id",
            "revision_number",
            name="uq_page_revisions_tenant_page_number",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_page_revisions_tenant_page",
        "page_revisions",
        ["tenant_id", "page_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "site_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_site_revisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["mod_sites.sites.tenant_id", "mod_sites.sites.id"],
            ondelete="RESTRICT",
            name="fk_site_revisions_tenant_site",
        ),
        sa.CheckConstraint(
            "revision_number > 0", name="ck_site_revisions_positive_number"
        ),
        sa.CheckConstraint(
            "length(snapshot_digest) = 64", name="ck_site_revisions_digest"
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'ready', 'retired')",
            name="ck_site_revisions_state",
        ),
        sa.CheckConstraint(
            "(state = 'draft' AND ready_at IS NULL AND retired_at IS NULL) OR "
            "(state = 'ready' AND ready_at IS NOT NULL AND retired_at IS NULL) OR "
            "(state = 'retired' AND ready_at IS NOT NULL AND retired_at IS NOT NULL)",
            name="ck_site_revisions_state_times",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_site_revisions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "id",
            name="uq_site_revisions_tenant_site_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "revision_number",
            name="uq_site_revisions_tenant_site_number",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_site_revisions_tenant_site",
        "site_revisions",
        ["tenant_id", "site_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_site_revisions_one_ready",
        "site_revisions",
        ["tenant_id", "site_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'ready'"),
    )

    op.create_table(
        "site_revision_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("site_revision_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("page_revision_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_site_revision_pages_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "site_revision_id"],
            [
                "mod_sites.site_revisions.tenant_id",
                "mod_sites.site_revisions.site_id",
                "mod_sites.site_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_site_revision_pages_tenant_site_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "page_id", "page_revision_id"],
            [
                "mod_sites.page_revisions.tenant_id",
                "mod_sites.page_revisions.site_id",
                "mod_sites.page_revisions.page_id",
                "mod_sites.page_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_site_revision_pages_tenant_site_page_revision",
        ),
        sa.CheckConstraint(
            "sort_order >= 0", name="ck_site_revision_pages_nonnegative_order"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_site_revision_pages_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "page_id",
            name="uq_site_revision_pages_tenant_revision_page",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "path",
            name="uq_site_revision_pages_tenant_revision_path",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_revision_id",
            "sort_order",
            name="uq_site_revision_pages_tenant_revision_order",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_site_revision_pages_tenant_revision",
        "site_revision_pages",
        ["tenant_id", "site_revision_id"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_sites.refuse_append_only_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$;

        CREATE TRIGGER page_revisions_append_only
        BEFORE UPDATE OR DELETE ON mod_sites.page_revisions
        FOR EACH ROW EXECUTE FUNCTION mod_sites.refuse_append_only_mutation();

        CREATE TRIGGER site_revision_pages_append_only
        BEFORE UPDATE OR DELETE ON mod_sites.site_revision_pages
        FOR EACH ROW EXECUTE FUNCTION mod_sites.refuse_append_only_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_sites.protect_site_revision_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'site revisions are immutable';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.site_id IS DISTINCT FROM OLD.site_id
               OR NEW.revision_number IS DISTINCT FROM OLD.revision_number
               OR NEW.snapshot_payload::text IS DISTINCT FROM OLD.snapshot_payload::text
               OR NEW.snapshot_digest IS DISTINCT FROM OLD.snapshot_digest
               OR NEW.created_by_ref IS DISTINCT FROM OLD.created_by_ref
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'site revision snapshot is immutable';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state
               AND NOT (
                   (OLD.state = 'draft' AND NEW.state = 'ready')
                   OR (OLD.state = 'ready' AND NEW.state = 'retired')
               ) THEN
                RAISE EXCEPTION 'invalid site revision readiness transition';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER site_revisions_immutable_snapshot
        BEFORE UPDATE OR DELETE ON mod_sites.site_revisions
        FOR EACH ROW EXECUTE FUNCTION mod_sites.protect_site_revision_snapshot();
        """
    )

    op.execute(
        """
        ALTER TABLE mod_sites.sites ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_sites.sites FORCE ROW LEVEL SECURITY;
        CREATE POLICY sites_tenant_isolation ON mod_sites.sites TO app_user
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        );
        GRANT SELECT, INSERT, UPDATE ON mod_sites.sites TO app_user;
        GRANT ALL PRIVILEGES ON mod_sites.sites TO app_admin;

        ALTER TABLE mod_sites.pages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_sites.pages FORCE ROW LEVEL SECURITY;
        CREATE POLICY pages_tenant_isolation ON mod_sites.pages TO app_user
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        );
        GRANT SELECT, INSERT ON mod_sites.pages TO app_user;
        GRANT ALL PRIVILEGES ON mod_sites.pages TO app_admin;

        ALTER TABLE mod_sites.page_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_sites.page_revisions FORCE ROW LEVEL SECURITY;
        CREATE POLICY page_revisions_tenant_isolation
        ON mod_sites.page_revisions TO app_user
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        );
        GRANT SELECT, INSERT ON mod_sites.page_revisions TO app_user;
        GRANT ALL PRIVILEGES ON mod_sites.page_revisions TO app_admin;

        ALTER TABLE mod_sites.site_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_sites.site_revisions FORCE ROW LEVEL SECURITY;
        CREATE POLICY site_revisions_tenant_isolation
        ON mod_sites.site_revisions TO app_user
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        );
        GRANT SELECT, INSERT, UPDATE ON mod_sites.site_revisions TO app_user;
        GRANT ALL PRIVILEGES ON mod_sites.site_revisions TO app_admin;

        ALTER TABLE mod_sites.site_revision_pages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_sites.site_revision_pages FORCE ROW LEVEL SECURITY;
        CREATE POLICY site_revision_pages_tenant_isolation
        ON mod_sites.site_revision_pages TO app_user
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        );
        GRANT SELECT, INSERT ON mod_sites.site_revision_pages TO app_user;
        GRANT ALL PRIVILEGES ON mod_sites.site_revision_pages TO app_admin;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER site_revisions_immutable_snapshot " "ON mod_sites.site_revisions;"
    )
    op.execute(
        "DROP TRIGGER site_revision_pages_append_only "
        "ON mod_sites.site_revision_pages;"
    )
    op.execute("DROP TRIGGER page_revisions_append_only ON mod_sites.page_revisions;")
    op.execute("DROP FUNCTION mod_sites.protect_site_revision_snapshot();")
    op.execute("DROP FUNCTION mod_sites.refuse_append_only_mutation();")
    op.drop_table("site_revision_pages", schema=_SCHEMA)
    op.drop_table("site_revisions", schema=_SCHEMA)
    op.drop_table("page_revisions", schema=_SCHEMA)
    op.drop_table("pages", schema=_SCHEMA)
    op.drop_table("sites", schema=_SCHEMA)
    op.execute("DROP SCHEMA mod_sites;")
