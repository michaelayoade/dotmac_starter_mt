"""Delivery receipts, and the loop that keeps the consent ledger honest.

**Owner of:** *what did the provider say about this message, and what does that
oblige us to do?*

This module is small on purpose. The queue, the retries, the backoff and the
worker lease are NOT here: `dotmac_kernel.messaging` already owns them, and Sub's
notification queue turned out to be that same engine built a second time
(evidence: `docs/inventories/delivery-outbox-sources.md`). What was missing from
the kernel is the receipt — the outbox knows we dispatched, it does not know the
provider assigned id `X` and later said it bounced.

## The feedback loop

`record_receipt` is the only writer, and a `bounced` or `complaint` verdict makes
it suppress the address with scope `all`.

That loop does not exist in either product, and its absence is why consent alone
is not enough. Verified in `dotmac_sub` at `5d6f115b7`: `DeliveryStatus.bounced`
is declared and never assigned, `SuppressionReason.bounce`/`.complaint` have zero
call sites, and exactly one site in the product writes a suppression — the
campaign unsubscribe link. Sub's ledger is unsubscribe-only in practice, so the
`all` scope that protects transactional delivery is never populated by anything
automated. **A consent ledger nothing writes to answers "yes, send" forever.**

## Where the judgement belongs, and the trap in it

Only `bounced` and `complaint` suppress. A SOFT bounce — mailbox full, greylisted,
server temporarily unavailable — must be recorded as `failed`, never `bounced`,
or a full inbox permanently stops that customer's invoices.

The kernel cannot make that call: "5.1.1 user unknown" is permanent and "4.2.2
mailbox full" is not, and every provider spells them differently. So the ADAPTER
classifies and this module acts on the classification. That boundary is the whole
reason `status` is a caller-supplied vocabulary rather than something parsed from
`response_code` here.

## Idempotency

Provider webhooks are at-least-once. `record_receipt` is idempotent on
`(tenant, provider, provider_message_id)`: a redelivered bounce returns the
existing row and does not suppress twice. A receipt with no provider id — a
synchronous send failure that never got one — is always recorded, since there is
nothing to deduplicate on.

## Transactions

Flush-only; `dotmac_kernel.db` is the one transaction authority. The receipt and
any suppression it triggers land in the SAME transaction, so a crash cannot leave
a recorded bounce with no suppression behind it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel import consent
from dotmac_kernel.consent_models import (
    REASON_BOUNCE,
    REASON_COMPLAINT,
    SCOPE_ALL,
)
from dotmac_kernel.delivery_models import (
    DELIVERY_BOUNCED,
    DELIVERY_STATUSES,
    SUPPRESSING_STATUSES,
    CommunicationDelivery,
)


class DeliveryError(ValueError):
    """A malformed receipt — never a delivery outcome."""


#: Which suppression reason a suppressing verdict records.
_REASON_FOR_STATUS = {
    DELIVERY_BOUNCED: REASON_BOUNCE,
}


def record_receipt(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str,
    provider: str,
    status: str,
    provider_message_id: str | None = None,
    response_code: str | None = None,
    response_body: str | None = None,
    occurred_at: datetime | None = None,
) -> CommunicationDelivery:
    """Record what a provider said, and act on it if it is final.

    Idempotent on `(tenant, provider, provider_message_id)` when an id is
    supplied. A `bounced` or `complaint` status additionally suppresses the
    address with scope `all` — see the module docstring for why a soft bounce
    must not be reported as `bounced`.
    """
    if status not in DELIVERY_STATUSES:
        raise DeliveryError(
            f"unknown delivery status {status!r} — expected one of "
            f"{', '.join(DELIVERY_STATUSES)}"
        )
    normalized = consent.normalize_address(channel, address)
    if not normalized:
        raise DeliveryError("cannot record a receipt for an empty address")

    if provider_message_id is not None:
        existing = db.execute(
            select(CommunicationDelivery).where(
                CommunicationDelivery.tenant_id == tenant_id,
                CommunicationDelivery.provider == provider,
                CommunicationDelivery.provider_message_id == provider_message_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # A redelivered webhook. Returning the first row rather than writing
            # a second is what keeps one bounce from suppressing twice.
            return existing

    receipt = CommunicationDelivery(
        tenant_id=tenant_id,
        channel=channel,
        address=normalized,
        provider=provider,
        provider_message_id=provider_message_id,
        status=status,
        response_code=response_code,
        response_body=response_body,
        **({"occurred_at": occurred_at} if occurred_at is not None else {}),
    )
    db.add(receipt)
    db.flush()

    if status in SUPPRESSING_STATUSES:
        # Same transaction as the receipt: a crash must not be able to leave a
        # recorded bounce with no suppression behind it.
        consent.suppress(
            db,
            tenant_id,
            channel=channel,
            address=address,
            scope=SCOPE_ALL,
            reason=_REASON_FOR_STATUS.get(status, REASON_COMPLAINT),
            note=f"provider {provider} reported {status}"
            + (f" ({response_code})" if response_code else ""),
            created_by=f"delivery:{provider}",
        )
    return receipt


def receipts_for_address(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str,
    limit: int = 50,
) -> list[CommunicationDelivery]:
    """Everything a provider has said about one address, newest first.

    The operator question behind a "why did this customer stop getting mail?"
    ticket.
    """
    stmt = (
        select(CommunicationDelivery)
        .where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.channel == channel,
            CommunicationDelivery.address
            == consent.normalize_address(channel, address),
        )
        .order_by(CommunicationDelivery.occurred_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


__all__ = [
    "DeliveryError",
    "receipts_for_address",
    "record_receipt",
]
