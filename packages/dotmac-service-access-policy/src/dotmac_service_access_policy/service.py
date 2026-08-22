"""Desired service-access policy; callers own authorization and transactions."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_service_access_policy.contracts import (
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    ResolveDesiredAccess,
)
from dotmac_service_access_policy.models import (
    DesiredAccessDecision,
    ServiceAccessInput,
)

_DENY_PRECEDENCE = (
    AccessSignal.ADMIN_HOLD,
    AccessSignal.COLLECTIONS_HOLD,
    AccessSignal.PREPAID_DEPLETED,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-service-access-policy requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def record_access_input(
    db: Session, *, scope: TenantScope, command: RecordAccessInput
) -> ServiceAccessInput:
    tenant_id = _tenant(scope)
    service = _required(command.service_reference, "service reference")
    row = db.scalar(
        select(ServiceAccessInput).where(
            ServiceAccessInput.tenant_id == tenant_id,
            ServiceAccessInput.service_reference == service,
            ServiceAccessInput.signal == command.signal,
        )
    )
    if row is None:
        row = ServiceAccessInput(
            tenant_id=tenant_id,
            service_reference=service,
            signal=command.signal,
            active=command.active,
            source_reference=_required(command.source_reference, "source reference"),
            observed_at=command.observed_at,
        )
        db.add(row)
    else:
        row.active = command.active
        row.source_reference = _required(command.source_reference, "source reference")
        row.observed_at = command.observed_at
    db.flush()
    return row


def resolve_desired_access(
    db: Session, *, scope: TenantScope, command: ResolveDesiredAccess
) -> DesiredAccessDecision:
    tenant_id = _tenant(scope)
    service = _required(command.service_reference, "service reference")
    active = set(
        db.scalars(
            select(ServiceAccessInput.signal).where(
                ServiceAccessInput.tenant_id == tenant_id,
                ServiceAccessInput.service_reference == service,
                ServiceAccessInput.active.is_(True),
            )
        ).all()
    )
    matched = next((signal for signal in _DENY_PRECEDENCE if signal in active), None)
    if matched is not None:
        desired, reason = DesiredAccess.DENY, matched.value
    elif AccessSignal.FUP_EXHAUSTED in active:
        desired, reason = DesiredAccess.RESTRICT, AccessSignal.FUP_EXHAUSTED.value
    else:
        desired, reason = DesiredAccess.ALLOW, "POLICY_CLEAR"
    row = db.scalar(
        select(DesiredAccessDecision).where(
            DesiredAccessDecision.tenant_id == tenant_id,
            DesiredAccessDecision.service_reference == service,
        )
    )
    if row is None:
        row = DesiredAccessDecision(
            tenant_id=tenant_id,
            service_reference=service,
            desired_access=desired,
            reason_code=reason,
            decided_at=command.decided_at,
        )
        db.add(row)
    else:
        row.desired_access = desired
        row.reason_code = reason
        row.decided_at = command.decided_at
    db.flush()
    return row


__all__ = ["record_access_input", "resolve_desired_access"]
