"""`link_subject` — product-owned link tables, generated so nobody hand-writes one.

## What a ticket's subject is, and why it is not one column

A ticket is *about* something: a subscriber, a project, a licence, a device. The
obvious shape is a polymorphic pair on the shared table —
``(subject_type, subject_id)`` — and it is what ERP's ``GeneratedDocument`` uses
correctly for generated documents.

It is the wrong shape here, and the reason is measured rather than argued.
**A ticket does not have one subject.** ``dotmac_sub``'s ticket carries six
links (``subscriber_id``, ``customer_account_id``, ``lead_id``,
``customer_person_id``, ``origin_conversation_id``, ``service_team_id``);
``dotmac_erp``'s carries five (``raised_by_id``, ``project_id``, ``customer_id``,
``category_id``, ``team_id``). A single polymorphic pair holds one of those, so
the remainder would spill into a JSONB blob: no referential integrity *and* no
queryability, which is worse than either option applied cleanly.

Integrity then compounds it. A polymorphic ``subject_id`` is a UUID PostgreSQL
does not know means anything, so deleting a subscriber leaves a ticket pointing
at a ghost and nothing complains until an agent opens it. That is the "an
imported identifier becomes the only copy of truth" failure the Dotmac
source-of-truth standard names.

## So: link tables, in the PRODUCT's lineage

    mod_tkt.tickets                     ← this module owns
    public.sub_ticket_subscriber        ← dotmac_sub owns
        ticket_id     → mod_tkt.tickets(id)
        subscriber_id → public.subscribers(id)

Real foreign keys in both directions, declared delete semantics, and a product
may declare as many as it has subjects.

## Why a helper rather than a documented pattern

Three things get forgotten when a link table is hand-written, and all three are
silent:

1. **The RLS policy.** A tenant-scoped table without one is a cross-tenant read,
   and it fails no test that does not specifically look for it. This is the
   single strongest argument for generating the table.
2. **The composite tenant key.** A plain ``ticket_id`` FK lets one tenant's link
   row point at another tenant's ticket the moment an id leaks. The composite
   ``(tenant_id, ticket_id)`` reference is the same defence the kernel's
   ``party_role_grants`` uses.
3. **``ON DELETE``.** Whether removing a subscriber cascades the link or is
   restricted by it is a product policy decision. This helper gives it no
   default, so it cannot be decided by accident.

## The ordering constraint this creates

The generated FK targets ``mod_tkt.tickets``, so **this module's migration
lineage must run before the product migration that calls this helper.** That is
a real deployment step, not a detail: a product whose lineage runs first fails
at ``CREATE TABLE`` with a missing referenced relation. Assemblies compose
lineages in dependency order for exactly this reason, and the composed migration
gate is where the requirement should be asserted rather than trusted.

## What this helper is NOT

It does not create anything in ``mod_tkt``, and it must not: a module owning a
table outside its own schema breaks the namespace rule that makes module data
ownership legible. It emits operations into **the caller's** migration, so the
product's lineage owns, versions and drops its own link tables.
"""

from __future__ import annotations

from typing import Final, Literal

import sqlalchemy as sa
from dotmac_kernel.namespaces import module_schema

from alembic import op

__all__ = ["MODULE_SCHEMA", "TICKETS_TABLE", "drop_subject_link", "link_subject"]

#: Resolved from the ledger allocation, never spelled as a literal.
MODULE_SCHEMA: Final[str] = module_schema("tkt")
TICKETS_TABLE: Final[str] = "tickets"

OnDelete = Literal["CASCADE", "RESTRICT", "SET NULL"]

_IDENT_MAX = 63  # PostgreSQL identifier limit; a truncated constraint name collides.


