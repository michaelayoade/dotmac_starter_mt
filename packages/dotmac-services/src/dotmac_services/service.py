"""Service lifecycle; callers own authorization and transactions."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_services.contracts import (
    Conflict,
    CreateService,
    ServiceStatus,
    TransitionService,
)
from dotmac_services.models import ServiceInstance, ServiceLifecycleEvent

_TRANSITIONS: dict[ServiceStatus, frozenset[ServiceStatus]] = {
    ServiceStatus.ORDERED: frozenset({ServiceStatus.ACTIVE, ServiceStatus.TERMINATED}),
    ServiceStatus.ACTIVE: frozenset(
        {ServiceStatus.SUSPENDED, ServiceStatus.TERMINATED}
    ),
    ServiceStatus.SUSPENDED: frozenset(
        {ServiceStatus.ACTIVE, ServiceStatus.TERMINATED}
    ),
    ServiceStatus.TERMINATED: frozenset(),
}


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-services requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def create_service(
    db: Session, *, scope: TenantScope, command: CreateService
) -> ServiceInstance:
    qualification = command.qualification_reference
    row = ServiceInstance(
        tenant_id=_tenant(scope),
        customer_reference=_required(command.customer_reference, "customer reference"),
        specification_reference=_required(
            command.specification_reference, "specification reference"
        ),
        qualification_reference=(
            _required(qualification, "qualification reference")
            if qualification is not None
            else None
        ),
        status=ServiceStatus.ORDERED,
    )
    db.add(row)
    db.flush()
    return row


def transition_service(
    db: Session, *, scope: TenantScope, command: TransitionService
) -> ServiceLifecycleEvent:
    tenant_id = _tenant(scope)
    row = db.scalar(
        select(ServiceInstance).where(
            ServiceInstance.tenant_id == tenant_id,
            ServiceInstance.id == command.service_id,
        )
    )
    if row is None:
        raise Conflict("service was not found in the tenant")
    if command.to_status not in _TRANSITIONS[row.status]:
        raise Conflict(
            f"transition from {row.status} to {command.to_status} is invalid"
        )
    event = ServiceLifecycleEvent(
        tenant_id=tenant_id,
        service_id=row.id,
        from_status=row.status,
        to_status=command.to_status,
        occurred_at=command.occurred_at,
        reason=_required(command.reason, "transition reason"),
    )
    row.status = command.to_status
    if command.to_status == ServiceStatus.ACTIVE and row.activated_at is None:
        row.activated_at = command.occurred_at
    if command.to_status == ServiceStatus.TERMINATED:
        row.terminated_at = command.occurred_at
    db.add(event)
    db.flush()
    return event


__all__ = ["create_service", "transition_service"]
