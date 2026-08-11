"""The provider seam, and the one send path that asks consent (ADR-0006 § 5c).

**Owner of:** *this message is ready — may it go, over what, and what happened?*

The kernel ships a `Protocol` and no client. SMTP, Twilio, Africa's Talking and
Meta Cloud API are product dependencies, exactly as ADR-0009 ships a
`SecretSource` seam and no secret-store client. A product that sends by SMTP
brings its own SMTP.

## Why `send` is here rather than in each caller

Because the consent check must not be optional. If every sender wired its own
provider call, "ask consent first" would be a convention, and Sub's own history
is what a convention gets you: marketing eligibility lived inside the campaign
segment filter, so the answer depended on who was asking. Routing every send
through one function makes the check structural.

`send` does exactly five things, in order:

1. **Replay a completed dispatch.** A stable `dispatch_id` lets an outbox retry
   return the committed receipt without calling the provider again.
2. **Ask consent.** A suppressed address returns a `Suppressed` outcome and the
   provider is never called — no network, no cost, no receipt.
3. **Call the provider.** The adapter passes `message.idempotency_key` to a
   provider that supports idempotency, covering an unknown external outcome.
4. **Record the receipt**, which is what closes the bounce→consent loop
   (`dotmac_kernel.delivery.record_receipt`).
5. **Return the outcome**, so the caller can retry or give up on its own terms.

## What it deliberately does NOT do

- **It does not queue or retry.** `dotmac_kernel.messaging` owns that, and Sub's
  notification queue turned out to be that same engine built twice. A caller that
  wants durability enqueues an outbox event whose handler calls this.
- **It does not render.** `dotmac-template-studio` owns what the message says.
- **It does not choose a channel.** `dotmac_kernel.channel_policy` does.

Four owners, one call site each. That separation is the § 5c capability map, and
this module is where the map is honoured rather than described.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_kernel import consent, delivery
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_STATUSES,
    CommunicationDelivery,
)
from dotmac_kernel.idempotency import fingerprint_of


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What a provider is asked to send. Already rendered, already addressed."""

    #: Product/outbox identity created before the first provider call and reused
    #: on every relay attempt. Provider adapters use `idempotency_key` where the
    #: external API supports idempotent requests.
    dispatch_id: UUID
    channel: str
    address: str
    body: str
    #: Why this is being sent — `billing`, `marketing`, … The consent decision
    #: turns on it, so it is required rather than defaulted: a caller that has to
    #: name the category cannot accidentally get the marketing rule applied to an
    #: invoice, or the reverse.
    category: str
    subject: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_id, UUID):
            raise ValueError("dispatch_id must be a UUID")
        if not consent.normalize_channel(self.channel):
            raise ValueError("channel is required")
        if not consent.normalize_address(self.channel, self.address):
            raise ValueError("address is required")
        if not (self.category or "").strip():
            raise ValueError("category is required")

    @property
    def idempotency_key(self) -> str:
        """Stable external-provider key for this outbox dispatch."""
        return str(self.dispatch_id)

    @property
    def request_fingerprint(self) -> str:
        """Bind the stable dispatch id to exactly one rendered request."""
        channel = consent.normalize_channel(self.channel)
        return fingerprint_of(
            {
                "channel": channel,
                "address": consent.normalize_address(channel, self.address),
                "category": self.category.strip().lower(),
                "subject": self.subject,
                "body": self.body,
            }
        )


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """What the provider said, in the kernel's vocabulary.

    The adapter CLASSIFIES: a permanent failure is `bounced`, a transient one is
    `failed`. See `dotmac_kernel.delivery` for why that judgement cannot live in
    the kernel and why getting it wrong stops a customer's invoices.
    """

    status: str = DELIVERY_ACCEPTED
    provider_message_id: str | None = None
    response_code: str | None = None
    response_body: str | None = None

    def __post_init__(self) -> None:
        if self.status not in DELIVERY_STATUSES:
            raise ValueError(
                f"unknown delivery status {self.status!r} — expected one of "
                f"{', '.join(DELIVERY_STATUSES)}"
            )


@runtime_checkable
class DeliveryProvider(Protocol):
    """A transport a product supplies. One method, plus a name for the receipt."""

    @property
    def name(self) -> str:
        """Stable provider id, recorded on every receipt (`ses`, `twilio`, …)."""

    def send(self, message: OutboundMessage) -> ProviderResult: ...


@dataclass(frozen=True, slots=True)
class Suppressed:
    """The send did not happen because consent said no."""

    reason: str

    sent = False


@dataclass(frozen=True, slots=True)
class Sent:
    """The provider was called, and this is its receipt."""

    receipt: CommunicationDelivery

    sent = True

    @property
    def status(self) -> str:
        return self.receipt.status


def send(
    db: Session,
    tenant_id: UUID,
    *,
    provider: DeliveryProvider,
    message: OutboundMessage,
) -> Sent | Suppressed:
    """Send one message, asking consent first and recording what came back.

    Returns `Suppressed` without calling the provider when the address is
    suppressed for this category — no network call, no receipt, nothing to
    retry. Returns `Sent` otherwise, carrying the receipt; a `bounced` or
    `complaint` receipt will already have suppressed the address by the time
    this returns.

    Flush-only: the receipt and any suppression it triggers belong to the
    caller's transaction.
    """
    fingerprint = message.request_fingerprint
    existing = delivery.latest_receipt_for_dispatch(
        db, tenant_id, dispatch_id=message.dispatch_id
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise delivery.DeliveryError(
                "dispatch id was already used for a different message"
            )
        return Sent(receipt=existing)

    reason = consent.suppression_reason(
        db,
        tenant_id,
        channel=message.channel,
        address=message.address,
        category=message.category,
    )
    if reason is not None:
        return Suppressed(reason=reason)

    result = provider.send(message)
    receipt = delivery.record_receipt(
        db,
        tenant_id,
        channel=message.channel,
        address=message.address,
        provider=provider.name,
        status=result.status,
        dispatch_id=message.dispatch_id,
        request_fingerprint=fingerprint,
        provider_message_id=result.provider_message_id,
        response_code=result.response_code,
        response_body=result.response_body,
    )
    return Sent(receipt=receipt)


__all__ = [
    "DeliveryProvider",
    "OutboundMessage",
    "ProviderResult",
    "Sent",
    "Suppressed",
    "send",
]
