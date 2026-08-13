"""Ticketing's tables, bound to the `mod_tkt` schema (ADR-0006 D1, ADR-0023).

Every model carries `schema_table_args(SCHEMA)`, so the ORM emits fully
qualified `mod_tkt.<table>` rather than resolving through `search_path` —
connection state a pooler or another module can change.

## Two planes, one lifecycle

This module is dual-plane (ADR-0023). The lifecycle, the status vocabulary, the
transition guards and the reason registry are **shared** — they are pure Python
and know nothing about persistence. What is duplicated, deliberately, is the
STORAGE:

| | tenant plane | platform plane |
|---|---|---|
| models | `TenantTicket(Comment)` | `PlatformTicket(Comment)` |
| tables | `tickets`, `ticket_comments` | the same two, `platform_`-prefixed |
| `tenant_id` | `NOT NULL` | absent |
| isolation | FORCEd RLS + policy | grants only; `app_user` REVOKEd |
| number unique | per tenant | control-plane-wide |
| link helper | `link_tenant_subject()` | `link_platform_subject()` |

A product data plane (ERP, Sub) uses the tenant plane. A control plane (the
vendor CP) uses the platform plane. Neither can reach the other's rows: the
tenant tables' RLS predicate needs a tenant GUC a platform session never sets,
and the platform tables are REVOKEd from the tenant role outright.

**The bare name is the tenant plane.** `tickets` carries a `tenant_id`;
`platform_tickets` is the prefixed exception. Tenancy is this fleet's default
(multi-tenant always), so prefixing both would imply a third, unprefixed thing
exists. The Python classes are both explicit because a bare `Ticket` in a
product's imports is exactly the ambiguity this split removes.

## What is here, and what deliberately is not

`tickets` is the request; `ticket_comments` is what was said about it. That is
the whole core, on each plane. Notably absent:

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

## Tenancy, on the tenant plane

Both tenant tables carry a real `tenant_id NOT NULL`, and `ticket_comments`
references its parent through the COMPOSITE `(tenant_id, ticket_id)` rather than
a bare `ticket_id` — a single-column reference would let one tenant's comment
point at another tenant's ticket the moment an id leaked. Same defence the
kernel's `party_role_grants` uses, and the reason `tickets` declares the
otherwise-redundant `uq_tickets_tenant_id_id`.

The platform plane needs none of that: with no tenant column there is no
cross-tenant reference to make unrepresentable, so its comment FK is the plain
single-column one.
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

#: The module's immutable namespace, from the ledger allocation. BOTH planes
#: live here: they are one module's data, held to two isolation contracts.
SCHEMA = module_schema("tkt")

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class _TicketColumns:
    """The plane-independent columns, declared once.

    A mixin rather than a shared base class: the two planes must not share a
    mapped ancestor, or a polymorphic query could span them and the separation
    would be a naming convention instead of a structure. This carries only
    column definitions, which are re-evaluated per class by SQLAlchemy's
    declarative mixin machinery.
    """

    #: Human reference, e.g. `TKT-1043`. Allocated by the service. Scope of the
    #: uniqueness differs per plane — see each class's `__table_args__`.
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

    #: Actor ids, deliberately NOT foreign keys. On the tenant plane identity
    #: lives in the kernel's `parties`; on the platform plane it is a
    #: `platform_admins` row. A module that FK'd to either would force every
    #: consumer to adopt that identity model before it could adopt tickets.
    #: The service validates; the schema stays adoptable.
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

    #: Free-form operator tags. Searchable, no behaviour, no declaration — the
    #: layer below `status_reason` (see the dossier's layering table).
    tags: Mapped[list | None] = mapped_column(_JSON_VARIANT, nullable=True)


class _CommentColumns:
    """The plane-independent comment columns. Same mixin rationale as above."""

    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Internal notes are invisible to the requester. Defaulting to FALSE is the
    #: safe direction only if every caller is explicit; the service requires it,
    #: so the default here exists for migrations, not for callers.
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.false()
    )
    #: Actor id of the author, or NULL for a system-generated entry. Not an FK,
    #: for the same adoptability reason as the ticket's actor columns.
    author_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


# ── Tenant plane ────────────────────────────────────────────────────────────


class TenantTicket(Base, _TicketColumns, TimestampMixin):
    """A tenant's durable request for work, with a guarded lifecycle."""

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

    #: The ticket this one was folded into. Composite-FK'd for the same tenant
    #: reason as everything else, and self-referential, so a merge chain is
    #: walkable rather than a dangling id.
    merged_into_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class TenantTicketComment(Base, _CommentColumns, TimestampMixin):
    """What was said about a tenant ticket, and whether the requester sees it."""

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


# ── Platform plane ──────────────────────────────────────────────────────────


class PlatformTicket(Base, _TicketColumns, TimestampMixin):
    """A control-plane request for work. No tenant, no RLS (ADR-0023).

    A vendor support desk's ticket about a deployment, a licence delivery or a
    vendor account is a CONTROL-PLANE fact. Giving it a `tenant_id` would assert
    it belongs to one tenant of the product data plane, which is false — and
    minting a sentinel tenant to satisfy the column is the specific dodge
    ADR-0023 rejects.

    Isolation here is the privilege boundary: GRANT to `platform_api` and
    `app_admin`, REVOKE ALL from `app_user`. The kernel's live-catalog gate
    checks that revoke as strictly as it checks an RLS policy on the tenant
    side.
    """

    __tablename__ = "platform_tickets"
    __table_args__ = (
        # Unique control-plane-wide, not per tenant: there is no tenant to scope
        # by, and the vendor genuinely has ONE numbering series.
        UniqueConstraint("number", name="uq_platform_tickets_number"),
        Index("ix_platform_tickets_status", "status"),
        Index("ix_platform_tickets_assignee", "assigned_to_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()

    #: Self-referential merge target. Single-column, because with no tenant
    #: column there is no cross-tenant merge to make unrepresentable.
    merged_into_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )


class PlatformTicketComment(Base, _CommentColumns, TimestampMixin):
    """What was said about a control-plane ticket."""

    __tablename__ = "platform_ticket_comments"
    __table_args__ = (
        Index("ix_platform_ticket_comments_ticket", "ticket_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    ticket_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.platform_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )


#: The tables held to the tenant contract, in manifest-declaration order.
TENANT_TABLES: tuple[str, ...] = ("tickets", "ticket_comments")
#: The tables held to the platform contract (ADR-0023).
PLATFORM_TABLES: tuple[str, ...] = ("platform_tickets", "platform_ticket_comments")

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "PlatformTicket",
    "PlatformTicketComment",
    "TenantTicket",
    "TenantTicketComment",
]
