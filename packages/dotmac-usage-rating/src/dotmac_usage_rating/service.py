"""Pre-tax usage rating; callers own authorization and transactions."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_usage_rating.contracts import Conflict, CreateRatingRule, RateUsage
from dotmac_usage_rating.models import RatedUsageObligation, RatingRule


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-usage-rating requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def create_rating_rule(
    db: Session, *, scope: TenantScope, command: CreateRatingRule
) -> RatingRule:
    if command.unit_price < Decimal(0):
        raise Conflict("unit price must not be negative")
    if (
        command.effective_until is not None
        and command.effective_until <= command.effective_from
    ):
        raise Conflict("rating rule end must follow its start")
    currency = _required(command.currency, "currency").upper()
    if len(currency) != 3:
        raise ValueError("currency must be a three-letter code")
    row = RatingRule(
        tenant_id=_tenant(scope),
        code=_required(command.code, "rule code"),
        meter_code=_required(command.meter_code, "meter code"),
        unit=_required(command.unit, "unit"),
        unit_price=command.unit_price,
        currency=currency,
        effective_from=command.effective_from,
        effective_until=command.effective_until,
    )
    db.add(row)
    db.flush()
    return row


def rate_usage(
    db: Session, *, scope: TenantScope, command: RateUsage
) -> RatedUsageObligation:
    tenant_id = _tenant(scope)
    rule = db.scalar(
        select(RatingRule).where(
            RatingRule.tenant_id == tenant_id, RatingRule.id == command.rule_id
        )
    )
    if rule is None:
        raise Conflict("rating rule was not found in the tenant")
    if command.quantity < Decimal(0):
        raise Conflict("rated quantity must not be negative")
    if command.usage_occurred_at < rule.effective_from or (
        rule.effective_until is not None
        and command.usage_occurred_at >= rule.effective_until
    ):
        raise Conflict("rating rule is not effective for the usage time")
    usage = _required(command.usage_reference, "usage reference")
    duplicate = db.scalar(
        select(RatedUsageObligation.id).where(
            RatedUsageObligation.tenant_id == tenant_id,
            RatedUsageObligation.usage_reference == usage,
            RatedUsageObligation.rule_id == rule.id,
        )
    )
    if duplicate is not None:
        raise Conflict("usage reference was already rated by this rule")
    amount = (command.quantity * rule.unit_price).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    row = RatedUsageObligation(
        tenant_id=tenant_id,
        usage_reference=usage,
        service_reference=_required(command.service_reference, "service reference"),
        rule_id=rule.id,
        quantity=command.quantity,
        unit_price=rule.unit_price,
        net_amount=amount,
        currency=rule.currency,
        usage_occurred_at=command.usage_occurred_at,
        rated_at=command.rated_at,
    )
    db.add(row)
    db.flush()
    return row


__all__ = ["create_rating_rule", "rate_usage"]
