"""Create the tenant-only conversation owner in ``mod_inbox``.

Revision ID: ib_0001_conversations
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ib_0001_conversations"
down_revision = None
branch_labels = ("inbox",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_inbox"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
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


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_inbox;")
    op.execute("GRANT USAGE ON SCHEMA mod_inbox TO app_user, platform_api;")

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("account_scope", sa.String(160), nullable=False),
        sa.Column("contact", sa.String(320), nullable=False),
        sa.Column("thread_key", sa.String(512), nullable=False),
        sa.Column("transport_thread_ref", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("tags", _JSON, nullable=True),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_conversations_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "thread_key", name="uq_conversations_tenant_thread"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_conversations_tenant_status_activity",
        "conversations",
        ["tenant_id", "status", "last_message_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("message_key", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("transport_message_ref", sa.String(255), nullable=True),
        sa.Column("transport_observation_ref", sa.String(255), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_messages_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["mod_inbox.conversations.tenant_id", "mod_inbox.conversations.id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "message_key", name="uq_messages_tenant_key"),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "id",
            name="uq_messages_conversation_id_id",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_messages_tenant_conversation_time",
        "messages",
        ["tenant_id", "conversation_id", "occurred_at", "id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "conversation_read_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("last_read_message_id", sa.Uuid(), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_conversation_read_states_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["mod_inbox.conversations.tenant_id", "mod_inbox.conversations.id"],
            name="fk_conversation_read_states_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "last_read_message_id"],
            [
                "mod_inbox.messages.tenant_id",
                "mod_inbox.messages.conversation_id",
                "mod_inbox.messages.id",
            ],
            name="fk_conversation_read_states_message",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "actor_id",
            name="uq_conversation_read_states_actor",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_conversation_read_states_actor",
        "conversation_read_states",
        ["tenant_id", "actor_id", "last_read_at"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_inbox.conversations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inbox.conversations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY conversations_tenant_isolation ON mod_inbox.conversations
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox.conversations TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox.conversations TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inbox.messages ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inbox.messages FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY messages_tenant_isolation ON mod_inbox.messages
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox.messages TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox.messages TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_inbox.conversation_read_states ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inbox.conversation_read_states FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY conversation_read_states_tenant_isolation
            ON mod_inbox.conversation_read_states
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_inbox.conversation_read_states TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_inbox.conversation_read_states TO platform_api;"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_read_states_actor",
        table_name="conversation_read_states",
        schema=_SCHEMA,
    )
    op.drop_table("conversation_read_states", schema=_SCHEMA)
    op.drop_index(
        "ix_messages_tenant_conversation_time",
        table_name="messages",
        schema=_SCHEMA,
    )
    op.drop_table("messages", schema=_SCHEMA)
    op.drop_index(
        "ix_conversations_tenant_status_activity",
        table_name="conversations",
        schema=_SCHEMA,
    )
    op.drop_table("conversations", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_inbox RESTRICT;")
