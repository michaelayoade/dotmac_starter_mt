"""Frozen producer contracts and persistence-free publisher fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope

from dotmac_subscriptions.cadence import (
    CollectionTiming,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
)
from dotmac_subscriptions.errors import (
    SubscriptionConflictError,
    SubscriptionDataError,
)
from dotmac_subscriptions.values import (
    ExactAmount,
    entitlement_projection_fingerprint,
    occurrence_idempotency_key,
    rating_input_fingerprint,
)


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SubscriptionDataError(
            "contracts.naive_datetime",
            f"{field_name} must be timezone-aware.",
        )


@dataclass(frozen=True, slots=True)
class RatedObligationOutputV1:
    contract_type: str = field(init=False, default="subscriptions.rated_obligation")
    contract_version: int = field(init=False, default=1)
    occurrence_id: UUID
    emitted_at: datetime
    generation: int
    scope: Scope
    subscription_contract_id: UUID
    contract_version_id: UUID
    contract_line_key: UUID
    charge_model_code: str
    source_code: str
    source_id: UUID
    source_version: int
    period_start: datetime
    period_end: datetime
    currency: str
    pre_tax_amount: ExactAmount
    collection_timing: CollectionTiming
    coverage_start: datetime
    coverage_end: datetime
    unit_price: ExactAmount
    quantity: Decimal
    rate_basis: RateBasis
    rate_unit: IntervalUnit
    rate_quantity: Decimal
    rate_units: Decimal
    proration_policy: ProrationPolicy
    proration_factor: Decimal
    timezone_name: str
    rating_policy_version: str
    offer_version_ref: str
    request_fingerprint: str
    idempotency_key: str
    corrects_occurrence_id: UUID | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "emitted_at",
            "period_start",
            "period_end",
            "coverage_start",
            "coverage_end",
        ):
            _aware(getattr(self, field_name), field_name)
        if self.generation < 1 or self.source_version < 1:
            raise SubscriptionDataError(
                "contracts.invalid_version",
                "Generation and source version must be positive.",
            )
        if self.period_end <= self.period_start:
            raise SubscriptionDataError(
                "contracts.invalid_period",
                "Rated period must be non-empty and half-open.",
            )
        if not (
            self.period_start
            <= self.coverage_start
            < self.coverage_end
            <= self.period_end
        ):
            raise SubscriptionDataError(
                "contracts.invalid_coverage",
                "Coverage must be a non-empty interval inside the rated period.",
            )
        for name, value in (
            ("quantity", self.quantity),
            ("rate_quantity", self.rate_quantity),
        ):
            if isinstance(value, float) or not isinstance(value, Decimal) or value <= 0:
                raise SubscriptionDataError(
                    f"contracts.invalid_{name}",
                    f"{name} must be an exact positive Decimal.",
                )
        if self.rate_units < 0 or not Decimal("0") <= self.proration_factor <= Decimal(
            "1"
        ):
            raise SubscriptionDataError(
                "contracts.invalid_rating_factor",
                "Rate units and proration factor fall outside their valid ranges.",
            )
        if self.pre_tax_amount.amount < 0 or self.unit_price.amount < 0:
            raise SubscriptionDataError(
                "contracts.negative_amount",
                "Rated subscription facts never carry negative amounts.",
            )
        if {
            self.currency,
            self.pre_tax_amount.currency,
            self.unit_price.currency,
        } != {self.currency}:
            raise SubscriptionDataError(
                "contracts.mixed_currency",
                "Every amount in one rated output must use its declared currency.",
            )
        if self.rate_basis is RateBasis.usage_metered:
            raise SubscriptionDataError(
                "contracts.usage_rating_not_owned",
                "Subscriptions records usage rate intent but does not rate usage.",
            )
        self.validate_fingerprint()
        expected_key = occurrence_idempotency_key(
            scope=self.scope,
            contract_line_key=self.contract_line_key,
            contract_version_id=self.contract_version_id,
            charge_model_code=self.charge_model_code,
            source_code=self.source_code,
            source_id=self.source_id,
            source_version=self.source_version,
            period_start=self.period_start,
            period_end=self.period_end,
            currency=self.currency,
        )
        if self.idempotency_key != expected_key:
            raise SubscriptionConflictError(
                "contracts.idempotency_key_conflict",
                "Occurrence idempotency key does not match its natural identity.",
            )

    def validate_fingerprint(self) -> None:
        expected = rating_input_fingerprint(
            unit_price=self.unit_price,
            quantity=self.quantity,
            rate_basis=self.rate_basis,
            rate_unit=self.rate_unit,
            rate_quantity=self.rate_quantity,
            rate_units=self.rate_units,
            proration_policy=self.proration_policy,
            proration_factor=self.proration_factor,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            currency=self.currency,
            timezone_name=self.timezone_name,
            rating_policy_version=self.rating_policy_version,
            offer_version_ref=self.offer_version_ref,
        )
        if self.request_fingerprint != expected:
            raise SubscriptionConflictError(
                "contracts.rating_fingerprint_conflict",
                "Recorded request fingerprint differs from the exact rating inputs.",
            )


class EntitlementIntent(str, Enum):
    intended_effective = "intended_effective"
    intended_ended = "intended_ended"


@dataclass(frozen=True, slots=True)
class CommercialEntitlementProjectionV1:
    contract_type: str = field(
        init=False,
        default="subscriptions.commercial_entitlement_projection",
    )
    contract_version: int = field(init=False, default=1)
    projection_id: UUID
    emitted_at: datetime
    scope: Scope
    subscription_contract_id: UUID
    contract_version_id: UUID
    contract_line_key: UUID
    entitlement_codes: tuple[str, ...]
    quantity: Decimal
    intent: EntitlementIntent
    effective_from: datetime
    effective_until: datetime | None
    source_code: str
    source_id: UUID
    source_version: int
    idempotency_key: str
    request_fingerprint: str
    supersedes_projection_id: UUID | None = None

    def __post_init__(self) -> None:
        _aware(self.emitted_at, "emitted_at")
        _aware(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _aware(self.effective_until, "effective_until")
            if self.effective_until <= self.effective_from:
                raise SubscriptionDataError(
                    "contracts.invalid_entitlement_interval",
                    "Entitlement intent interval must be half-open and non-empty.",
                )
        if not self.entitlement_codes or any(
            not code for code in self.entitlement_codes
        ):
            raise SubscriptionDataError(
                "contracts.missing_entitlement_code",
                "Commercial intent requires at least one declared entitlement code.",
            )
        if len(set(self.entitlement_codes)) != len(self.entitlement_codes):
            raise SubscriptionDataError(
                "contracts.duplicate_entitlement_code",
                "Commercial intent cannot repeat an entitlement code.",
            )
        if isinstance(self.quantity, float) or not isinstance(self.quantity, Decimal):
            raise SubscriptionDataError(
                "contracts.invalid_quantity",
                "Entitlement quantity must be an exact Decimal.",
            )
        if self.quantity <= 0 or self.source_version < 1:
            raise SubscriptionDataError(
                "contracts.invalid_entitlement_source",
                "Entitlement quantity and source version must be positive.",
            )
        expected = entitlement_projection_fingerprint(
            entitlement_codes=self.entitlement_codes,
            quantity=self.quantity,
            effective_from=self.effective_from,
            effective_until=self.effective_until,
            source_code=self.source_code,
            source_id=self.source_id,
            source_version=self.source_version,
        )
        if self.request_fingerprint != expected:
            raise SubscriptionConflictError(
                "contracts.entitlement_fingerprint_conflict",
                "Entitlement projection fingerprint differs from its inputs.",
            )


@dataclass(frozen=True, slots=True)
class StageResult:
    was_duplicate: bool


class RatedObligationPublisher(Protocol):
    def stage(self, output: RatedObligationOutputV1) -> StageResult: ...


class EntitlementProjectionPublisher(Protocol):
    def stage(self, output: CommercialEntitlementProjectionV1) -> StageResult: ...


class FakeRatedObligationPublisher:
    """Deterministic fake enforcing the key/fingerprint replay contract."""

    def __init__(self) -> None:
        self._outputs: dict[str, RatedObligationOutputV1] = {}

    @property
    def outputs(self) -> tuple[RatedObligationOutputV1, ...]:
        return tuple(self._outputs.values())

    def stage(self, output: RatedObligationOutputV1) -> StageResult:
        existing = self._outputs.get(output.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != output.request_fingerprint:
                raise SubscriptionConflictError(
                    "publisher.fingerprint_conflict",
                    "The same obligation key was staged with different content.",
                )
            return StageResult(was_duplicate=True)
        self._outputs[output.idempotency_key] = output
        return StageResult(was_duplicate=False)


class FakeEntitlementProjectionPublisher:
    def __init__(self) -> None:
        self._outputs: dict[str, CommercialEntitlementProjectionV1] = {}

    @property
    def outputs(self) -> tuple[CommercialEntitlementProjectionV1, ...]:
        return tuple(self._outputs.values())

    def stage(self, output: CommercialEntitlementProjectionV1) -> StageResult:
        existing = self._outputs.get(output.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != output.request_fingerprint:
                raise SubscriptionConflictError(
                    "publisher.fingerprint_conflict",
                    "The same projection key was staged with different content.",
                )
            return StageResult(was_duplicate=True)
        self._outputs[output.idempotency_key] = output
        return StageResult(was_duplicate=False)


__all__ = [
    "CommercialEntitlementProjectionV1",
    "EntitlementIntent",
    "EntitlementProjectionPublisher",
    "FakeEntitlementProjectionPublisher",
    "FakeRatedObligationPublisher",
    "RatedObligationOutputV1",
    "RatedObligationPublisher",
    "StageResult",
]
