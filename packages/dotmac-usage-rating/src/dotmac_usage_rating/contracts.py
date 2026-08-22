"""Usage-rating commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class UsageRatingError(Exception):
    """Base usage-rating refusal."""


class Conflict(UsageRatingError):
    """The requested rating mutation is inadmissible."""


@dataclass(frozen=True, slots=True)
class CreateRatingRule:
    code: str
    meter_code: str
    unit: str
    unit_price: Decimal
    currency: str
    effective_from: datetime
    effective_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class RateUsage:
    usage_reference: str
    service_reference: str
    rule_id: UUID
    quantity: Decimal
    usage_occurred_at: datetime
    rated_at: datetime


__all__ = [
    "Conflict",
    "CreateRatingRule",
    "RateUsage",
    "UsageRatingError",
]
