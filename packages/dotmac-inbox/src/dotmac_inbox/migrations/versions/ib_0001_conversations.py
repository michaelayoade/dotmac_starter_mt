"""Inbox's schema and tables — the third module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and
`depends_on` (never `down_revision`) orders this after the kernel's `tenants`
table, which both tables reference. Cross-lineage ordering is `depends_on`
by rule — a `down_revision` across owners would splice two independently
released lineages into one chain and make either un-releasable.

Everything is fully qualified to `mod_ibx`. Hard rule 11 for both tables:
`tenant_id NOT NULL`, composite uniques including `tenant_id`, RLS ENABLEd *and*
FORCEd, a tenant-isolation policy, and the online-role grants. FORCE matters —
without it the table owner, which migrations run as, bypasses its own policy.

## No CHECK constraint on `channel` or `status`

`channel` is open by design: it is a declaration registry
(`dotmac_kernel.channels`), and the whole point is that a product adds one
without a migration. A CHECK would reintroduce exactly the `ALTER TYPE` growth
problem ADR-0008 records against native enums.

`status` is a CLOSED four-value vocabulary and still gets no constraint, for the
reason ticketing's `tk_0001` gives: a CHECK is an `ALTER TABLE` to change, so
the day a fifth standard status is genuinely justified it becomes a migration on
every deployment rather than a released module version. The vocabulary is closed
in `dotmac_inbox.lifecycle` and enforced on the way in, where it is testable.

## Why the dedup constraint has no partial predicate

CRM's equivalent is two overlapping partial unique indexes, one of whose
predicates contains a literal list of three channel names. That is the defect
`docs/inventories/inbox-sources.md` documents: the rule cannot be extended
without a migration, cannot be tested as a rule, and is silently contradicted by
the other index. Here `dotmac_inbox.threading.dedup_key` has already folded the
channel's declared id scope into the stored value, so one unconditional
`UNIQUE (tenant_id, dedup_key)` expresses every channel's rule.

Revision ID: ib_0001_conversations
Revises: (lineage root)
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ib_0001_conversations"
down_revision = None
branch_labels = ("inbox",)
depends_on = ("0001_initial_tenant_schema",)

# A literal, not `module_schema("ibx")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_ibx"

_CONVERSATIONS = "inbox_conversations"
_MESSAGES = "inbox_messages"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_ibx;")
    op.execute("GRANT USAGE ON SCHEMA mod_ibx TO app_user, platform_api;")

    op.create_table(
        _CONVERSATIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("account_scope", sa.String(160), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("thread_key", sa.String(512), nullable=False),
        sa.Column("external_thread_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", _JSON, nullable=True),
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
            name="fk_inbox_conversations_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "thread_key", name="uq_inbox_conversations_tenant_thread"
        ),
        # The composite-FK target for messages, observations and every product
        # link table.
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_conversations_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_conversations_tenant_id",
        _CONVERSATIONS,
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_conversations_tenant_status_last",
        _CONVERSATIONS,
        ["tenant_id", "status", "last_message_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        _MESSAGES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("dedup_key", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        # The kernel observation this was derived from. Not an FK: it lives
        # in `public` and this table in `mod_ibx`, and a cross-schema FK
        # would make the module un-installable without that exact kernel
        # migration.
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_inbox_messages_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [
                "mod_ibx.inbox_conversations.tenant_id",
                "mod_ibx.inbox_conversations.id",
            ],
            ondelete="CASCADE",
            name="fk_inbox_messages_conversation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_inbox_messages_tenant_dedup"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inbox_messages_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_messages_tenant_conversation",
        _MESSAGES,
        ["tenant_id", "conversation_id", "created_at"],
        schema=_SCHEMA,
    )

    # Literal SQL per table, never looped: the composed gate reads this file
    # statically without importing it, so a statement built from a loop variable
    # is uninspectable and fails closed — correctly.
    op.execute("ALTER TABLE mod_ibx.inbox_conversations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_ibx.inbox_conversations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY inbox_conversations_tenant_isolation
            ON mod_ibx.inbox_conversations
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ibx.inbox_conversations "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ibx.inbox_conversations "
        "TO platform_api;"
    )

    op.execute("ALTER TABLE mod_ibx.inbox_messages ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_ibx.inbox_messages FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY inbox_messages_tenant_isolation ON mod_ibx.inbox_messages
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ibx.inbox_messages TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ibx.inbox_messages "
        "TO platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_ibx.inbox_messages CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_ibx.inbox_conversations CASCADE;")
    # The schema itself is left in place: a product link table referencing this
    # one lives in the PRODUCT's schema, and dropping `mod_ibx` here would take
    # an unrelated owner's table with it via CASCADE. Removing the namespace is
    # a deliberate operator act.
