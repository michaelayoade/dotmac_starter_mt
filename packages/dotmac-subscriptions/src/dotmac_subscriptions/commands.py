"""Typed assembly-called inputs; no product models or primitive payload bags."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from sqlalchemy.orm import Session

from dotmac_subscriptions.cadence import BillingCadence
from dotmac_subscriptions.contracts import (
    CommercialEntitlementProjectionV1,
    RatedObligationOutputV1,
)
from dotmac_subscriptions.values import ExactAmount


@dataclass(frozen=True, slots=True)
class OfferPriceInput:
    price_key: str
    charge_model_code: str
    unit_price: ExactAmount
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class PublishOfferVersionCommand:
    scope: Scope
    offer_id: UUID | None
    offer_code: str
    offer_name: str
    version: int
    prices: tuple[OfferPriceInput, ...]
    effective_from: datetime
    effective_until: datetime | None
    source_code: str
    source_id: UUID
    source_version: int
    command_id: UUID


@dataclass(frozen=True, slots=True)
class WithdrawOfferVersionCommand:
    scope: Scope
    offer_version_id: UUID
    reason: str
    command_id: UUID
    withdrawn_at: datetime


@dataclass(frozen=True, slots=True)
class ContractLineInput:
    contract_line_key: UUID | None
    charge_model_code: str
    source_code: str
    source_id: UUID
    source_version: int
    description: str
    product_link_ref: str
    quantity: Decimal
    unit_price: ExactAmount
    offer_version_id: UUID
    offer_version: int
    entitlement_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordSubscriptionContractVersionCommand:
    scope: Scope
    contract_id: UUID | None
    source_code: str
    source_id: UUID
    source_version: int
    starts_at: datetime
    ends_at: datetime | None
    currency: str
    cadence: BillingCadence
    lines: tuple[ContractLineInput, ...]
    actor: str
    reason: str
    recorded_at: datetime
    command_id: UUID
    correlation_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class EndContractVersionCommand:
    scope: Scope
    contract_version_id: UUID
    ended_at: datetime
    actor: str
    reason: str
    command_id: UUID


@dataclass(frozen=True, slots=True)
class GenerateRecurringChargeCommand:
    scope: Scope
    contract_version_id: UUID
    contract_line_key: UUID
    period_index: int
    generation: int
    emitted_at: datetime
    command_id: UUID
    correlation_id: UUID
    coverage: tuple[datetime, datetime] | None = None
    corrects_occurrence_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PublishOfferVersionResult:
    offer_id: UUID
    offer_version_id: UUID
    was_duplicate: bool


@dataclass(frozen=True, slots=True)
class ContractVersionResult:
    contract_id: UUID
    version_id: UUID
    version: int
    line_keys: tuple[UUID, ...]
    staged_entitlement_outputs: tuple[CommercialEntitlementProjectionV1, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class EndContractVersionResult:
    staged_entitlement_outputs: tuple[CommercialEntitlementProjectionV1, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class OccurrenceResult:
    occurrence_id: UUID
    replayed: bool
    staged_output: RatedObligationOutputV1


@dataclass(frozen=True, slots=True)
class TimerScheduleResult:
    generation: int
    due_at: datetime


@dataclass(frozen=True, slots=True)
class TimerCancelResult:
    canceled: bool


class DurableTimerPort(Protocol):
    """Assembly adapter over the independently owned timer module."""

    def schedule(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        due_at: datetime,
        recorded_at: datetime,
    ) -> TimerScheduleResult: ...

    def cancel(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        recorded_at: datetime,
    ) -> TimerCancelResult: ...


__all__ = [
    "ContractLineInput",
    "ContractVersionResult",
    "DurableTimerPort",
    "EndContractVersionCommand",
    "EndContractVersionResult",
    "GenerateRecurringChargeCommand",
    "OccurrenceResult",
    "OfferPriceInput",
    "PublishOfferVersionCommand",
    "PublishOfferVersionResult",
    "RecordSubscriptionContractVersionCommand",
    "TimerCancelResult",
    "TimerScheduleResult",
    "WithdrawOfferVersionCommand",
]
