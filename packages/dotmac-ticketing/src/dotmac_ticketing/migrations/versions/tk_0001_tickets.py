"""Ticketing's schema and tables — the second module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and
`depends_on` (never `down_revision`) orders this after the kernel's `tenants`
table, which both tables reference. Cross-lineage ordering is `depends_on` by
rule — a `down_revision` across owners would splice two independently released
lineages into one chain and make either un-releasable.

Everything is fully qualified to `mod_tkt`.

## Two planes, two contracts (ADR-0023)

This lineage creates FOUR tables in two planes, held to opposite contracts.

**Tenant plane** (`tickets`, `ticket_comments`) — hard rule 11: `tenant_id NOT
NULL`, composite uniques including `tenant_id`, RLS ENABLEd *and* FORCEd, a
tenant-isolation policy, and the online-role grants. FORCE matters — without it
the table owner, which migrations run as, bypasses its own policy.

**Platform plane** (`platform_tickets`, `platform_ticket_comments`) — no
`tenant_id`, no RLS, GRANT to `platform_api`/`app_admin` and **REVOKE ALL from
`app_user`**. The revoke is the load-bearing half: on this plane the privilege
boundary IS the isolation, so it is checked by the kernel's live-catalog gate as
strictly as an RLS policy is on the tenant side. A control-plane ticket about a
deployment or a licence delivery belongs to the vendor, not to a tenant, and
minting a sentinel tenant to satisfy a column is the dodge ADR-0023 rejects.

No foreign key crosses the two planes, and the kernel gate refuses one that
does. They share a lifecycle, never a row.

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
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import all_bound, resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "tk_0001_tickets"
down_revision = None
branch_labels = ("ticketing",)
# This lineage needs a tenant catalogue to point its foreign keys at and roles to
# grant to — NOT the kernel's identity, RBAC or audit estate. Naming those
# EFFECTS instead of kernel revision `0001_initial_tenant_schema` is what lets
# this module install into an assembly that supplies them from its own lineage;
# ERP hosts `public.tenants` itself and can never run kernel 0001 (ADR-0006 D1
# amendment). Literals, not imported constants, so the composed gate can read
# them statically and diff them against the manifest.
# Split by PLANE (ADR-0027). The platform plane needs roles to grant to; the
# tenant plane additionally needs a catalogue for its foreign keys and an RLS
# predicate to evaluate. Merged into one list, this lineage demanded a tenant
# scope in order to create ANY table — which made the module un-installable in
# the vendor control plane, an assembly that has no tenant catalogue and never
# will.
PLATFORM_REQUIRES = ("module_database_roles.v1",)
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
REQUIRES = PLATFORM_REQUIRES + TENANT_REQUIRES

# Resolved from this assembly's installed bindings, so Alembic still orders on a
# concrete revision id.
# A bound optional prerequisite still contributes a real ordering edge; an
# unbound one contributes nothing, because the plane needing it is not built.
depends_on = resolve_depends_on(PLATFORM_REQUIRES, optional=TENANT_REQUIRES)

# A literal, not `module_schema("tkt")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_tkt"

_TICKETS = "tickets"
_COMMENTS = "ticket_comments"
_PLATFORM_TICKETS = "platform_tickets"
_PLATFORM_COMMENTS = "platform_ticket_comments"


def upgrade() -> None:
    # A binding is a claim about the database, so it is checked against the
    # database before any DDL runs.
    require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    tenant_plane = all_bound(TENANT_REQUIRES)
    if tenant_plane:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_tkt;")
    # `app_admin` joins `platform_api` because the platform plane below grants it
    # DML: schema USAGE is a prerequisite for reaching any table in it.
    op.execute("GRANT USAGE ON SCHEMA mod_tkt TO platform_api, app_admin;")
    if tenant_plane:
        # Only where there is something for the tenant role to reach — a
        # platform-only schema must not demand tenant-role USAGE (kernel a57).
        op.execute("GRANT USAGE ON SCHEMA mod_tkt TO app_user;")

    _upgrade_platform_plane()
    if tenant_plane:
        _upgrade_tenant_plane()


def _upgrade_tenant_plane() -> None:
    """Built only where the assembly bound `tenant_scope_catalog.v1`."""
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


def _upgrade_platform_plane() -> None:
    """Always built (ADR-0023/ADR-0027).

    No tenant_id, no RLS, and REVOKEd from app_user. See this file's docstring
    for why that is a contract rather than an omission.
    """
    op.create_table(
        _PLATFORM_TICKETS,
        sa.Column("id", sa.Uuid(), primary_key=True),
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
        # Single-column, unlike the tenant plane's composite: with no tenant
        # column there is no cross-tenant merge to make unrepresentable.
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["mod_tkt.platform_tickets.id"],
            ondelete="SET NULL",
            name="fk_platform_tickets_merged_into",
        ),
        # Control-plane-wide, not per tenant. There is no tenant to scope by,
        # and the vendor genuinely runs ONE numbering series.
        sa.UniqueConstraint("number", name="uq_platform_tickets_number"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_tickets_status", _PLATFORM_TICKETS, ["status"], schema=_SCHEMA
    )
    op.create_index(
        "ix_platform_tickets_assignee",
        _PLATFORM_TICKETS,
        ["assigned_to_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        _PLATFORM_COMMENTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
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
            ["ticket_id"],
            ["mod_tkt.platform_tickets.id"],
            ondelete="CASCADE",
            name="fk_platform_ticket_comments_ticket",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_ticket_comments_ticket",
        _PLATFORM_COMMENTS,
        ["ticket_id"],
        schema=_SCHEMA,
    )

    # Literal per table, same reason as the tenant grants above. The REVOKE is
    # last so a future edit adding a grant cannot silently outrank it, and it is
    # the half that actually isolates this plane.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.platform_tickets "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.platform_tickets "
        "TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.platform_ticket_comments "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.platform_ticket_comments "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_tkt.platform_tickets FROM app_user;")
    op.execute("REVOKE ALL ON mod_tkt.platform_ticket_comments FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_tkt.platform_ticket_comments CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_tkt.platform_tickets CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_tkt.ticket_comments CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_tkt.tickets CASCADE;")
    # The schema itself is left in place: a product link table generated by
    # `link_subject` lives in the PRODUCT's schema but references this one, and
    # dropping `mod_tkt` here would take an unrelated owner's table with it via
    # CASCADE. Removing the namespace is a deliberate operator act.
