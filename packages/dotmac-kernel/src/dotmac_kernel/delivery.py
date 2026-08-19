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
`(tenant, provider, provider_message_id, status)`: a redelivered bounce returns
the existing bounce, while a later status for the same message is preserved. A
receipt with no provider id — a synchronous send failure that never got one — is
always recorded, since there is nothing to deduplicate on.

## Transactions

Flush-only; `dotmac_kernel.db` is the one transaction authority. The receipt and
any suppression it triggers land in the SAME transaction, so a crash cannot leave
a recorded bounce with no suppression behind it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel import consent
from dotmac_kernel._transactions import conflict_savepoint
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


def _provider_name(provider: str | None) -> str:
    return (provider or "").strip().lower()


def _provider_receipt(
    db: Session,
    tenant_id: UUID,
    *,
    provider: str,
    provider_message_id: str,
    status: str,
) -> CommunicationDelivery | None:
    return db.execute(
        select(CommunicationDelivery).where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.provider == provider,
            CommunicationDelivery.provider_message_id == provider_message_id,
            CommunicationDelivery.status == status,
        )
    ).scalar_one_or_none()


def _latest_provider_receipt(
    db: Session,
    tenant_id: UUID,
    *,
    provider: str,
    provider_message_id: str,
) -> CommunicationDelivery | None:
    return db.execute(
        select(CommunicationDelivery)
        .where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.provider == provider,
            CommunicationDelivery.provider_message_id == provider_message_id,
        )
        .order_by(
            CommunicationDelivery.occurred_at.desc(),
            CommunicationDelivery.created_at.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _assert_same_message(
    receipt: CommunicationDelivery,
    *,
    channel: str,
    address: str,
    dispatch_id: UUID | None,
    request_fingerprint: str | None,
) -> None:
    if receipt.channel != channel or receipt.address != address:
        raise DeliveryError(
            "provider message id and status were already recorded for a "
            "different channel or address"
        )
    if dispatch_id is not None and receipt.dispatch_id != dispatch_id:
        raise DeliveryError("dispatch id conflicts with the recorded provider message")
    if (
        request_fingerprint is not None
        and receipt.request_fingerprint is not None
        and receipt.request_fingerprint != request_fingerprint
    ):
        raise DeliveryError("dispatch id was already used for a different message")


def record_receipt(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str,
    provider: str,
    status: str,
    dispatch_id: UUID | None = None,
    request_fingerprint: str | None = None,
    provider_message_id: str | None = None,
    response_code: str | None = None,
    response_body: str | None = None,
    occurred_at: datetime | None = None,
) -> CommunicationDelivery:
    """Record what a provider said, and act on it if it is final.

    Idempotent on `(tenant, provider, provider_message_id, status)` when an id
    is supplied. Distinct statuses for one provider message share a dispatch id
    and remain distinct rows. A `bounced` or `complaint` status additionally
    suppresses the address with scope `all` — see the module docstring for why a
    soft bounce must not be reported as `bounced`.
    """
    if status not in DELIVERY_STATUSES:
        raise DeliveryError(
            f"unknown delivery status {status!r} — expected one of "
            f"{', '.join(DELIVERY_STATUSES)}"
        )
    normalized_channel = consent.normalize_channel(channel)
    if not normalized_channel:
        raise DeliveryError("cannot record a receipt for an empty channel")
    normalized = consent.normalize_address(normalized_channel, address)
    if not normalized:
        raise DeliveryError("cannot record a receipt for an empty address")
    normalized_provider = _provider_name(provider)
    if not normalized_provider:
        raise DeliveryError("cannot record a receipt for an empty provider")
    normalized_message_id = (
        provider_message_id.strip() if provider_message_id is not None else None
    )
    if normalized_message_id == "":
        normalized_message_id = None

    prior: CommunicationDelivery | None = None
    if normalized_message_id is not None:
        existing = _provider_receipt(
            db,
            tenant_id,
            provider=normalized_provider,
            provider_message_id=normalized_message_id,
            status=status,
        )
        if existing is not None:
            _assert_same_message(
                existing,
                channel=normalized_channel,
                address=normalized,
                dispatch_id=dispatch_id,
                request_fingerprint=request_fingerprint,
            )
            return existing
        prior = _latest_provider_receipt(
            db,
            tenant_id,
            provider=normalized_provider,
            provider_message_id=normalized_message_id,
        )
        if prior is not None:
            _assert_same_message(
                prior,
                channel=normalized_channel,
                address=normalized,
                dispatch_id=dispatch_id,
                request_fingerprint=request_fingerprint,
            )

    resolved_dispatch_id = dispatch_id or (prior.dispatch_id if prior else uuid4())
    resolved_fingerprint = request_fingerprint or (
        prior.request_fingerprint if prior else None
    )

    receipt = CommunicationDelivery(
        tenant_id=tenant_id,
        dispatch_id=resolved_dispatch_id,
        request_fingerprint=resolved_fingerprint,
        channel=normalized_channel,
        address=normalized,
        provider=normalized_provider,
        provider_message_id=normalized_message_id,
        status=status,
        response_code=response_code,
        response_body=response_body,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    try:
        with conflict_savepoint(db):
            db.add(receipt)
            db.flush()
    except IntegrityError:
        if normalized_message_id is None:
            raise
        winner = _provider_receipt(
            db,
            tenant_id,
            provider=normalized_provider,
            provider_message_id=normalized_message_id,
            status=status,
        )
        if winner is None:
            raise
        _assert_same_message(
            winner,
            channel=normalized_channel,
            address=normalized,
            dispatch_id=dispatch_id,
            request_fingerprint=request_fingerprint,
        )
        return winner

    if status in SUPPRESSING_STATUSES:
        # Same transaction as the receipt: a crash must not be able to leave a
        # recorded bounce with no suppression behind it.
        consent.suppress(
            db,
            tenant_id,
            channel=normalized_channel,
            address=address,
            scope=SCOPE_ALL,
            reason=_REASON_FOR_STATUS.get(status, REASON_COMPLAINT),
            note=f"provider {normalized_provider} reported {status}"
            + (f" ({response_code})" if response_code else ""),
            created_by=f"delivery:{normalized_provider}",
        )
    return receipt


def latest_receipt_for_dispatch(
    db: Session, tenant_id: UUID, *, dispatch_id: UUID
) -> CommunicationDelivery | None:
    """Newest known provider status for one stable outbound dispatch."""
    return db.execute(
        select(CommunicationDelivery)
        .where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.dispatch_id == dispatch_id,
        )
        .order_by(
            CommunicationDelivery.occurred_at.desc(),
            CommunicationDelivery.created_at.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


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
    normalized_channel = consent.normalize_channel(channel)
    stmt = (
        select(CommunicationDelivery)
        .where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.channel == normalized_channel,
            CommunicationDelivery.address
            == consent.normalize_address(normalized_channel, address),
        )
        .order_by(CommunicationDelivery.occurred_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


__all__ = [
    "DeliveryError",
    "latest_receipt_for_dispatch",
    "receipts_for_address",
    "record_receipt",
]
