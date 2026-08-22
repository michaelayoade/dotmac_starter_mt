"""Service-delivery readiness decisions; callers own authorization and transactions.

Ported from `dotmac_sub`'s `app/services/provisioning_lifecycle.py`, with the
one change the module boundary forces: Sub reads Projects, Project Tasks, Work
Orders and IP Assignments directly to build its checks. Those are other owners'
facts, so here the CALLER normalizes them into `ReadinessCheck` observations and
the module owns only the decision made from them. The decision rule itself is
Sub's, unchanged:

* a failed DELIVERY_RUN is terminal — the order FAILED;
* any other failed check BLOCKS, carrying the first failure's reason code;
* all checks passing or not applying requests activation;
* and every one of those requires an in-flight order, because a decision about
  a draft or a settled order describes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_service_orders.contracts import (
    ConfirmActivation,
    Conflict,
    DecideReadiness,
    OpenServiceOrder,
    ReadinessCheck,
    ReadinessCheckKind,
    ReadinessCheckResult,
    ReadinessDecisionStatus,
    ServiceOrderStatus,
)
from dotmac_service_orders.models import (
    ServiceOrder,
    ServiceOrderReadinessCheck,
    ServiceOrderReadinessDecision,
)

# An order is "in flight" exactly while delivery work can still change its
# outcome. Sub refuses a readiness decision outside this window; so do we.
_IN_FLIGHT = ServiceOrderStatus.IN_DELIVERY
_SETTLED = frozenset(
    {
        ServiceOrderStatus.ACTIVATED,
        ServiceOrderStatus.CANCELLED,
        ServiceOrderStatus.FAILED,
    }
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-service-orders requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _order(db: Session, tenant_id: UUID, service_order_id: UUID) -> ServiceOrder:
    row = db.scalar(
        select(ServiceOrder).where(
            ServiceOrder.tenant_id == tenant_id, ServiceOrder.id == service_order_id
        )
    )
    if row is None:
        raise Conflict("service order was not found in the tenant")
    return row


def _decision_for_command(
    db: Session, tenant_id: UUID, command_id: UUID
) -> ServiceOrderReadinessDecision | None:
    return db.scalar(
        select(ServiceOrderReadinessDecision).where(
            ServiceOrderReadinessDecision.tenant_id == tenant_id,
            ServiceOrderReadinessDecision.command_id == command_id,
        )
    )


def _append(
    db: Session,
    *,
    tenant_id: UUID,
    order: ServiceOrder,
    command_id: UUID,
    correlation_id: UUID,
    actor: str,
    status: ReadinessDecisionStatus,
    reason_code: str,
    checks: tuple[ReadinessCheck, ...],
    decided_at: datetime,
) -> ServiceOrderReadinessDecision:
    decision = ServiceOrderReadinessDecision(
        tenant_id=tenant_id,
        service_order_id=order.id,
        command_id=command_id,
        correlation_id=correlation_id,
        status=status,
        reason_code=reason_code,
        actor=actor,
        decided_at=decided_at,
    )
    db.add(decision)
    db.flush()
    for check in checks:
        db.add(
            ServiceOrderReadinessCheck(
                tenant_id=tenant_id,
                decision_id=decision.id,
                kind=check.kind,
                result=check.result,
                reason_code=_required(check.reason_code, "check reason code"),
                source_type=_required(check.source_type, "check source type"),
                source_reference=check.source_reference,
                observed_at=decided_at,
            )
        )
    db.flush()
    return decision


def open_service_order(
    db: Session, *, scope: TenantScope, command: OpenServiceOrder
) -> ServiceOrder:
    """Open a delivery order, idempotently on `request_key`.

    Returning the existing order rather than raising is what makes a retried
    submission safe: Sub carries the same rule on `idempotency_key`, and a
    caller that cannot tell "already opened" from "conflict" opens duplicates.
    """
    tenant_id = _tenant(scope)
    request_key = _required(command.request_key, "request key")
    existing = db.scalar(
        select(ServiceOrder).where(
            ServiceOrder.tenant_id == tenant_id,
            ServiceOrder.request_key == request_key,
        )
    )
    if existing is not None:
        if existing.order_type != command.order_type:
            raise Conflict("request key was reused for a different order type")
        if existing.customer_reference != command.customer_reference.strip():
            raise Conflict("request key was reused for a different customer")
        return existing
    row = ServiceOrder(
        tenant_id=tenant_id,
        customer_reference=_required(command.customer_reference, "customer reference"),
        commercial_order_reference=command.commercial_order_reference,
        specification_reference=command.specification_reference,
        service_reference=command.service_reference,
        request_key=request_key,
        order_type=command.order_type,
        status=ServiceOrderStatus.DRAFT,
        opened_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def submit_service_order(
    db: Session, *, scope: TenantScope, service_order_id: UUID
) -> ServiceOrder:
    order = _order(db, _tenant(scope), service_order_id)
    if order.status is not ServiceOrderStatus.DRAFT:
        raise Conflict("only a draft service order can be submitted")
    order.status = ServiceOrderStatus.SUBMITTED
    db.flush()
    return order


def begin_delivery(
    db: Session, *, scope: TenantScope, service_order_id: UUID
) -> ServiceOrder:
    order = _order(db, _tenant(scope), service_order_id)
    if order.status is not ServiceOrderStatus.SUBMITTED:
        raise Conflict("only a submitted service order can enter delivery")
    order.status = ServiceOrderStatus.IN_DELIVERY
    db.flush()
    return order


def cancel_service_order(
    db: Session, *, scope: TenantScope, service_order_id: UUID
) -> ServiceOrder:
    order = _order(db, _tenant(scope), service_order_id)
    if order.status in _SETTLED:
        raise Conflict("a settled service order cannot be cancelled")
    order.status = ServiceOrderStatus.CANCELLED
    order.settled_at = datetime.now(UTC)
    db.flush()
    return order


def decide_readiness(
    db: Session, *, scope: TenantScope, command: DecideReadiness
) -> ServiceOrderReadinessDecision:
    tenant_id = _tenant(scope)
    replay = _decision_for_command(db, tenant_id, command.command_id)
    if replay is not None:
        if replay.service_order_id != command.service_order_id:
            raise Conflict("command id was replayed against a different service order")
        return replay

    order = _order(db, tenant_id, command.service_order_id)
    if order.status is not _IN_FLIGHT:
        raise Conflict("readiness can only be decided for an in-flight service order")
    if not command.checks:
        raise Conflict("a readiness decision needs at least one check")
    kinds = [check.kind for check in command.checks]
    if len(kinds) != len(set(kinds)):
        raise Conflict("a readiness decision carries at most one check per kind")

    decided_at = command.decided_at or datetime.now(UTC)
    failed = tuple(
        check for check in command.checks if check.result is ReadinessCheckResult.FAILED
    )
    delivery_run_failed = any(
        check.kind is ReadinessCheckKind.DELIVERY_RUN for check in failed
    )
    if delivery_run_failed:
        status = ReadinessDecisionStatus.FAILED
        reason_code = next(
            check.reason_code
            for check in failed
            if check.kind is ReadinessCheckKind.DELIVERY_RUN
        )
    elif failed:
        status = ReadinessDecisionStatus.BLOCKED
        reason_code = failed[0].reason_code
    else:
        status = ReadinessDecisionStatus.ACTIVATION_REQUESTED
        reason_code = "activation_requested"

    decision = _append(
        db,
        tenant_id=tenant_id,
        order=order,
        command_id=command.command_id,
        correlation_id=command.correlation_id,
        actor=_required(command.actor, "actor"),
        status=status,
        reason_code=reason_code,
        checks=command.checks,
        decided_at=decided_at,
    )
    if status is ReadinessDecisionStatus.FAILED:
        order.status = ServiceOrderStatus.FAILED
        order.settled_at = decided_at
        db.flush()
    return decision


def latest_readiness(
    db: Session, *, scope: TenantScope, service_order_id: UUID
) -> ServiceOrderReadinessDecision | None:
    return db.scalar(
        select(ServiceOrderReadinessDecision)
        .where(
            ServiceOrderReadinessDecision.tenant_id == _tenant(scope),
            ServiceOrderReadinessDecision.service_order_id == service_order_id,
        )
        .order_by(ServiceOrderReadinessDecision.decided_at.desc())
        .limit(1)
    )


def confirm_activation(
    db: Session, *, scope: TenantScope, command: ConfirmActivation
) -> ServiceOrderReadinessDecision:
    """Confirm the exact order whose activation this module requested.

    The precondition is the point: an activation nobody requested cannot be
    confirmed. Sub refuses it with `No readiness decision requested this
    activation`, and confirming without that check is how an order becomes
    active on the strength of an unrelated projection succeeding.
    """
    tenant_id = _tenant(scope)
    replay = _decision_for_command(db, tenant_id, command.command_id)
    if replay is not None:
        if replay.service_order_id != command.service_order_id:
            raise Conflict("command id was replayed against a different service order")
        return replay

    order = _order(db, tenant_id, command.service_order_id)
    latest = latest_readiness(db, scope=scope, service_order_id=order.id)
    requested = ReadinessDecisionStatus.ACTIVATION_REQUESTED
    if latest is None or latest.status is not requested:
        raise Conflict("no readiness decision requested this activation")
    if order.status is not _IN_FLIGHT:
        raise Conflict("only an in-flight service order can be activated")

    decided_at = command.decided_at or datetime.now(UTC)
    decision = _append(
        db,
        tenant_id=tenant_id,
        order=order,
        command_id=command.command_id,
        correlation_id=command.correlation_id,
        actor=_required(command.actor, "actor"),
        status=ReadinessDecisionStatus.ACTIVATED,
        reason_code="activation_confirmed",
        checks=(),
        decided_at=decided_at,
    )
    order.status = ServiceOrderStatus.ACTIVATED
    order.settled_at = decided_at
    db.flush()
    return decision


__all__ = [
    "begin_delivery",
    "cancel_service_order",
    "confirm_activation",
    "decide_readiness",
    "latest_readiness",
    "open_service_order",
    "submit_service_order",
]
