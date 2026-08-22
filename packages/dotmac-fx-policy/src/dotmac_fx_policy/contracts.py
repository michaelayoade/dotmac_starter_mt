"""Typed FX-policy commands and results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class FXPolicyError(Exception):
    """Base FX-policy refusal."""


class Conflict(FXPolicyError):
    """The requested mutation or determination is inadmissible."""


@dataclass(frozen=True, slots=True)
class CreateRateType:
    code: str
    name: str
    description: str | None = None
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class RegisterRateSource:
    code: str
    name: str
    priority: int


@dataclass(frozen=True, slots=True)
class SetSelectionPolicy:
    rate_type_id: UUID
    base_currency: str
    quote_currency: str
    effective_from: datetime
    effective_to: datetime | None = None
    preferred_source_id: UUID | None = None
    allow_inverse: bool = True


@dataclass(frozen=True, slots=True)
class RecordRateObservation:
    rate_type_id: UUID
    source_id: UUID
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_at: datetime
    observed_at: datetime
    source_event_reference: str


@dataclass(frozen=True, slots=True)
class DetermineRate:
    request_reference: str
    base_currency: str
    quote_currency: str
    rate_type_code: str
    effective_at: datetime
    determined_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SelectedRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_at: datetime
    source_code: str
    inverted: bool
    observation_id: UUID | None
    policy_id: UUID | None
    determination_id: UUID | None


__all__ = [
    "Conflict",
    "CreateRateType",
    "DetermineRate",
    "FXPolicyError",
    "RecordRateObservation",
    "RegisterRateSource",
    "SelectedRate",
    "SetSelectionPolicy",
]
