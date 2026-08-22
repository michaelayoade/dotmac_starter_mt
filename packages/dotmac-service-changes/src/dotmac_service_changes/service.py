"""Service-change requests; callers own authorization and transactions.

Ported from `dotmac_sub`'s `SubscriptionChangeRequest` and its execution
handlers. The module owns the durable customer request, the decision on it, the
evidence that each crossed owner reached a named point, and the ORDER those
points may be reached in. It owns none of the crossed domains: Qualification
still decides eligibility, Billing still raises the fee, Payments still confirms
it, Service Orders still delivers, Service Access still enforces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_service_changes.contracts import (
    EXECUTION_ORDER,
    AdvanceExecution,
    Conflict,
    DecideServiceChange,
    ExecutionState,
    OpenServiceChange,
    RecordCheckpoint,
    ServiceChangeStatus,
)
from dotmac_service_changes.models import (
    ServiceChangeCheckpoint,
    ServiceChangeRequest,
)

_TERMINAL = frozenset(
    {
        ServiceChangeStatus.REJECTED,
        ServiceChangeStatus.APPLIED,
        ServiceChangeStatus.CANCELLED,
    }
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-service-changes requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _request(db: Session, tenant_id: UUID, request_id: UUID) -> ServiceChangeRequest:
    row = db.scalar(
        select(ServiceChangeRequest).where(
            ServiceChangeRequest.tenant_id == tenant_id,
            ServiceChangeRequest.id == request_id,
        )
    )
    if row is None:
        raise Conflict("service change request was not found in the tenant")
    return row


def open_service_change(
    db: Session, *, scope: TenantScope, command: OpenServiceChange
) -> ServiceChangeRequest:
    """Open a change request, idempotently on its confirmation key.

    Sub carries the same rule as `uq_subscription_change_confirmation_idempotency`;
    a customer double-submitting a confirmed change must not open two.
    """
    tenant_id = _tenant(scope)
    confirmation_key = _required(command.confirmation_key, "confirmation key")
    existing = db.scalar(
        select(ServiceChangeRequest).where(
            ServiceChangeRequest.tenant_id == tenant_id,
            ServiceChangeRequest.confirmation_key == confirmation_key,
        )
    )
    if existing is not None:
        if existing.change_type != command.change_type:
            raise Conflict("confirmation key was reused for a different change type")
        if existing.subject_reference != command.subject_reference.strip():
            raise Conflict("confirmation key was reused for a different subject")
        return existing
    row = ServiceChangeRequest(
        tenant_id=tenant_id,
        subject_reference=_required(command.subject_reference, "subject reference"),
        confirmation_key=confirmation_key,
        change_type=command.change_type,
        status=ServiceChangeStatus.PENDING,
        execution_state=None,
        current_offer_reference=command.current_offer_reference,
        requested_offer_reference=command.requested_offer_reference,
        target_location_reference=command.target_location_reference,
        requested_at=datetime.now(UTC),
        effective_from=command.effective_from,
    )
    db.add(row)
    db.flush()
    return row


def decide_service_change(
    db: Session, *, scope: TenantScope, command: DecideServiceChange
) -> ServiceChangeRequest:
    request = _request(db, _tenant(scope), command.request_id)
    if request.status is not ServiceChangeStatus.PENDING:
        raise Conflict("only a pending service change can be decided")
    decided_at = command.decided_at or datetime.now(UTC)
    request.decided_at = decided_at
    request.decided_by = _required(command.actor, "actor")
    request.rationale = _required(command.rationale, "rationale")
    if not command.approve:
        request.status = ServiceChangeStatus.REJECTED
        request.settled_at = decided_at
        db.flush()
        return request
    request.status = ServiceChangeStatus.APPROVED
    request.execution_state = ExecutionState.AWAITING_PAYMENT
    db.flush()
    return request


def cancel_service_change(
    db: Session, *, scope: TenantScope, request_id: UUID
) -> ServiceChangeRequest:
    request = _request(db, _tenant(scope), request_id)
    if request.status in _TERMINAL:
        raise Conflict("a settled service change cannot be cancelled")
    request.status = ServiceChangeStatus.CANCELLED
    request.settled_at = datetime.now(UTC)
    db.flush()
    return request


def record_checkpoint(
    db: Session, *, scope: TenantScope, command: RecordCheckpoint
) -> ServiceChangeCheckpoint:
    """Record that one crossed owner reached a named point, with its evidence."""
    tenant_id = _tenant(scope)
    request = _request(db, tenant_id, command.request_id)
    if request.status in _TERMINAL:
        raise Conflict("a settled service change accepts no further evidence")
    if not command.facts:
        raise Conflict("checkpoint facts must not be empty")
    evidence_reference = _required(command.evidence_reference, "evidence reference")
    replay = db.scalar(
        select(ServiceChangeCheckpoint).where(
            ServiceChangeCheckpoint.tenant_id == tenant_id,
            ServiceChangeCheckpoint.request_id == request.id,
            ServiceChangeCheckpoint.domain == command.domain,
            ServiceChangeCheckpoint.evidence_reference == evidence_reference,
        )
    )
    if replay is not None:
        return replay
    row = ServiceChangeCheckpoint(
        tenant_id=tenant_id,
        request_id=request.id,
        domain=command.domain,
        evidence_reference=evidence_reference,
        facts=dict(command.facts),
        observed_at=command.observed_at or datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def advance_execution(
    db: Session, *, scope: TenantScope, command: AdvanceExecution
) -> ServiceChangeRequest:
    """Move exactly one step along the declared chain, or fail the request.

    Refusing a skipped step is the whole point: Sub's execution states were
    written by several handlers with no single guard, so a request could reach
    `fulfillment_released` without a settlement ever having been recorded.
    """
    request = _request(db, _tenant(scope), command.request_id)
    if request.status is not ServiceChangeStatus.APPROVED:
        raise Conflict("only an approved service change executes")
    current = request.execution_state
    if current is None:
        raise Conflict("service change has no execution state to advance")
    at = command.at or datetime.now(UTC)
    _required(command.reason_code, "reason code")

    if command.to_state is ExecutionState.FAILED:
        request.execution_state = ExecutionState.FAILED
        request.settled_at = at
        db.flush()
        return request
    if current in {ExecutionState.FAILED, ExecutionState.COMPLETED}:
        raise Conflict("service change execution has already finished")

    index = EXECUTION_ORDER.index(current)
    expected = EXECUTION_ORDER[index + 1]
    if command.to_state is not expected:
        raise Conflict(
            f"execution advances to {expected.value} from {current.value}, "
            f"not to {command.to_state.value}"
        )
    request.execution_state = command.to_state
    if command.to_state is ExecutionState.COMPLETED:
        request.status = ServiceChangeStatus.APPLIED
        request.settled_at = at
    db.flush()
    return request


__all__ = [
    "advance_execution",
    "cancel_service_change",
    "decide_service_change",
    "open_service_change",
    "record_checkpoint",
]
