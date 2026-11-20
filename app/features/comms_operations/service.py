"""Operator view over the consent ledger and delivery receipts (ADR-0006 § 5c/5g, K-7).

**Owner of:** *an operator can see why an address stopped receiving mail and,
on a real business case, restore transactional mail with a recorded reason.*

This is a thin layer over kernel primitives; it owns no table:

- reads: the tenant's `communication_suppressions` (filter by channel, scope,
  address) and `dotmac_kernel.delivery.receipts_for_address`;
- the one mutation: `restore_transactional_mail` = the kernel's
  `consent.restore_transactional` (scope `all` -> `marketing`; the row, its
  reason and provenance are KEPT, marketing stays blocked, never a delete) plus
  ONE `communication.suppression.transactional_restored` audit row in the same
  transaction. An erasure row and a marketing opt-out are refused before any
  change (`RestoreRefused`).

Every query carries an explicit `tenant_id` predicate on top of RLS. The audit
row never carries the address; the retained suppression id identifies the row.
Messages suppressed earlier are NOT revived: the customer must request again.

Flush-only: the caller (`dotmac_kernel.db`) owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.consent import (
    normalize_address,
    normalize_channel,
    restore_transactional,
)
from dotmac_kernel.consent_models import (
    REASON_ERASURE,
    SCOPE_ALL,
    SUPPRESSION_SCOPES,
    CommunicationSuppression,
)
from dotmac_kernel.delivery import receipts_for_address
from dotmac_kernel.delivery_models import CommunicationDelivery
from dotmac_kernel.exceptions import BadRequestError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

AUDIT_ACTION_RESTORED = "communication.suppression.transactional_restored"
AUDIT_ENTITY_TYPE = "communication_suppression"
MAX_REASON_LENGTH = 500
RECEIPTS_LIMIT = 50


class RestoreRefused(BadRequestError):
    """The suppression may not be restored. Nothing was changed."""


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

    @property
    def can_restore(self) -> bool:
        """Only a transactional block on a non-erasure row can be lifted."""
        return self.scope == SCOPE_ALL and self.reason != REASON_ERASURE


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
        clauses.append(CommunicationSuppression.channel == normalize_channel(channel))
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
    normalized_channel = normalize_channel(channel)
    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.channel == normalized_channel,
            CommunicationSuppression.address
            == normalize_address(normalized_channel, address),
        )
    ).scalar_one_or_none()
    return None if row is None else _view(row)


def get_suppression_by_id(
    db: Session, tenant_id: UUID, suppression_id: UUID
) -> SuppressionRow | None:
    row = db.execute(
        select(CommunicationSuppression).where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.id == suppression_id,
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
    rows = receipts_for_address(
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


def restore_transactional_mail(
    db: Session,
    tenant_id: UUID,
    *,
    suppression_id: UUID,
    reason: str,
    actor_party_id: UUID,
) -> bool:
    """Restore transactional mail for one suppression and audit it.

    Downgrades scope `all` -> `marketing` through the kernel's
    `restore_transactional`; the row, its reason and its provenance are kept
    and marketing stays blocked. Refuses (typed `RestoreRefused`, nothing
    changed, no audit row) an erasure row and a marketing-only row. Validates
    the reason and the declared audit action BEFORE changing state. Returns
    False (no audit row) when the suppression does not exist for this tenant.
    """
    active_audit_actions().require(AUDIT_ACTION_RESTORED)
    cleaned = (reason or "").strip()
    if not cleaned:
        raise RestoreRefused("restoring transactional mail needs a reason")
    if len(cleaned) > MAX_REASON_LENGTH:
        raise RestoreRefused(f"reason exceeds {MAX_REASON_LENGTH} characters")

    existing = db.execute(
        select(CommunicationSuppression)
        .where(
            CommunicationSuppression.tenant_id == tenant_id,
            CommunicationSuppression.id == suppression_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is None:
        return False
    if existing.reason == REASON_ERASURE:
        raise RestoreRefused("an erasure suppression is never lifted")
    if existing.scope != SCOPE_ALL:
        raise RestoreRefused(
            "this is a marketing opt-out; only the customer can lift it"
        )
    previous_scope = existing.scope
    updated = restore_transactional(
        db, tenant_id, channel=existing.channel, address=existing.address
    )
    if updated is None:
        return False
    write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_party_id=actor_party_id,
        actor_type="user",
        actor_id=str(actor_party_id),
        action=AUDIT_ACTION_RESTORED,
        entity_type=AUDIT_ENTITY_TYPE,
        entity_id=str(updated.id),
        is_success=True,
        details={
            "reason": cleaned,
            "channel": updated.channel,
            "previous_scope": previous_scope,
            "new_scope": updated.scope,
            "suppression_reason": updated.reason,
        },
    )
    return True


__all__ = [
    "AUDIT_ACTION_RESTORED",
    "MAX_REASON_LENGTH",
    "ReceiptRow",
    "SuppressionRow",
    "RestoreRefused",
    "get_suppression_by_id",
    "restore_transactional_mail",
    "count_suppressions",
    "get_suppression",
    "list_receipts",
    "list_suppressions",
]
