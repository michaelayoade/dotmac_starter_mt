"""Inbox tables, bound to the `mod_ibx` schema (ADR-0006 D1).

Every model carries `schema_table_args(SCHEMA)`, so the ORM emits fully
qualified `mod_ibx.<table>` rather than resolving through `search_path` —
connection state a pooler or another module can change.

## What is here, and what deliberately is not

Two tables: the conversation, and the messages on it. Notably absent, all of it
evidenced in `docs/inventories/inbox-sources.md` § "The contract":

* **No observation ledger.** It moved to `dotmac_kernel.inbound_models` on
  2026-08-12: the inbound seam is the kernel's, because admitting a provider
  fact needs `dotmac_kernel.idempotency` and a connected-account registry that
  consent, delivery and any conversation module all sit beside. A message points
  BACK at the observation it came from through `observation_id`; the kernel
  cannot point forward into a module's schema.

* **No subject columns.** No `subscriber_id`, no `person_id`, no `ticket_id`.
  Sub's conversation FKs to `subscribers` and CRM's to `people` NOT NULL; both
  are product identity models, and a module that FK'd to either would force
  every consumer to adopt that product's identity before it could adopt an
  inbox. Products link from their own schema.
* **No assignment, no presence, no queue.** Workforce policy. Sub has nine
  tables for it and they are correctly Sub's.
* **No routing rules, no automation, no labels, no macros, no templates.**
  Product policy and operator convenience.
* **No outbound delivery state.** The kernel outbox owns delivery; a second
  `status`/`attempts`/`next_attempt_at` triple here (CRM has exactly that in
  `crm_outbox`) would be a second writer of one concern.

## `contact` is a string, and there is no contact table

Both products resolve a contact address to a domain entity — Sub through
`inbox_contact_links` with subscriber/reseller/party-contact-point matching,
CRM through `person_channels`. That resolution is 340+ lines of ISP identity
policy in Sub alone. The module stores what the provider said and stops; who
that turns out to be is the product's answer, recorded in the product's tables.

## Status and channel are string columns, not native enums

Same reasoning as ticketing's `tk_0001`, and the same ADR-0008 non-conformance
it avoids: a native enum needs `ALTER TYPE` to grow. The four statuses are
closed at the Python layer (`dotmac_inbox.lifecycle.Status`) and enforced on the
way in, where it is also testable. `channel` is open by design and validated
against the declaration registry.

## Tenancy

Both tables carry a real `tenant_id NOT NULL`. `inbox_messages` references its
parent through a COMPOSITE `(tenant_id, conversation_id)` foreign key rather
than a bare id — a single-column reference
would let one tenant's message attach to another tenant's conversation the
moment an id leaked. Same defence the kernel's `party_roles` uses, and the
reason `inbox_conversations` declares the otherwise-redundant
`uq_inbox_conversations_tenant_id_id`.

**Neither source product has any tenancy at all** — Sub is single-operator by
design and CRM's inbox tables carry no scoping column. This is the starter's
contribution to the module rather than something ported.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
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
SCHEMA = module_schema("ibx")

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class InboxConversation(Base, TimestampMixin):
    """A durable, threaded exchange with one external party on one channel."""

    __tablename__ = "inbox_conversations"
    __table_args__ = (
        # The threading rule, enforced. `thread_key` is what
        # `dotmac_inbox.threading.thread_key` returns, and this constraint is
        # why that function can be trusted as THE rule rather than one of
        # several implementations — a second code path producing a different key
        # collides here instead of quietly opening a duplicate conversation.
        UniqueConstraint(
            "tenant_id", "thread_key", name="uq_inbox_conversations_tenant_thread"
        ),
        # Referenced by messages', observations' and product link tables'
        # composite FKs.
        UniqueConstraint("tenant_id", "id", name="uq_inbox_conversations_tenant_id_id"),
        Index("ix_inbox_conversations_tenant_id", "tenant_id"),
        # The workspace query: live conversations for a tenant, most recent
        # activity first. Status leads because every such query filters on it.
        Index(
            "ix_inbox_conversations_tenant_status_last",
            "tenant_id",
            "status",
            "last_message_at",
        ),
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

    #: The declared channel code. Validated against
    #: `dotmac_kernel.channels`, never constrained in SQL — see the docstring.
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The connected account this exchange happens at: a mailbox, a Meta page, a
    #: WhatsApp business number. Part of the thread key on every channel.
    account_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The external party's address or opaque provider id, as normalized by the
    #: product for ADDRESSABLE channels and verbatim for OPAQUE ones.
    contact: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Output of `dotmac_inbox.threading.thread_key`. Unique per tenant.
    thread_key: Mapped[str] = mapped_column(String(512), nullable=False)
    #: The provider's thread id when it supplied one. Kept alongside
    #: `thread_key` rather than derived from it: the key is opaque by design,
    #: and support work needs the provider's own identifier verbatim.
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: One of the four standard statuses. Closed vocabulary.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Why it is in that status. Open, product-declared, validated against
    #: `dotmac_inbox.lifecycle`. NULL is the common case.
    status_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    first_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the status enters RESOLVED, cleared on reopen.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When a SNOOZED conversation should return to OPEN. The product's
    #: scheduler acts on it; the module only records it.
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Free-form operator tags. Searchable, no behaviour, no declaration — the
    #: layer below `status_reason`.
    tags: Mapped[list | None] = mapped_column(_JSON_VARIANT, nullable=True)


class InboxMessage(Base, TimestampMixin):
    """One message on a conversation, in one direction."""

    __tablename__ = "inbox_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [
                f"{SCHEMA}.inbox_conversations.tenant_id",
                f"{SCHEMA}.inbox_conversations.id",
            ],
            name="fk_inbox_messages_conversation",
            ondelete="CASCADE",
        ),
        # The deduplication rule, enforced — and note what is NOT in the
        # predicate: no channel names. `dedup_key` has already folded the
        # channel's declared id scope into the value, so one unconditional
        # constraint replaces CRM's two overlapping partial indexes and the
        # three-channel literal list inside one of them.
        UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_inbox_messages_tenant_dedup"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inbox_messages_tenant_id_id"),
        Index(
            "ix_inbox_messages_tenant_conversation",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)

    #: Denormalized from the conversation. A conversation is single-channel
    #: today, but the column is what makes a message's dedup key checkable
    #: without a join, and multi-channel conversations are a plausible future.
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `inbound` | `outbound` | `internal`.
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Output of `dotmac_inbox.threading.dedup_key`. Unique per tenant, and
    #: NOT NULL for every direction — an outbound message the product sent twice
    #: is as much a duplicate as an inbound one delivered twice.
    dedup_key: Mapped[str] = mapped_column(String(512), nullable=False)

    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Party id of the operator who wrote it, or NULL for an inbound or
    #: system-generated message. Deliberately NOT a foreign key: identity lives
    #: in the kernel's `parties`, but a module that FK'd to it would force every
    #: consumer to adopt kernel identity before it could adopt an inbox. The
    #: service validates; the schema stays adoptable.
    author_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    #: The `dotmac_kernel.inbound_models.InboundObservation` this message was
    #: derived from, for an inbound message admitted through the kernel seam.
    #: NULL for outbound and internal messages, which have no provider fact.
    #:
    #: Deliberately NOT a foreign key: the observation lives in `public` and
    #: this table in `mod_ibx`, and a cross-schema FK would make the module
    #: un-installable without the exact kernel migration that created it. The
    #: pointer runs this way — consequence to fact — because the kernel may not
    #: reference a module's schema at all.
    observation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)

    #: When the provider says it was sent, and when we admitted it. Both, not
    #: one: provider clocks skew, and ordering by the wrong one reorders a
    #: customer's thread in front of them.
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "SCHEMA",
    "InboxConversation",
    "InboxMessage",
]
