"""Add append-only transport message correlations."""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ib_0003_transport_refs"
down_revision = "ib_0002_supplied_identity"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_inbox"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_unique_constraint(
        "uq_messages_tenant_id_id", "messages", ["tenant_id", "id"], schema=_SCHEMA
    )
    op.create_table(
        "message_transport_refs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("raw_ref", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("account_scope", sa.String(160), nullable=True),
        sa.Column("transport_key", sa.String(68), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            name="fk_message_transport_refs_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["mod_inbox.messages.tenant_id", "mod_inbox.messages.id"],
            name="fk_message_transport_refs_message",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_message_transport_refs_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "message_id",
            "transport_key",
            name="uq_message_transport_refs_tenant_message_key",
        ),
        sa.UniqueConstraint(
            "tenant_id", "transport_key", name="uq_message_transport_refs_tenant_key"
        ),
        sa.CheckConstraint(
            "trim(raw_ref) <> ''", name="ck_message_transport_refs_raw_nonblank"
        ),
        sa.CheckConstraint(
            "scope IN ('global', 'account')",
            name="ck_message_transport_refs_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'account') = (account_scope IS NOT NULL AND trim(account_scope) <> '')",
            name="ck_message_transport_refs_account_coherence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_message_transport_refs_tenant_message",
        "message_transport_refs",
        ["tenant_id", "message_id"],
        schema=_SCHEMA,
    )
    op.execute(
        "ALTER TABLE mod_inbox.message_transport_refs ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_inbox.message_transport_refs FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY message_transport_refs_tenant_isolation "
        "ON mod_inbox.message_transport_refs "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_inbox.message_transport_refs TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT ON mod_inbox.message_transport_refs TO platform_api;"
    )
    op.execute(
        "CREATE FUNCTION mod_inbox.refuse_message_transport_ref_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'message transport references are append-only'; END; $$;"
    )
    op.execute(
        "CREATE TRIGGER message_transport_refs_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inbox.message_transport_refs "
        "FOR EACH ROW EXECUTE FUNCTION mod_inbox.refuse_message_transport_ref_mutation();"
    )


def downgrade() -> None:
    bind = op.get_bind()
    populated = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM mod_inbox.message_transport_refs)")
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "cannot downgrade inbox transport refs while evidence exists"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS message_transport_refs_append_only "
        "ON mod_inbox.message_transport_refs;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_inbox.refuse_message_transport_ref_mutation();"
    )
    op.drop_index(
        "ix_message_transport_refs_tenant_message",
        table_name="message_transport_refs",
        schema=_SCHEMA,
    )
    op.drop_table("message_transport_refs", schema=_SCHEMA)
    op.drop_constraint("uq_messages_tenant_id_id", "messages", schema=_SCHEMA)
