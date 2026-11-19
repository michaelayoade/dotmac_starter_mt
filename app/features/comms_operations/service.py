"""Operator view over the consent ledger and delivery receipts (ADR-0006 § 5c/5g, K-7).

**Owner of:** *an operator can see why an address stopped receiving mail, and,
on a real business case, clear that suppression with a recorded reason.*

This is a thin layer over kernel primitives; it owns no table and adds no
authority the kernel did not already expose:

- reads: the tenant's `communication_suppressions` (filter by channel, scope,
  address) and `dotmac_kernel.delivery.receipts_for_address`;
- the one mutation: `clear_suppression` = `consent.unsuppress` plus ONE
  `communication.suppression.cleared` audit row in the same transaction.

Every query carries an explicit `tenant_id` predicate on top of RLS, so a bug
in the session scope cannot widen a read. The audit row never carries the
address (PII); it carries the suppression id and a short digest of the
normalised address, enough to correlate with the receipts without storing it.

Flush-only: the caller (`dotmac_kernel.db`) owns the transaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from dotmac_kernel import consent, delivery
from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.consent_models import (
    SUPPRESSION_SCOPES,
    CommunicationSuppression,
)
from dotmac_kernel.delivery_models import CommunicationDelivery
from dotmac_kernel.exceptions import BadRequestError, NotFoundError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

AUDIT_ACTION_CLEARED = "communication.suppression.cleared"
AUDIT_ENTITY_TYPE = "communication_suppression"
MAX_REASON_LENGTH = 500
RECEIPTS_LIMIT = 50


@dataclass(frozen=True, slots=True)
class SuppressionRow:
    id: UUID
    channel: str
    address: str
    scope: str
    reason: str
    note: str | None
    created_at: object
    created_by: str | None


def _view(row: CommunicationSuppression) -> SuppressionRow:
    return SuppressionRow(
        id=row.id,
        channel=row.channel,
        address=row.address,
        scope=row.scope,
        reason=row.reason,
        note=row.note,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _filters(
    tenant_id: UUID, channel: str | None, scope: str | None, address: str | None
) -> list:
    clauses = [CommunicationSuppression.tenant_id == tenant_id]
    if channel:
        clauses.append(
            CommunicationSuppression.channel == consent.normalize_channel(channel)
        )
    if scope:
        if scope not in SUPPRESSION_SCOPES:
            raise BadRequestError(f"unknown scope {scope!r}")
        clauses.append(CommunicationSuppression.scope == scope)
    if address and address.strip():
        needle = _escape_like(address.strip().lower())
        clauses.append(
            func.lower(CommunicationSuppression.address).like(
                f"%{needle}%", escape="\\"
            )
        )
    return clauses


def list_suppressions(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str | None = None,
    scope: str | None = None,
    address: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[SuppressionRow]:
    rows = db.execute(
        select(CommunicationSuppression)
        .where(*_filters(tenant_id, channel, scope, address))
        .order_by(CommunicationSuppression.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars()
    return [_view(row) for row in rows]


def count_suppressions(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str | None = None,
    scope: str | None = None,
    address: str | None = None,
) -> int:
    return db.execute(
        select(func.count())
        .select_from(CommunicationSuppression)
        .where(*_filters(tenant_id, channel, scope, address))
    ).scalar_one()


def get_suppression(
    db: Session, tenant_id: UUID, *, channel: str, address: str
) -> SuppressionRow | None:
    normalized_channel = consent.normalize_channel(channel)
    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address
            == consent.normalize_address(normalized_channel, address),
        )
    ).scalar_one_or_none()
    return None if row is None else _view(row)


@dataclass(frozen=True, slots=True)
class ReceiptRow:
    id: UUID
    dispatch_id: UUID
    provider: str
    provider_message_id: str | None
    status: str
    response_code: str | None
    occurred_at: object


def list_receipts(
    db: Session, tenant_id: UUID, *, channel: str, address: str
) -> list[ReceiptRow]:
    """Recent receipts for one address, newest first (no bodies, no payloads)."""
    rows = delivery.receipts_for_address(
        db, tenant_id, channel=channel, address=address, limit=RECEIPTS_LIMIT
    )
    return [_receipt_view(row) for row in rows if row.tenant_id == tenant_id]


def _receipt_view(row: CommunicationDelivery) -> ReceiptRow:
    return ReceiptRow(
        id=row.id,
        dispatch_id=row.dispatch_id,
        provider=row.provider,
        provider_message_id=row.provider_message_id,
        status=row.status,
        response_code=row.response_code,
        occurred_at=row.occurred_at,
    )


def clear_suppression(
    db: Session,
    tenant_id: UUID,
    *,
    channel: str,
    address: str,
    reason: str,
    actor_party_id: UUID,
) -> bool:
    """Clear one suppression and audit it. True if a suppression was cleared.

    Validates the reason and the declared audit action BEFORE changing state
    (the audit table is append-only; a failed write could not be amended). A
    missing suppression is a no-op returning False: no audit row, since nothing
    changed.
    """
    active_audit_actions().require(AUDIT_ACTION_CLEARED)
    cleaned = (reason or "").strip()
    if not cleaned:
        raise BadRequestError("clearing a suppression needs a reason")
    if len(cleaned) > MAX_REASON_LENGTH:
        raise BadRequestError(f"reason exceeds {MAX_REASON_LENGTH} characters")

    normalized_channel = consent.normalize_channel(channel)
    normalized = consent.normalize_address(normalized_channel, address)
    existing = (
        db.execute(
            select(CommunicationSuppression)
            .where(
                CommunicationSuppression.tenant_id == tenant_id,
                CommunicationSuppression.channel == normalized_channel,
                CommunicationSuppression.address == normalized,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if normalized
        else None
    )
    if existing is None:
        return False
    details: dict[str, object] = {
        "reason": cleaned,
        "channel": existing.channel,
        "scope": existing.scope,
        "suppression_reason": existing.reason,
        "address_sha256": hashlib.sha256(normalized.encode()).hexdigest()[:16],
    }
    suppression_id = existing.id
    if not consent.unsuppress(
        db, tenant_id, channel=normalized_channel, address=normalized
    ):
        raise NotFoundError("suppression disappeared during clear")
    write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_party_id=actor_party_id,
        actor_type="user",
        actor_id=str(actor_party_id),
        action=AUDIT_ACTION_CLEARED,
        entity_type=AUDIT_ENTITY_TYPE,
        entity_id=str(suppression_id),
        is_success=True,
        details=details,
    )
    return True


__all__ = [
    "AUDIT_ACTION_CLEARED",
    "MAX_REASON_LENGTH",
    "ReceiptRow",
    "SuppressionRow",
    "clear_suppression",
    "count_suppressions",
    "get_suppression",
    "list_receipts",
    "list_suppressions",
]
