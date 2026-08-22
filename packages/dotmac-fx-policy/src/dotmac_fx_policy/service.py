"""Effective FX observation and selection policy; callers own transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from dotmac_fx_policy.contracts import (
    Conflict,
    CreateRateType,
    DetermineRate,
    RecordRateObservation,
    RegisterRateSource,
    SelectedRate,
    SetSelectionPolicy,
)
from dotmac_fx_policy.models import (
    FXRateDetermination,
    FXRateObservation,
    FXRateSource,
    FXRateType,
    FXSelectionPolicy,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-fx-policy requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _code(value: str, field: str) -> str:
    return _required(value, field).upper()


def _currency(value: str) -> str:
    normalized = _code(value, "currency")
    if len(normalized) != 3 or not normalized.isalpha():
        raise Conflict("currency must be a three-letter alphabetic code")
    return normalized


def _rate_type(db: Session, tenant_id: UUID, rate_type_id: UUID) -> FXRateType:
    row = db.scalar(
        select(FXRateType).where(
            FXRateType.tenant_id == tenant_id, FXRateType.id == rate_type_id
        )
    )
    if row is None:
        raise Conflict("FX rate type was not found")
    return row


def _source(db: Session, tenant_id: UUID, source_id: UUID) -> FXRateSource:
    row = db.scalar(
        select(FXRateSource).where(
            FXRateSource.tenant_id == tenant_id, FXRateSource.id == source_id
        )
    )
    if row is None or not row.active:
        raise Conflict("FX rate source was not found or is inactive")
    return row


def create_rate_type(
    db: Session, *, scope: TenantScope, command: CreateRateType
) -> FXRateType:
    row = FXRateType(
        tenant_id=_tenant(scope),
        code=_code(command.code, "rate type code"),
        name=_required(command.name, "rate type name"),
        description=command.description.strip() if command.description else None,
        is_default=command.is_default,
    )
    db.add(row)
    db.flush()
    return row


def register_rate_source(
    db: Session, *, scope: TenantScope, command: RegisterRateSource
) -> FXRateSource:
    if command.priority < 0:
        raise Conflict("source priority must not be negative")
    row = FXRateSource(
        tenant_id=_tenant(scope),
        code=_code(command.code, "source code"),
        name=_required(command.name, "source name"),
        priority=command.priority,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def set_selection_policy(
    db: Session, *, scope: TenantScope, command: SetSelectionPolicy
) -> FXSelectionPolicy:
    tenant_id = _tenant(scope)
    rate_type = _rate_type(db, tenant_id, command.rate_type_id)
    base, quote = _currency(command.base_currency), _currency(command.quote_currency)
    if base == quote:
        raise Conflict("selection policy currency pair must differ")
    if (
        command.effective_to is not None
        and command.effective_to <= command.effective_from
    ):
        raise Conflict("selection policy needs a valid effective window")
    if command.preferred_source_id is not None:
        _source(db, tenant_id, command.preferred_source_id)
    row = FXSelectionPolicy(
        tenant_id=tenant_id,
        rate_type_id=rate_type.id,
        base_currency=base,
        quote_currency=quote,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        preferred_source_id=command.preferred_source_id,
        allow_inverse=command.allow_inverse,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def record_rate_observation(
    db: Session, *, scope: TenantScope, command: RecordRateObservation
) -> FXRateObservation:
    tenant_id = _tenant(scope)
    rate_type = _rate_type(db, tenant_id, command.rate_type_id)
    source = _source(db, tenant_id, command.source_id)
    base, quote = _currency(command.base_currency), _currency(command.quote_currency)
    if base == quote:
        raise Conflict("rate observation currency pair must differ")
    if not command.rate.is_finite() or command.rate <= 0:
        raise Conflict("exchange rate must be finite and positive")
    row = FXRateObservation(
        tenant_id=tenant_id,
        rate_type_id=rate_type.id,
        source_id=source.id,
        base_currency=base,
        quote_currency=quote,
        rate=command.rate,
        effective_at=command.effective_at,
        observed_at=command.observed_at,
        source_event_reference=_required(
            command.source_event_reference, "source event reference"
        ),
    )
    db.add(row)
    db.flush()
    return row


def _observation_query(
    *,
    tenant_id: UUID,
    rate_type_id: UUID,
    base: str,
    quote: str,
    effective_at: datetime,
    preferred_source_id: UUID | None,
) -> Select[tuple[FXRateObservation]]:
    statement = (
        select(FXRateObservation)
        .join(
            FXRateSource,
            (FXRateSource.tenant_id == FXRateObservation.tenant_id)
            & (FXRateSource.id == FXRateObservation.source_id),
        )
        .where(
            FXRateObservation.tenant_id == tenant_id,
            FXRateObservation.rate_type_id == rate_type_id,
            FXRateObservation.base_currency == base,
            FXRateObservation.quote_currency == quote,
            FXRateObservation.effective_at <= effective_at,
            FXRateSource.active.is_(True),
        )
        .order_by(
            FXRateSource.priority.asc(),
            FXRateObservation.effective_at.desc(),
            FXRateObservation.observed_at.desc(),
        )
        .limit(1)
    )
    if preferred_source_id is not None:
        statement = statement.where(FXRateObservation.source_id == preferred_source_id)
    return statement


def determine_rate(
    db: Session, *, scope: TenantScope, command: DetermineRate
) -> SelectedRate:
    tenant_id = _tenant(scope)
    request_reference = _required(command.request_reference, "request reference")
    base, quote = _currency(command.base_currency), _currency(command.quote_currency)
    if base == quote:
        return SelectedRate(
            base_currency=base,
            quote_currency=quote,
            rate=Decimal(1),
            effective_at=command.effective_at,
            source_code="identity",
            inverted=False,
            observation_id=None,
            policy_id=None,
            determination_id=None,
        )
    rate_type = db.scalar(
        select(FXRateType).where(
            FXRateType.tenant_id == tenant_id,
            FXRateType.code == _code(command.rate_type_code, "rate type code"),
        )
    )
    if rate_type is None:
        raise Conflict("FX rate type was not found")
    policy = db.scalar(
        select(FXSelectionPolicy)
        .where(
            FXSelectionPolicy.tenant_id == tenant_id,
            FXSelectionPolicy.rate_type_id == rate_type.id,
            FXSelectionPolicy.base_currency == base,
            FXSelectionPolicy.quote_currency == quote,
            FXSelectionPolicy.active.is_(True),
            FXSelectionPolicy.effective_from <= command.effective_at,
            or_(
                FXSelectionPolicy.effective_to.is_(None),
                FXSelectionPolicy.effective_to > command.effective_at,
            ),
        )
        .order_by(FXSelectionPolicy.effective_from.desc())
        .limit(1)
    )
    if policy is None:
        raise Conflict("no effective FX source-selection policy was found")
    observation = db.scalar(
        _observation_query(
            tenant_id=tenant_id,
            rate_type_id=rate_type.id,
            base=base,
            quote=quote,
            effective_at=command.effective_at,
            preferred_source_id=policy.preferred_source_id,
        )
    )
    inverted = False
    if observation is None and policy.allow_inverse:
        observation = db.scalar(
            _observation_query(
                tenant_id=tenant_id,
                rate_type_id=rate_type.id,
                base=quote,
                quote=base,
                effective_at=command.effective_at,
                preferred_source_id=policy.preferred_source_id,
            )
        )
        inverted = observation is not None
    if observation is None:
        raise Conflict("no admissible FX rate observation was found")
    source = _source(db, tenant_id, observation.source_id)
    rate = Decimal(1) / observation.rate if inverted else observation.rate
    determination = FXRateDetermination(
        tenant_id=tenant_id,
        request_reference=request_reference,
        rate_type_id=rate_type.id,
        policy_id=policy.id,
        observation_id=observation.id,
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        effective_at=command.effective_at,
        inverted=inverted,
        determined_at=command.determined_at or datetime.now(UTC),
    )
    db.add(determination)
    db.flush()
    return SelectedRate(
        base_currency=base,
        quote_currency=quote,
        rate=rate,
        effective_at=observation.effective_at,
        source_code=source.code,
        inverted=inverted,
        observation_id=observation.id,
        policy_id=policy.id,
        determination_id=determination.id,
    )


__all__ = [
    "create_rate_type",
    "determine_rate",
    "record_rate_observation",
    "register_rate_source",
    "set_selection_policy",
]
