"""Normalized usage owner; callers own authorization and transactions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_usage.contracts import (
    Conflict,
    CorrectUsage,
    ProjectUsageAggregate,
    RecordUsageObservation,
)
from dotmac_usage.models import UsageAggregate, UsageCorrection, UsageObservation


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-usage requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _window(start: datetime, end: datetime) -> None:
    if end <= start:
        raise Conflict("usage window end must follow its start")


def record_usage_observation(
    db: Session, *, scope: TenantScope, command: RecordUsageObservation
) -> UsageObservation:
    tenant_id = _tenant(scope)
    _window(command.period_start, command.period_end)
    if command.quantity < Decimal(0):
        raise Conflict("observed quantity must not be negative")
    source = _required(command.source_reference, "source reference")
    source_event = _required(command.source_event_id, "source event id")
    duplicate = db.scalar(
        select(UsageObservation.id).where(
            UsageObservation.tenant_id == tenant_id,
            UsageObservation.source_reference == source,
            UsageObservation.source_event_id == source_event,
        )
    )
    if duplicate is not None:
        raise Conflict("source event was already recorded")
    row = UsageObservation(
        tenant_id=tenant_id,
        service_reference=_required(command.service_reference, "service reference"),
        meter_code=_required(command.meter_code, "meter code"),
        period_start=command.period_start,
        period_end=command.period_end,
        quantity=command.quantity,
        unit=_required(command.unit, "unit"),
        source_reference=source,
        source_event_id=source_event,
    )
    db.add(row)
    db.flush()
    return row


def record_usage_correction(
    db: Session, *, scope: TenantScope, command: CorrectUsage
) -> UsageCorrection:
    tenant_id = _tenant(scope)
    observation = db.scalar(
        select(UsageObservation.id).where(
            UsageObservation.tenant_id == tenant_id,
            UsageObservation.id == command.observation_id,
        )
    )
    if observation is None:
        raise Conflict("usage observation was not found in the tenant")
    if command.delta_quantity == Decimal(0):
        raise Conflict("usage correction must change the quantity")
    row = UsageCorrection(
        tenant_id=tenant_id,
        observation_id=observation,
        delta_quantity=command.delta_quantity,
        reason=_required(command.reason, "correction reason"),
        corrected_at=command.corrected_at,
    )
    db.add(row)
    db.flush()
    return row


def project_usage_aggregate(
    db: Session, *, scope: TenantScope, command: ProjectUsageAggregate
) -> UsageAggregate:
    tenant_id = _tenant(scope)
    _window(command.window_start, command.window_end)
    service = _required(command.service_reference, "service reference")
    meter = _required(command.meter_code, "meter code")
    row = db.scalar(
        select(UsageAggregate).where(
            UsageAggregate.tenant_id == tenant_id,
            UsageAggregate.service_reference == service,
            UsageAggregate.meter_code == meter,
            UsageAggregate.window_start == command.window_start,
            UsageAggregate.window_end == command.window_end,
        )
    )
    if row is None:
        row = UsageAggregate(
            tenant_id=tenant_id,
            service_reference=service,
            meter_code=meter,
            window_start=command.window_start,
            window_end=command.window_end,
            quantity=command.quantity,
            computed_at=command.computed_at,
        )
        db.add(row)
    else:
        row.quantity = command.quantity
        row.computed_at = command.computed_at
    db.flush()
    return row


__all__ = [
    "project_usage_aggregate",
    "record_usage_correction",
    "record_usage_observation",
]
