"""Ticketing's schema and tables — the second module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and
`depends_on` (never `down_revision`) orders this after the kernel's `tenants`
table, which both tables reference. Cross-lineage ordering is `depends_on` by
rule — a `down_revision` across owners would splice two independently released
lineages into one chain and make either un-releasable.

Everything is fully qualified to `mod_tkt`. Hard rule 11 for both tables:
`tenant_id NOT NULL`, composite uniques including `tenant_id`, RLS ENABLEd *and*
FORCEd, a tenant-isolation policy, and the online-role grants. FORCE matters —
without it the table owner, which migrations run as, bypasses its own policy.

## No CHECK constraint on `status`

Template Studio's `ts_0001` carries `CHECK (kind IN ('notification','document'))`
and this migration deliberately does not do the equivalent for `status`, even
though the nine statuses are a closed vocabulary. A CHECK is an `ALTER TABLE` to
change, which is the same growth problem ADR-0008 records against native enums:
the day a tenth standard status is genuinely justified, a constraint makes it a
migration on every deployment rather than a released module version. The
vocabulary is closed in `dotmac_ticketing.lifecycle` and enforced on the way in,
where it is also testable.

`status_reason` gets no constraint for the opposite reason: it is open by
design, validated against the product-declared registry.

Revision ID: tk_0001_tickets
Revises: (lineage root)
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "tk_0001_tickets"
down_revision = None
branch_labels = ("ticketing",)
depends_on = ("0001_initial_tenant_schema",)

# A literal, not `module_schema("tkt")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_tkt"

_TICKETS = "tickets"
_COMMENTS = "ticket_comments"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_tkt;")
    op.execute("GRANT USAGE ON SCHEMA mod_tkt TO app_user, platform_api;")

    op.create_table(
        _TICKETS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_reason", sa.String(64), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_to_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_team_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
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
            name="fk_tickets_tenant",
        ),
        # Self-referential and COMPOSITE: a merge target is always in the same
        # tenant, and carrying tenant_id into the reference makes the
        # cross-tenant merge unrepresentable rather than merely unlikely.
        sa.ForeignKeyConstraint(
            ["tenant_id", "merged_into_id"],
            ["mod_tkt.tickets.tenant_id", "mod_tkt.tickets.id"],
            ondelete="SET NULL",
            name="fk_tickets_merged_into",
        ),
        sa.UniqueConstraint("tenant_id", "number", name="uq_tickets_tenant_number"),
        # The composite-FK target for comments and for every product link table.
        sa.UniqueConstraint("tenant_id", "id", name="uq_tickets_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index("ix_tickets_tenant_id", _TICKETS, ["tenant_id"], schema=_SCHEMA)
    op.create_index(
        "ix_tickets_tenant_status", _TICKETS, ["tenant_id", "status"], schema=_SCHEMA
    )
    op.create_index(
        "ix_tickets_tenant_assignee",
        _TICKETS,
        ["tenant_id", "assigned_to_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        _COMMENTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "is_internal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("author_id", sa.Uuid(), nullable=True),
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
            name="fk_ticket_comments_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            ["mod_tkt.tickets.tenant_id", "mod_tkt.tickets.id"],
            ondelete="CASCADE",
            name="fk_ticket_comments_ticket",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ticket_comments_tenant_ticket",
        _COMMENTS,
        ["tenant_id", "ticket_id"],
        schema=_SCHEMA,
    )

    # Literal SQL per table, never looped: the composed gate reads this file
    # statically without importing it, so a statement built from a loop variable
    # is uninspectable and fails closed — correctly.
    op.execute("ALTER TABLE mod_tkt.tickets ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_tkt.tickets FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tickets_tenant_isolation ON mod_tkt.tickets
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.tickets TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.tickets TO platform_api;"
    )

    op.execute("ALTER TABLE mod_tkt.ticket_comments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_tkt.ticket_comments FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY ticket_comments_tenant_isolation ON mod_tkt.ticket_comments
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.ticket_comments TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.ticket_comments "
        "TO platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_tkt.ticket_comments CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_tkt.tickets CASCADE;")
    # The schema itself is left in place: a product link table generated by
    # `link_subject` lives in the PRODUCT's schema but references this one, and
    # dropping `mod_tkt` here would take an unrelated owner's table with it via
    # CASCADE. Removing the namespace is a deliberate operator act.