def link_subject(
    *,
    table_name: str,
    subject_table: str,
    subject_column: str = "subject_id",
    subject_schema: str = "public",
    subject_pk: str = "id",
    on_delete_subject: OnDelete,
    on_delete_ticket: OnDelete = "CASCADE",
    schema: str = "public",
    app_role: str = "app_user",
) -> None:
    """Emit a product-owned ticket↔subject link table into the CALLER's migration.

    Call it from inside a product's own Alembic ``upgrade()``. Every argument
    that encodes a policy decision is keyword-only and, where it matters,
    without a default.

    :param table_name: the link table, in the product's namespace
        (e.g. ``sub_ticket_subscriber``). Prefix it with the product's own
        short name; this helper does not invent one, because the product owns
        the table.
    :param subject_table: the product table being linked (e.g. ``subscribers``).
    :param subject_column: column name for the subject key.
    :param on_delete_subject: **required.** What happens to the link when the
        SUBJECT is deleted. ``RESTRICT`` refuses to delete a subscriber that
        still has tickets; ``CASCADE`` drops the link and leaves the ticket.
        There is no default because there is no safe one.
    :param on_delete_ticket: what happens when the TICKET is deleted. Defaults
        to ``CASCADE``, which is safe in a way the subject side is not: the
        link's whole meaning is the ticket, so an orphaned link row is never
        wanted.
    """
    _check_identifier(table_name)
    _check_identifier(subject_table)
    _check_identifier(subject_column)

    op.create_table(
        table_name,
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "ticket_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            subject_column,
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # One link row per (ticket, subject-kind). A ticket has many subjects,
        # but not two subscribers — that would be two tickets.
        sa.PrimaryKeyConstraint("ticket_id", name=f"pk_{table_name}"),
        # COMPOSITE reference into the module's table. A bare ticket_id FK would
        # let a link row in tenant A point at a ticket in tenant B.
        sa.ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            [
                f"{MODULE_SCHEMA}.{TICKETS_TABLE}.tenant_id",
                f"{MODULE_SCHEMA}.{TICKETS_TABLE}.id",
            ],
            name=f"fk_{table_name}_ticket",
            ondelete=on_delete_ticket,
        ),
        sa.ForeignKeyConstraint(
            [subject_column],
            [f"{subject_schema}.{subject_table}.{subject_pk}"],
            name=f"fk_{table_name}_subject",
            ondelete=on_delete_subject,
        ),
        schema=schema,
    )
    # The subject side is the one queried in reverse — "tickets for this
    # subscriber" — and it is not covered by the ticket_id primary key.
    op.create_index(
        f"ix_{table_name}_{subject_column}",
        table_name,
        [subject_column],
        schema=schema,
    )
    op.create_index(
        f"ix_{table_name}_tenant_id",
        table_name,
        ["tenant_id"],
        schema=schema,
    )
    _enable_rls(table_name=table_name, schema=schema, app_role=app_role)


def drop_subject_link(*, table_name: str, schema: str = "public") -> None:
    """The matching ``downgrade()``. Policies and indexes go with the table."""
    _check_identifier(table_name)
    op.drop_table(table_name, schema=schema)


def _enable_rls(*, table_name: str, schema: str, app_role: str) -> None:
    """FORCE row-level security, plus the isolation policy and grants.

    ``FORCE`` as well as ``ENABLE``: without it the table owner bypasses the
    policy, and migrations run as an owner. ``ENABLE`` alone is the most common
    way an RLS estate reads as protected while not being.

    The predicate calls ``public.app_current_tenant_id()`` — the kernel's own
    accessor, not a raw ``current_setting`` — so a link table participates in
    exactly the same request-scoped isolation as the tables on either side of
    it, and a future change to how the tenant is resolved reaches it too.

    Note this SQL is built from parameters and is therefore *dynamic*: the
    composed migration gate's static scan cannot confirm it names a schema, so a
    product calling this helper inside its own migration will need that
    migration acknowledged by the gate the way any generated DDL is. That is a
    deliberate trade — the alternative is every product hand-writing the four
    statements below, which is how an RLS policy goes missing.
    """
    qualified = f"{schema}.{table_name}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_tenant_isolation ON {qualified} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {app_role}")


def _check_identifier(name: str) -> None:
    if not name or len(name) > _IDENT_MAX:
        raise ValueError(f"identifier {name!r} must be 1..{_IDENT_MAX} characters")
    if name != name.strip().lower() or not name.replace("_", "").isalnum():
        raise ValueError(
            f"identifier {name!r} must be lowercase alphanumeric with underscores"
        )
    # Constraint and index names are derived from the table name, so the table
    # name must leave room for the longest suffix this helper generates.
    longest = len(f"ix_{name}_subject_id")
    if longest > _IDENT_MAX:
        raise ValueError(
            f"table name {name!r} is too long: the generated index name "
            f"would be {longest} characters, over PostgreSQL's {_IDENT_MAX} limit"
        )
