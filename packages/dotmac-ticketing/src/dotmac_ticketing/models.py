"""Ticketing's tables, bound to the `mod_tkt` schema (ADR-0006 D1).

Every model carries `schema_table_args(SCHEMA)`, so the ORM emits fully
qualified `mod_tkt.<table>` rather than resolving through `search_path` —
connection state a pooler or another module can change.

## What is here, and what deliberately is not

`tickets` is the request; `ticket_comments` is what was said about it. That is
the whole core. Notably absent:

* **No subject columns.** No `subscriber_id`, no `project_id`. A ticket has
  many subjects (Sub's has six, ERP's five) and they live in product-owned link
  tables — see :mod:`dotmac_ticketing.linking` for why, at length.
* **No category, no team-routing rules, no automation table.** Those are policy
  a product owns. This module holds the lifecycle and the trail.
* **No `resolution` text column.** ERP has one; it is a comment with a flag, and
  giving it a dedicated column means two places to look for the same sentence.

## Status is a string column, not a native enum

The nine standard statuses are closed at the *Python* layer
(:class:`dotmac_ticketing.lifecycle.Status`), and the database stores their
values as text. That is deliberate: a native PostgreSQL enum needs an
`ALTER TYPE` migration to change, which is exactly the non-conformance ADR-0008
records against `SettingDomain` and ERP's `document_template_type`. The closed
vocabulary is enforced where it can be enforced *and tested* — in the service
layer, on the way in.

`status_reason` is a plain string for the opposite reason: it is genuinely open,
product-declared, and validated against the registry rather than a constraint.

## Tenancy

Both tables carry a real `tenant_id NOT NULL`, and `ticket_comments` references
its parent through the COMPOSITE `(tenant_id, ticket_id)` rather than a bare
`ticket_id` — a single-column reference would let one tenant's comment point at
another tenant's ticket the moment an id leaked. Same defence the kernel's
`party_role_grants` uses, and the reason `tickets` declares the otherwise-redundant
`uq_tickets_tenant_id_id`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

#: The module's immutable namespace, from the ledger allocation.
SCHEMA = module_schema("tkt")

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class Ticket(Base, TimestampMixin):
    """A durable request for work, with one owner and a guarded lifecycle."""

    __tablename__ = "tickets"
    __table_args__ = (
        # The human-readable reference. Unique per tenant, not globally: two
        # tenants both having "TKT-1" is correct, and a global sequence would
        # leak fleet volume to anyone who can read their own ticket numbers.
        UniqueConstraint("tenant_id", "number", name="uq_tickets_tenant_number"),
        # Referenced by comments' and link tables' composite FKs.
        UniqueConstraint("tenant_id", "id", name="uq_tickets_tenant_id_id"),
        Index("ix_tickets_tenant_id", "tenant_id"),
        # The workqueue query: open tickets for a tenant, newest first. Status
        # leads because every such query filters on it.
        Index("ix_tickets_tenant_status", "tenant_id", "status"),
        Index("ix_tickets_tenant_assignee", "tenant_id", "assigned_to_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        # The column object, not a string — the kernel's tables are registered
        # unqualified in `Base.metadata`, so `"public.tenants.id"` resolves to
        # no metadata key while bare `"tenants.id"` would be search_path
        # dependent. The migration spells the qualified form in full.
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )

    #: Tenant-scoped human reference, e.g. `TKT-1043`. Allocated by the service.
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: One of the nine standard statuses. Closed vocabulary — see the module
    #: docstring for why it is text rather than a native enum.
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Why it is in that status. Open, product-declared, validated against
    #: `dotmac_ticketing.vocabulary` on the way in. NULL is the common case.
    status_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    #: How the request arrived. NULL when a product does not track it.
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: Party ids, deliberately NOT foreign keys. Identity lives in the kernel's
    #: `parties`, but a module that FK'd to it would force every consumer to
    #: adopt kernel identity before it could adopt tickets. The service
    #: validates; the schema stays adoptable.
    requested_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    assigned_to_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    assigned_team_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the status first enters class RESOLVED, cleared on reopen.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the status enters a terminal class.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The ticket this one was folded into. Composite-FK'd for the same tenant
    #: reason as everything else, and self-referential, so a merge chain is
    #: walkable rather than a dangling id.
    merged_into_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    #: Free-form operator tags. Searchable, no behaviour, no declaration — the
    #: layer below `status_reason` (see the dossier's layering table).
    tags: Mapped[list | None] = mapped_column(_JSON_VARIANT, nullable=True)


class TicketComment(Base, TimestampMixin):
    """What was said about a ticket, and whether the requester may see it."""

    __tablename__ = "ticket_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ticket_id"],
            [f"{SCHEMA}.tickets.tenant_id", f"{SCHEMA}.tickets.id"],
            name="fk_ticket_comments_ticket",
            ondelete="CASCADE",
        ),
        Index("ix_ticket_comments_tenant_ticket", "tenant_id", "ticket_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Internal notes are invisible to the requester. Defaulting to FALSE is the
    #: safe direction only if every caller is explicit; the service requires it,
    #: so the default here exists for migrations, not for callers.
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.false()
    )
    #: Party id of the author, or NULL for a system-generated entry. Not an FK,
    #: for the same adoptability reason as the ticket's party columns.
    author_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


__all__ = ["SCHEMA", "Ticket", "TicketComment"]
