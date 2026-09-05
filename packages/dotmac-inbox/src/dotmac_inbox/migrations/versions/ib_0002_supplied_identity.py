"""Add product-supplied thread and message identities.

Revision ID: ib_0002_supplied_identity
Revises: ib_0001_conversations
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ib_0002_supplied_identity"
down_revision = "ib_0001_conversations"
branch_labels = None
depends_on = None

_SCHEMA = "mod_inbox"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("supplied_thread_ref", sa.String(255), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "messages",
        sa.Column("supplied_message_ref", sa.String(255), nullable=True),
        schema=_SCHEMA,
    )
    op.alter_column(
        "conversations",
        "contact",
        existing_type=sa.String(320),
        nullable=True,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_conversations_supplied_thread_ref_nonblank",
        "conversations",
        "supplied_thread_ref IS NULL OR trim(supplied_thread_ref) <> ''",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_conversations_identity_evidence",
        "conversations",
        "trim(coalesce(contact, '')) <> '' OR "
        "trim(coalesce(transport_thread_ref, '')) <> '' OR "
        "trim(coalesce(supplied_thread_ref, '')) <> ''",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_conversations_thread_refs_exclusive",
        "conversations",
        "NOT (supplied_thread_ref IS NOT NULL AND transport_thread_ref IS NOT NULL)",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_messages_supplied_message_ref_nonblank",
        "messages",
        "supplied_message_ref IS NULL OR trim(supplied_message_ref) <> ''",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    incompatible = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM mod_inbox.conversations "
            "WHERE supplied_thread_ref IS NOT NULL OR contact IS NULL "
            "UNION ALL "
            "SELECT 1 FROM mod_inbox.messages "
            "WHERE supplied_message_ref IS NOT NULL"
            ")"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError(
            "cannot downgrade inbox supplied identity while supplied references "
            "or NULL conversation contacts exist"
        )
    op.drop_constraint(
        "ck_messages_supplied_message_ref_nonblank", "messages", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_conversations_thread_refs_exclusive", "conversations", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_conversations_identity_evidence", "conversations", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_conversations_supplied_thread_ref_nonblank",
        "conversations",
        schema=_SCHEMA,
    )
    op.alter_column(
        "conversations",
        "contact",
        existing_type=sa.String(320),
        nullable=False,
        schema=_SCHEMA,
    )
    op.drop_column("messages", "supplied_message_ref", schema=_SCHEMA)
    op.drop_column("conversations", "supplied_thread_ref", schema=_SCHEMA)
