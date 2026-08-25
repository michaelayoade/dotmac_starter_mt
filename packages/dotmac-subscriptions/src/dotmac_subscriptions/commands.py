"""Typed assembly-called inputs; no product models or primitive payload bags."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from sqlalchemy.orm import Session

from dotmac_subscriptions.cadence import BillingCadence
from dotmac_subscriptions.contracts import (
    CommercialEntitlementProjectionV1,
    NonCashGrantOutputV1,
    RatedObligationOutputV1,
)
from dotmac_subscriptions.lifecycle import (
    BillingArrangementDecisionStatus,
    BillingArrangementStatus,
    BillingTreatment,
)
from dotmac_subscriptions.values import ExactAmount


@dataclass(frozen=True, slots=True)
class OfferPriceInput:
    price_key: str
    charge_model_code: str
    unit_price: ExactAmount
    quantity: Decimal


class OfferPricingMode(StrEnum):
    """Whether catalogue price evidence is mandatory or contract-owned."""

    catalog_price = "catalog_price"
    contract_price = "contract_price"


@dataclass(frozen=True, slots=True)
class PublishOfferVersionCommand:
    scope: Scope
    offer_id: UUID | None
    offer_code: str
    offer_name: str
    charge_model_code: str
    pricing_mode: OfferPricingMode
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
class ApprovalPolicySnapshot:
    """The approval horizon the PRODUCT resolved, snapshotted as evidence.

    The module never reads a product setting to find this (ADR-0009/0011): the
    adopter resolves its own registered horizon and hands the exact value in,
    and the arrangement keeps it, so shortening the policy later cannot
    retroactively invalidate why an existing approval satisfied it.
    """

    policy_ref: str
    policy_version: str
    maximum_days: int


@dataclass(frozen=True, slots=True)
class PreviewBillingArrangementCommand:
    scope: Scope
    contract_line_key: UUID
    treatment: BillingTreatment
    reason_code: str
    reason: str
    starts_at: datetime
    ends_at: datetime | None
    approval_policy: ApprovalPolicySnapshot
    sponsor_reference: str | None
    cost_center: str | None
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class BillingArrangementPreview:
    scope: Scope
    contract_id: UUID
    contract_line_key: UUID
    authorized_contract_version_id: UUID
    authorized_offer_version_id: UUID
    treatment: BillingTreatment
    reason_code: str
    reason: str
    starts_at: datetime
    ends_at: datetime
    approval_policy: ApprovalPolicySnapshot
    maximum_recurring_amount: ExactAmount
    service_interval_unit: str
    service_interval_count: int
    sponsor_reference: str | None
    cost_center: str | None
    evaluated_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ApproveBillingArrangementCommand:
    scope: Scope
    contract_line_key: UUID
    treatment: BillingTreatment
    reason_code: str
    reason: str
    starts_at: datetime
    ends_at: datetime | None
    approval_policy: ApprovalPolicySnapshot
    sponsor_reference: str | None
    cost_center: str | None
    approved_by: str
    approved_at: datetime
    preview_evaluated_at: datetime
    preview_fingerprint: str
    command_id: UUID
    correlation_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RevokeBillingArrangementCommand:
    scope: Scope
    arrangement_id: UUID
    reason: str
    revoked_by: str
    revoked_at: datetime
    command_id: UUID
    correlation_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class BillingArrangementResult:
    arrangement_id: UUID
    contract_id: UUID
    contract_line_key: UUID
    treatment: BillingTreatment
    status: BillingArrangementStatus
    starts_at: datetime
    ends_at: datetime
    maximum_recurring_amount: ExactAmount
    replayed: bool


@dataclass(frozen=True, slots=True)
class BillingArrangementDecision:
    """One customer-billing answer for one contract line at one moment."""

    scope: Scope
    contract_id: UUID | None
    contract_line_key: UUID
    status: BillingArrangementDecisionStatus
    treatment: BillingTreatment | None = None
    arrangement_id: UUID | None = None
    authorized_contract_version_id: UUID | None = None
    reason_code: str | None = None
    reason: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    maximum_recurring_amount: ExactAmount | None = None
    contracted_amount: ExactAmount | None = None
    drift_reason: str | None = None

    @property
    def suppress_customer_billing(self) -> bool:
        return self.status is not BillingArrangementDecisionStatus.standard

    @property
    def grantable(self) -> bool:
        return self.status is BillingArrangementDecisionStatus.effective


@dataclass(frozen=True, slots=True)
class RecordNonCashGrantCommand:
    scope: Scope
    arrangement_id: UUID
    recurring_occurrence_id: UUID
    foregone_amount: ExactAmount | None
    actor: str
    recorded_at: datetime
    command_id: UUID
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class NonCashGrantResult:
    grant_id: UUID
    arrangement_id: UUID
    contract_line_key: UUID
    recurring_occurrence_id: UUID
    replayed: bool
    staged_output: NonCashGrantOutputV1


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
    "ApprovalPolicySnapshot",
    "ApproveBillingArrangementCommand",
    "BillingArrangementDecision",
    "BillingArrangementPreview",
    "BillingArrangementResult",
    "ContractLineInput",
    "ContractVersionResult",
    "DurableTimerPort",
    "EndContractVersionCommand",
    "EndContractVersionResult",
    "GenerateRecurringChargeCommand",
    "NonCashGrantResult",
    "OccurrenceResult",
    "OfferPriceInput",
    "OfferPricingMode",
    "PublishOfferVersionCommand",
    "PublishOfferVersionResult",
    "PreviewBillingArrangementCommand",
    "RecordNonCashGrantCommand",
    "RecordSubscriptionContractVersionCommand",
    "RevokeBillingArrangementCommand",
    "TimerCancelResult",
    "TimerScheduleResult",
    "WithdrawOfferVersionCommand",
]
