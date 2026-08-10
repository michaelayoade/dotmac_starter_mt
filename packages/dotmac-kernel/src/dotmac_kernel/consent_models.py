"""The do-not-contact ledger (ADR-0006 § 5c).

One tenant-scoped table, `communication_suppressions`, holding the answer to a
single question: *may we contact this address on this channel?*

Ported from `dotmac_sub:app/models/notification.py::CommunicationSuppression`,
the only qualifying implementation in the fleet — ERP has none at all while
sending invoices, statements and offer letters by email. Evidence:
`docs/inventories/consent-suppression-sources.md`.

Four columns carry the whole contract and are worth reading closely.

``channel``
    An open string, not an enum (ADR-0008). Sub's source enum has ten ISP-shaped
    members; a product declares the channels it actually delivers on.

``address``
    The NORMALISED recipient, and the thing the ledger is keyed on — not the
    person. An address is not always resolvable to a party (imports, forwarded
    mail), and the address is what the transport actually sends to.
    ``raw_address`` keeps what the customer clicked so the row stays auditable
    back to its origin.

``scope``
    ``marketing`` blocks marketing only — this is what "unsubscribe" means.
    ``all`` blocks everything, transactional included, and is reserved for
    addresses we must not send to at all: hard bounces, spam complaints, legal
    erasure. **Never** set by a customer clicking unsubscribe. Collapsing the two
    turns a consent ledger into a billing incident: someone who unsubscribed from
    a promo has not waived their invoice.

``reason``
    Provenance for the decision, so an operator looking at a blocked address can
    tell an unsubscribe from a bounce from an erasure request.

Both are CHECK-constrained rather than PostgreSQL enums, deliberately: the
2026-08-10 Template Studio audit found ERP's 43-member native
``document_template_type`` enum to be the same ADR-0008 non-conformance the
settings cutover is repairing, and a native enum needs an ``ALTER TYPE`` dance to
change. These two vocabularies are closed and legal rather than product-shaped,
so a CHECK is honest about them while staying cheap to amend.

Import-safe: this module touches only ``Base.metadata``, never the engine.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_kernel.models import Base, uuid_pk

#: Blocks marketing only. What an unsubscribe sets.
SCOPE_MARKETING = "marketing"
#: Blocks everything, transactional included. Bounces, complaints, erasure.
SCOPE_ALL = "all"
SUPPRESSION_SCOPES: tuple[str, ...] = (SCOPE_MARKETING, SCOPE_ALL)

REASON_UNSUBSCRIBE = "unsubscribe"
REASON_BOUNCE = "bounce"
REASON_COMPLAINT = "complaint"
REASON_MANUAL = "manual"
REASON_ERASURE = "erasure"
SUPPRESSION_REASONS: tuple[str, ...] = (
    REASON_UNSUBSCRIBE,
    REASON_BOUNCE,
    REASON_COMPLAINT,
    REASON_MANUAL,
    REASON_ERASURE,
)


class CommunicationSuppression(Base):
    """One address that must not be contacted on one channel, and why."""

    __tablename__ = "communication_suppressions"
    __table_args__ = (
        # Tenant-scoped identity. Sub keys on `(channel, address)` because it is
        # single-tenant; a shared ledger MUST carry the tenant or one tenant's
        # unsubscribe would silence another's invoice.
        UniqueConstraint(
            "tenant_id",
            "channel",
            "address",
            name="uq_communication_suppressions_tenant_channel_address",
        ),
        CheckConstraint(
            "scope IN ('marketing', 'all')",
            name="ck_communication_suppressions_scope",
        ),
        CheckConstraint(
            "reason IN ('unsubscribe', 'bounce', 'complaint', 'manual', 'erasure')",
            name="ck_communication_suppressions_reason",
        ),
        Index("ix_communication_suppressions_tenant_id", "tenant_id"),
        # The read path is always `(tenant, channel, address)`; this covers both
        # the single-address check and the bulk `IN` form.
        Index(
            "ix_communication_suppressions_lookup", "tenant_id", "channel", "address"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Normalised: lower-cased email, digits-only phone.
    #: See `dotmac_kernel.consent.normalize_address`.
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    #: What the customer actually clicked, kept so the row is auditable.
    raw_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: Best-effort link to a party. The ledger is keyed on the ADDRESS, not this.
    #: A bare UUID column, not an FK: `parties` is another concern's table and a
    #: suppression must survive the party record being removed.
    party_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Free-text provenance: the bounce code, the campaign carrying the
    #: unsubscribe link, the operator who set it by hand.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


__all__ = [
    "REASON_BOUNCE",
    "REASON_COMPLAINT",
    "REASON_ERASURE",
    "REASON_MANUAL",
    "REASON_UNSUBSCRIBE",
    "SCOPE_ALL",
    "SCOPE_MARKETING",
    "SUPPRESSION_REASONS",
    "SUPPRESSION_SCOPES",
    "CommunicationSuppression",
]
