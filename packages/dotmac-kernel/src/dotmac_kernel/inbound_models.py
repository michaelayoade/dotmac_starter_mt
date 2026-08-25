"""The two inbound tables: where a message arrives, and what the provider said.

**STATUS: PROTOTYPE — not a declared owner (2026-08-12).** This module exists as
audit evidence on branch `docs/omni-inbox-sources`. Whether this capability
belongs in the kernel at all is under adjudication in
`docs/superpowers/plans/2026-08-12-fleet-decomposition-matrix.md`. Do not publish,
do not adopt, and read every "Owner of" claim below as a proposal.

The kernel's communications stack was entirely OUTBOUND before this
(`messaging`, `delivery`, `delivery_providers`, `consent`, `channel_policy`).
`docs/inventories/inbox-sources.md` § "Prerequisites" measured the gap: no
webhook-receiver contract, no mailbox poller, no model for *this tenant has
connected this mailbox*, and nowhere to put per-connector credentials. These two
tables are the durable half of closing it.

## `connected_accounts` — where a message can arrive

The registry the whole inbound path hangs off. Its `account_scope` is the value
that identifies WHICH of a tenant's mailboxes, Meta pages or WhatsApp numbers a
message came to, and it is part of every thread key and most dedup keys
downstream — so without this table those keys have no source and a conversation
module cannot be adopted at all.

Both products have an equivalent and neither generalised it: CRM's
`connector_configs` + `integration_targets`, Sub's `team_inbox_email_routes` +
`team_inbox_channel_routes`. Sub's additionally carry ROUTING policy (which team
owns this mailbox), which is deliberately NOT here — routing is workforce policy
and stays in the product. This table answers only "does this account exist, is it
live, and how do I authenticate to it".

### Credentials are a NAME, never a value

`credential_name` is a key into `dotmac_kernel.secret_sources`, resolved at
startup and held in memory (ADR-0009). The value never touches this table, and
nothing on the read path reaches a network. A column holding an API token would
put every tenant's provider credentials in the database the application already
has broad read access to — and would make a database backup a credential leak.

## `inbound_observations` — what the provider actually said

The durable normalized fact, admitted BEFORE anything decides on it. Ported from
`dotmac_sub:app/models/team_inbox.py::InboxProviderObservation`, the fleet's only
implementation — CRM takes provider payloads straight to a derived row, so a
parsing bug there is unrecoverable for everything already ingested.

### What it does NOT do, and this is the important part

It does **not** decide at-most-once. That has one owner —
`dotmac_kernel.idempotency.execute_once` (ADR-0014, hard rule 21) — and
`dotmac_kernel.inbound.admit` delegates to it. Sub's table re-implements that
decision alongside the genuinely new one; porting it wholesale would have given
the fleet a fourth idempotency implementation while removing none.

The split, stated once:

| Question | Owner |
|---|---|
| has this provider event already been processed? | `dotmac_kernel.idempotency` |
| what exactly did the provider say, so consequences can be re-derived? | this table |

The unique constraint here is therefore belt-and-braces rather than the
mechanism: the decision is made in `admit`, and the constraint means a second
writer that bypassed `admit` collides instead of duplicating the fact.

### No back-reference to what it became

Sub's version carries `conversation_id`/`message_id`. Those are a MODULE's
tables, in a module's schema, which the kernel may not reference — and a
stringly-typed `result_ref` would be the same coupling wearing a disguise. The
pointer runs the other way: a consumer's own row carries the observation id it
came from (see `dotmac_inbox.models.InboxMessage.observation_id`). `processing_status`
still answers the operator question "which observations produced nothing", which
is what the back-reference was really for.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

#: An observation's lifecycle. Closed and legal rather than product-shaped, so
#: it is CHECK-constrained — same call `consent_models` makes for `scope`.
STATUS_RECORDED = "recorded"
STATUS_PROCESSED = "processed"
STATUS_REJECTED = "rejected"
OBSERVATION_STATUSES: frozenset[str] = frozenset(
    {STATUS_RECORDED, STATUS_PROCESSED, STATUS_REJECTED}
)


class ConnectedAccount(Base, TimestampMixin):
    """One of a tenant's accounts at one provider, on one channel."""

    __tablename__ = "connected_accounts"
    __table_args__ = (
        # The identity. `account_scope` is unique per tenant and provider — two
        # tenants may both connect `support@` at their own domains, and one
        # tenant may connect several mailboxes at the same provider.
        UniqueConstraint(
            "tenant_id",
            "provider",
            "account_scope",
            name="uq_connected_accounts_tenant_provider_scope",
        ),
        # The composite-FK target, for consumers that want a real reference
        # rather than the denormalised string. Same defence every other
        # tenant-scoped table in the fleet uses.
        UniqueConstraint("tenant_id", "id", name="uq_connected_accounts_tenant_id_id"),
        Index("ix_connected_accounts_tenant_id", "tenant_id"),
        # The dispatch read: live accounts for a tenant on a channel.
        Index(
            "ix_connected_accounts_tenant_channel",
            "tenant_id",
            "channel",
            "is_active",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    #: A code declared in `dotmac_kernel.channels`. Open string, validated on the
    #: way in rather than CHECK-constrained — the whole point of the registry is
    #: that a product adds a channel without a migration.
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Who operates it: `smtp`, `meta`, `whatsapp_cloud`. Product vocabulary.
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    #: WHICH account: the mailbox address, the Meta page id, the WhatsApp
    #: business number. The value every thread key downstream is scoped by.
    account_scope: Mapped[str] = mapped_column(String(160), nullable=False)

    #: What an operator sees. Never branched on.
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Deactivated rather than deleted: an account that stops receiving still has
    #: history pointing at its `account_scope`, and deleting the row would strand
    #: every conversation that arrived through it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.true()
    )

    #: A NAME resolved through `dotmac_kernel.secret_sources`, never a secret
    #: value (ADR-0009). See the module docstring.
    credential_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Non-secret provider configuration: an IMAP host and port, a Meta app id, a
    #: polling interval. Anything sensitive belongs behind `credential_name`.
    config: Mapped[dict | None] = mapped_column(_JSON_VARIANT, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class InboundObservation(Base, TimestampMixin):
    """A normalized provider fact, durable and replayable."""

    __tablename__ = "inbound_observations"
    __table_args__ = (
        # Provider event identity. Belt-and-braces: `dotmac_kernel.inbound.admit`
        # makes the at-most-once decision through `idempotency`, and this
        # constraint means a writer that bypassed `admit` collides rather than
        # duplicating the fact.
        UniqueConstraint(
            "tenant_id",
            "provider",
            "account_scope",
            "provider_event_id",
            name="uq_inbound_observations_tenant_event",
        ),
        CheckConstraint(
            "processing_status IN ('recorded', 'processed', 'rejected')",
            name="ck_inbound_observations_status",
        ),
        Index("ix_inbound_observations_tenant_id", "tenant_id"),
        # The operator question: what arrived and did not become anything.
        Index(
            "ix_inbound_observations_tenant_status",
            "tenant_id",
            "processing_status",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The connected account it arrived at. Deliberately the STRING rather than a
    #: foreign key to `connected_accounts`: an observation must be recordable
    #: even when it arrives at an account nobody registered, which is exactly the
    #: case an operator most needs to see.
    account_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The provider's own id for this delivery.
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The normalized fact. THE reason this table exists — everything else here
    #: is addressing and status.
    payload: Mapped[dict] = mapped_column(_JSON_VARIANT, nullable=False)

    #: When the provider says it happened, and when we admitted it. Both, never
    #: one: provider clocks skew, and an ingest lag is invisible without the pair.
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa.text(f"'{STATUS_RECORDED}'")
    )
    #: Why it was rejected, when it was. A rejected observation KEEPS its
    #: payload: that is the row that explains a missing message.
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)


__all__ = [
    "OBSERVATION_STATUSES",
    "STATUS_PROCESSED",
    "STATUS_RECORDED",
    "STATUS_REJECTED",
    "ConnectedAccount",
    "InboundObservation",
]
