"""One recurring-commercial behavior over two explicitly selected planes.

The caller owns the transaction and the assembly supplies peer-module ports.
This module mutates and flushes only; it never commits, delivers an external
message, or reaches into billing, collections, timers, orders, or a product.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, TypeVar, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from dotmac_kernel.cache import Scope, TenantScope, scope_segment
from dotmac_kernel.query import apply_pagination, escape_like
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session

from dotmac_subscriptions.cadence import (
    BillingCadence,
    CadenceAlignment,
    CollectionTiming,
    EndOfMonthRule,
    Interval,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    invoice_period,
    period_containing,
)
from dotmac_subscriptions.commands import (
    ApproveBillingArrangementCommand,
    BillingArrangementResult,
    BillingArrangementRevocationResult,
    ContractLineInput,
    ContractVersionResult,
    DurableTimerPort,
    EndContractVersionCommand,
    EndContractVersionResult,
    GenerateRecurringChargeCommand,
    NonCashGrantResult,
    OccurrenceResult,
    OfferPricingMode,
    PreviewBillingArrangementCommand,
    PublishOfferVersionCommand,
    PublishOfferVersionResult,
    RecordNonCashGrantCommand,
    RecordSubscriptionContractVersionCommand,
    RevokeBillingArrangementCommand,
    WithdrawOfferVersionCommand,
)
from dotmac_subscriptions.contracts import (
    BillingArrangementDecision,
    BillingArrangementPreview,
    CommercialEntitlementProjectionV1,
    EntitlementIntent,
    NonCashGrantOutputV1,
    RatedObligationOutputV1,
)
from dotmac_subscriptions.engine import (
    FIXED_RATING_POLICY_VERSION,
    RatingInput,
    rate_recurring_line,
)
from dotmac_subscriptions.errors import (
    SubscriptionConflictError,
    SubscriptionDataError,
    SubscriptionStateError,
)
from dotmac_subscriptions.lifecycle import (
    BillingTreatmentDecisionStatus,
    BillingTreatmentReason,
    BillingTreatmentStatus,
    SubscriptionBillingTreatment,
)
from dotmac_subscriptions.models import (
    Offer,
    OfferVersion,
    OfferVersionPrice,
    PlatformOffer,
    PlatformOfferVersion,
    PlatformOfferVersionPrice,
    PlatformRecurringChargeOccurrence,
    PlatformSubscriptionBillingArrangement,
    PlatformSubscriptionBillingGrant,
    PlatformSubscriptionContract,
    PlatformSubscriptionContractLine,
    PlatformSubscriptionContractVersion,
    RecurringChargeOccurrence,
    SubscriptionBillingArrangement,
    SubscriptionBillingGrant,
    SubscriptionContract,
    SubscriptionContractLine,
    SubscriptionContractVersion,
)
from dotmac_subscriptions.values import (
    ExactAmount,
    entitlement_projection_fingerprint,
    occurrence_idempotency_key,
)
from dotmac_subscriptions.vocabulary import SubscriptionVocabularyRegistry

if TYPE_CHECKING:
    from dotmac_kernel.idempotency import IdempotentOutcome

_PUBLISH_SCOPE = "subscriptions.publish_offer_version"
_CONTRACT_SCOPE = "subscriptions.record_contract_version"
_OCCURRENCE_SCOPE = "subscriptions.generate_recurring_charge"
_ARRANGEMENT_APPROVE_SCOPE = "subscriptions.approve_billing_arrangement"
_ARRANGEMENT_REVOKE_SCOPE = "subscriptions.revoke_billing_arrangement"
_GRANT_SCOPE = "subscriptions.record_non_cash_grant"

OfferModel = type[Offer] | type[PlatformOffer]
OfferVersionModel = type[OfferVersion] | type[PlatformOfferVersion]
PriceModel = type[OfferVersionPrice] | type[PlatformOfferVersionPrice]
ContractModel = type[SubscriptionContract] | type[PlatformSubscriptionContract]
ContractVersionModel = (
    type[SubscriptionContractVersion] | type[PlatformSubscriptionContractVersion]
)
LineModel = type[SubscriptionContractLine] | type[PlatformSubscriptionContractLine]
OccurrenceModel = (
    type[RecurringChargeOccurrence] | type[PlatformRecurringChargeOccurrence]
)
ArrangementModel = (
    type[SubscriptionBillingArrangement] | type[PlatformSubscriptionBillingArrangement]
)
GrantModel = type[SubscriptionBillingGrant] | type[PlatformSubscriptionBillingGrant]
ContractVersionRow = SubscriptionContractVersion | PlatformSubscriptionContractVersion
LineRow = SubscriptionContractLine | PlatformSubscriptionContractLine
OfferRow = Offer | PlatformOffer
OfferVersionRow = OfferVersion | PlatformOfferVersion
PriceRow = OfferVersionPrice | PlatformOfferVersionPrice
ContractRow = SubscriptionContract | PlatformSubscriptionContract
OccurrenceRow = RecurringChargeOccurrence | PlatformRecurringChargeOccurrence
ArrangementRow = SubscriptionBillingArrangement | PlatformSubscriptionBillingArrangement
GrantRow = SubscriptionBillingGrant | PlatformSubscriptionBillingGrant
_SelectT = TypeVar("_SelectT", bound=tuple[object, ...])


@dataclass(frozen=True, slots=True)
class _PlaneModels:
    offer: OfferModel
    offer_version: OfferVersionModel
    price: PriceModel
    contract: ContractModel
    contract_version: ContractVersionModel
    line: LineModel
    occurrence: OccurrenceModel
    arrangement: ArrangementModel
    grant: GrantModel


class _TenantAwareModel(Protocol):
    tenant_id: InstrumentedAttribute[UUID]


_TENANT_MODELS = _PlaneModels(
    Offer,
    OfferVersion,
    OfferVersionPrice,
    SubscriptionContract,
    SubscriptionContractVersion,
    SubscriptionContractLine,
    RecurringChargeOccurrence,
    SubscriptionBillingArrangement,
    SubscriptionBillingGrant,
)
_PLATFORM_MODELS = _PlaneModels(
    PlatformOffer,
    PlatformOfferVersion,
    PlatformOfferVersionPrice,
    PlatformSubscriptionContract,
    PlatformSubscriptionContractVersion,
    PlatformSubscriptionContractLine,
    PlatformRecurringChargeOccurrence,
    PlatformSubscriptionBillingArrangement,
    PlatformSubscriptionBillingGrant,
)


@dataclass(frozen=True, slots=True)
class OfferVersionSnapshot:
    offer_id: UUID
    offer_version_id: UUID
    version: int
    charge_model_code: str
    pricing_mode: OfferPricingMode
    state: str
    effective_from: datetime
    effective_until: datetime | None
    source_code: str
    source_id: UUID
    source_version: int
    prices: tuple[tuple[str, str, ExactAmount, Decimal], ...]


@dataclass(frozen=True, slots=True)
class OfferCatalogPrice:
    """One exact price row on an immutable effective offer version."""

    price_key: str
    charge_model_code: str
    unit_price: ExactAmount
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OfferCatalogItem:
    """One effective recurring offer with its immutable price snapshot."""

    offer_id: UUID
    code: str
    name: str
    description: str | None
    offer_version_id: UUID
    version: int
    charge_model_code: str
    pricing_mode: OfferPricingMode
    effective_from: datetime
    effective_until: datetime | None
    source_code: str
    source_id: UUID
    source_version: int
    prices: tuple[OfferCatalogPrice, ...]


@dataclass(frozen=True, slots=True)
class OfferCatalogPage:
    """A bounded, deterministic page of effective recurring offers."""

    items: tuple[OfferCatalogItem, ...]
    total: int
    limit: int
    offset: int
    effective_at: datetime


def _models(scope: Scope) -> _PlaneModels:
    return _TENANT_MODELS if isinstance(scope, TenantScope) else _PLATFORM_MODELS


def _scoped(
    statement: Select[_SelectT], scope: Scope, model: object
) -> Select[_SelectT]:
    if isinstance(scope, TenantScope):
        tenant_model = cast(_TenantAwareModel, model)
        statement = statement.where(tenant_model.tenant_id == scope.tenant_id)
    return statement


def _scope_values(scope: Scope) -> dict[str, UUID]:
    if isinstance(scope, TenantScope):
        return {"tenant_id": scope.tenant_id}
    return {}


def _execute_once(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    correlation_id: UUID,
    operation: Callable[[Session], Mapping[str, object] | None],
) -> IdempotentOutcome:
    # Function-local by design: importing `dotmac_kernel.idempotency` reaches
    # the eager database engine through `conflict_savepoint`. Package discovery
    # and migration location must remain safe before DATABASE_URL exists.
    from dotmac_kernel.idempotency import (
        IdempotencyConflict,
        execute_once,
        execute_once_platform,
    )

    try:
        if isinstance(scope, TenantScope):
            return execute_once(
                db,
                tenant_id=scope.tenant_id,
                scope=operation_scope,
                key=key,
                fingerprint=fingerprint,
                correlation_id=str(correlation_id),
                operation=operation,
            )
        return execute_once_platform(
            db,
            scope=operation_scope,
            key=key,
            fingerprint=fingerprint,
            correlation_id=str(correlation_id),
            operation=operation,
        )
    except IdempotencyConflict as exc:
        raise SubscriptionConflictError(
            "idempotency.fingerprint_conflict",
            "Idempotency key was reused with different subscription inputs.",
        ) from exc


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SubscriptionDataError(
            "subscriptions.naive_datetime", f"{field} must be timezone-aware."
        )


def _stored_utc(value: datetime) -> datetime:
    """Restore SQLite's dropped UTC marker; PostgreSQL values stay equivalent."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _fingerprint(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _offer_digest(command: PublishOfferVersionCommand) -> str:
    return _fingerprint(
        {
            "offer_id": str(command.offer_id) if command.offer_id else None,
            "offer_code": command.offer_code,
            "offer_name": command.offer_name,
            "charge_model_code": command.charge_model_code,
            "pricing_mode": command.pricing_mode.value,
            "version": command.version,
            "prices": [
                {
                    "price_key": price.price_key,
                    "charge_model_code": price.charge_model_code,
                    "unit_price": price.unit_price.as_wire(),
                    "quantity": str(price.quantity),
                }
                for price in command.prices
            ],
            "effective_from": command.effective_from.isoformat(),
            "effective_until": (
                command.effective_until.isoformat()
                if command.effective_until is not None
                else None
            ),
            "source_code": command.source_code,
            "source_id": str(command.source_id),
            "source_version": command.source_version,
        }
    )


def _contract_digest(command: RecordSubscriptionContractVersionCommand) -> str:
    cadence = command.cadence
    return _fingerprint(
        {
            "contract_id": str(command.contract_id) if command.contract_id else None,
            "source_code": command.source_code,
            "source_id": str(command.source_id),
            "source_version": command.source_version,
            "starts_at": command.starts_at.isoformat(),
            "ends_at": command.ends_at.isoformat() if command.ends_at else None,
            "currency": command.currency,
            "cadence": {
                "rate_basis": cadence.rate_basis.value,
                "rate_unit": cadence.rate_unit.value,
                "rate_quantity": str(cadence.rate_quantity),
                "service_interval_unit": cadence.service_interval_unit.value,
                "service_interval_count": cadence.service_interval_count,
                "invoice_interval_unit": cadence.invoice_interval_unit.value,
                "invoice_interval_count": cadence.invoice_interval_count,
                "collection_timing": cadence.collection_timing.value,
                "alignment": cadence.alignment.value,
                "anchor_day": cadence.anchor_day,
                "end_of_month_rule": cadence.end_of_month_rule.value,
                "timezone_name": cadence.timezone_name,
                "proration_policy": cadence.proration_policy.value,
            },
            "lines": [
                {
                    "contract_line_key": (
                        str(line.contract_line_key) if line.contract_line_key else None
                    ),
                    "charge_model_code": line.charge_model_code,
                    "source_code": line.source_code,
                    "source_id": str(line.source_id),
                    "source_version": line.source_version,
                    "description": line.description,
                    "product_link_ref": line.product_link_ref,
                    "quantity": str(line.quantity),
                    "unit_price": line.unit_price.as_wire(),
                    "offer_version_id": str(line.offer_version_id),
                    "offer_version": line.offer_version,
                    "entitlement_codes": list(line.entitlement_codes),
                }
                for line in command.lines
            ],
            "actor": command.actor,
            "reason": command.reason,
            "recorded_at": command.recorded_at.isoformat(),
            "command_id": str(command.command_id),
            "correlation_id": str(command.correlation_id),
        }
    )


def publish_offer_version(
    db: Session,
    command: PublishOfferVersionCommand,
    *,
    registry: SubscriptionVocabularyRegistry,
) -> PublishOfferVersionResult:
    """Publish one immutable price snapshot beneath a stable offer identity."""
    _require_aware(command.effective_from, "effective_from")
    if command.effective_until is not None:
        _require_aware(command.effective_until, "effective_until")
        if command.effective_until <= command.effective_from:
            raise SubscriptionDataError(
                "offers.invalid_interval", "Offer effective interval is empty."
            )
    if command.version < 1 or command.source_version < 1:
        raise SubscriptionDataError(
            "offers.incomplete_version",
            "Positive source and offer version values are required.",
        )
    if not command.offer_code or not command.offer_name:
        raise SubscriptionDataError(
            "offers.missing_identity", "Offer code and name are required."
        )
    registry.require_charge_model(command.charge_model_code)
    if command.pricing_mode is OfferPricingMode.catalog_price and not command.prices:
        raise SubscriptionDataError(
            "offers.catalog_price_required",
            "A catalog-priced offer version requires a positive reference price.",
        )
    price_keys: set[str] = set()
    for price in command.prices:
        registry.require_charge_model(price.charge_model_code)
        if price.charge_model_code != command.charge_model_code:
            raise SubscriptionDataError(
                "offers.mixed_charge_model",
                "Reference prices must use the offer version's charge model.",
            )
        if not price.price_key or price.price_key in price_keys:
            raise SubscriptionDataError(
                "offers.duplicate_price_key", "Price keys must be present and unique."
            )
        if isinstance(price.quantity, float) or not isinstance(price.quantity, Decimal):
            raise SubscriptionDataError(
                "offers.invalid_quantity", "Price quantity must be an exact Decimal."
            )
        if price.quantity <= 0 or price.unit_price.amount <= 0:
            raise SubscriptionDataError(
                "offers.invalid_price",
                "Reference price and quantity must be strictly positive.",
            )
        price_keys.add(price.price_key)
    registry.require_obligation_source(command.source_code)
    digest = _offer_digest(command)
    plane = _models(command.scope)

    def operation(session: Session) -> dict[str, object]:
        offer: OfferRow | None = None
        if command.offer_id is not None:
            offer = cast(
                OfferRow | None,
                session.execute(
                    _scoped(
                        select(plane.offer).where(plane.offer.id == command.offer_id),
                        command.scope,
                        plane.offer,
                    )
                ).scalar_one_or_none(),
            )
        if offer is None:
            offer = cast(
                OfferRow | None,
                session.execute(
                    _scoped(
                        select(plane.offer).where(
                            plane.offer.code == command.offer_code
                        ),
                        command.scope,
                        plane.offer,
                    )
                ).scalar_one_or_none(),
            )
        if offer is None:
            offer = cast(
                OfferRow,
                plane.offer(
                    **_scope_values(command.scope),
                    id=command.offer_id or uuid4(),
                    code=command.offer_code,
                    name=command.offer_name,
                    description=None,
                    status="published",
                ),
            )
            session.add(offer)
            session.flush()
        elif offer.code != command.offer_code:
            raise SubscriptionConflictError(
                "offers.identity_conflict", "Offer id and code identify different rows."
            )

        collision = session.execute(
            _scoped(
                select(plane.offer_version).where(
                    plane.offer_version.offer_id == offer.id,
                    plane.offer_version.version == command.version,
                ),
                command.scope,
                plane.offer_version,
            )
        ).scalar_one_or_none()
        if collision is not None:
            raise SubscriptionConflictError(
                "offers.version_conflict", "That offer version is already published."
            )
        version_id = uuid4()
        version = plane.offer_version(
            **_scope_values(command.scope),
            id=version_id,
            offer_id=offer.id,
            version=command.version,
            charge_model_code=command.charge_model_code,
            pricing_mode=command.pricing_mode.value,
            state="published",
            effective_from=command.effective_from.astimezone(UTC),
            effective_until=(
                command.effective_until.astimezone(UTC)
                if command.effective_until is not None
                else None
            ),
            source_code=command.source_code,
            source_id=command.source_id,
            source_version=command.source_version,
            command_id=command.command_id,
            content_digest=digest,
            withdrawn_at=None,
            withdrawal_reason=None,
            withdrawal_command_id=None,
        )
        session.add(version)
        for price in command.prices:
            session.add(
                plane.price(
                    **_scope_values(command.scope),
                    id=uuid4(),
                    offer_version_id=version_id,
                    price_key=price.price_key,
                    charge_model_code=price.charge_model_code,
                    amount=price.unit_price.amount,
                    currency=price.unit_price.currency,
                    scale=price.unit_price.scale,
                    quantity=price.quantity,
                )
            )
        session.flush()
        return {"offer_id": str(offer.id), "offer_version_id": str(version_id)}

    try:
        outcome = _execute_once(
            db,
            scope=command.scope,
            operation_scope=_PUBLISH_SCOPE,
            key=str(command.command_id),
            fingerprint=digest,
            correlation_id=command.command_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "offers.database_conflict", "Offer publication conflicts with stored state."
        ) from exc
    return PublishOfferVersionResult(
        offer_id=UUID(str(outcome.result["offer_id"])),
        offer_version_id=UUID(str(outcome.result["offer_version_id"])),
        was_duplicate=outcome.replayed,
    )


def withdraw_offer_version(db: Session, command: WithdrawOfferVersionCommand) -> None:
    _require_aware(command.withdrawn_at, "withdrawn_at")
    if not command.reason.strip():
        raise SubscriptionDataError(
            "offers.missing_withdrawal_reason", "Withdrawal reason is required."
        )
    plane = _models(command.scope)
    version = cast(
        OfferVersionRow | None,
        db.execute(
            _scoped(
                select(plane.offer_version).where(
                    plane.offer_version.id == command.offer_version_id
                ),
                command.scope,
                plane.offer_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError("offers.not_found", "Offer version was not found.")
    if version.state == "withdrawn":
        if version.withdrawal_command_id != command.command_id:
            raise SubscriptionConflictError(
                "offers.withdrawal_conflict",
                "Offer version was withdrawn by a different command.",
            )
        return
    version.state = "withdrawn"
    version.withdrawn_at = command.withdrawn_at.astimezone(UTC)
    version.withdrawal_reason = command.reason
    version.withdrawal_command_id = command.command_id
    db.flush()


def _cadence_from(version: ContractVersionRow) -> BillingCadence:
    return BillingCadence(
        rate_basis=RateBasis(version.rate_basis),
        rate_unit=IntervalUnit(version.rate_unit),
        rate_quantity=version.rate_quantity,
        service_interval_unit=IntervalUnit(version.service_interval_unit),
        service_interval_count=version.service_interval_count,
        invoice_interval_unit=IntervalUnit(version.invoice_interval_unit),
        invoice_interval_count=version.invoice_interval_count,
        collection_timing=CollectionTiming(version.collection_timing),
        alignment=CadenceAlignment(version.alignment),
        anchor_day=version.anchor_day,
        end_of_month_rule=EndOfMonthRule(version.end_of_month_rule),
        timezone_name=version.timezone_name,
        proration_policy=ProrationPolicy(version.proration_policy),
    )


def _billing_cadence_fingerprint(version: ContractVersionRow) -> str:
    cadence = _cadence_from(version)
    return _fingerprint(
        {
            "rate_basis": cadence.rate_basis.value,
            "rate_unit": cadence.rate_unit.value,
            "rate_quantity": str(cadence.rate_quantity),
            "service_interval_unit": cadence.service_interval_unit.value,
            "service_interval_count": cadence.service_interval_count,
            "invoice_interval_unit": cadence.invoice_interval_unit.value,
            "invoice_interval_count": cadence.invoice_interval_count,
            "collection_timing": cadence.collection_timing.value,
            "alignment": cadence.alignment.value,
            "anchor_day": cadence.anchor_day,
            "end_of_month_rule": cadence.end_of_month_rule.value,
            "timezone_name": cadence.timezone_name,
            "proration_policy": cadence.proration_policy.value,
            "rating_policy_version": version.rating_policy_version,
        }
    )


def _load_arrangement_inputs(
    db: Session, command: PreviewBillingArrangementCommand
) -> tuple[ContractVersionRow, LineRow, OfferVersionRow, _PlaneModels]:
    plane = _models(command.scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == command.contract_version_id,
                    plane.contract_version.contract_id
                    == command.subscription_contract_id,
                ),
                command.scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError(
            "billing_arrangements.contract_version_not_found",
            "The exact subscription contract version was not found.",
        )
    line = cast(
        LineRow | None,
        db.execute(
            _scoped(
                select(plane.line).where(
                    plane.line.contract_version_id == version.id,
                    plane.line.contract_line_key == command.contract_line_key,
                ),
                command.scope,
                plane.line,
            )
        ).scalar_one_or_none(),
    )
    if line is None:
        raise SubscriptionDataError(
            "billing_arrangements.contract_line_not_found",
            "The exact subscription contract line was not found.",
        )
    offer_version = cast(
        OfferVersionRow | None,
        db.execute(
            _scoped(
                select(plane.offer_version).where(
                    plane.offer_version.id == line.offer_version_id,
                    plane.offer_version.version == line.offer_version,
                ),
                command.scope,
                plane.offer_version,
            )
        ).scalar_one_or_none(),
    )
    if offer_version is None:
        raise SubscriptionDataError(
            "billing_arrangements.offer_version_not_found",
            "The contract line's immutable offer version was not found.",
        )
    return version, line, offer_version, plane


def _validate_arrangement_command(command: PreviewBillingArrangementCommand) -> None:
    for instant, field in (
        (command.starts_at, "starts_at"),
        (command.ends_at, "ends_at"),
        (command.evaluated_at, "evaluated_at"),
    ):
        _require_aware(instant, field)
    if command.treatment is SubscriptionBillingTreatment.standard:
        raise SubscriptionDataError(
            "billing_arrangements.standard_treatment",
            "Standard billing is represented by no arrangement.",
        )
    if command.ends_at <= command.starts_at:
        raise SubscriptionDataError(
            "billing_arrangements.invalid_period",
            "A billing arrangement requires a non-empty finite period.",
        )
    if command.starts_at < command.evaluated_at - timedelta(minutes=5):
        raise SubscriptionDataError(
            "billing_arrangements.retroactive",
            "A billing arrangement must start prospectively.",
        )
    if not 1 <= command.approval_policy_max_days <= 366:
        raise SubscriptionDataError(
            "billing_arrangements.invalid_policy_horizon",
            "Approval policy maximum days must be between 1 and 366.",
        )
    if command.ends_at - command.starts_at > timedelta(
        days=command.approval_policy_max_days
    ):
        raise SubscriptionDataError(
            "billing_arrangements.policy_horizon_exceeded",
            "The billing arrangement exceeds its approval policy horizon.",
        )
    required = {
        "reason": (command.reason, 2000),
        "approval_policy_reference": (command.approval_policy_reference, 200),
        "approval_policy_version": (command.approval_policy_version, 80),
    }
    for field, (value, limit) in required.items():
        if not value.strip() or len(value) > limit:
            raise SubscriptionDataError(
                "billing_arrangements.invalid_evidence",
                f"{field} is required and must be at most {limit} characters.",
            )
    if command.sponsor_reference is not None and (
        not command.sponsor_reference.strip() or len(command.sponsor_reference) > 200
    ):
        raise SubscriptionDataError(
            "billing_arrangements.invalid_sponsor",
            "Sponsor reference must be non-blank and at most 200 characters.",
        )
    if command.cost_center is not None and (
        not command.cost_center.strip() or len(command.cost_center) > 100
    ):
        raise SubscriptionDataError(
            "billing_arrangements.invalid_cost_center",
            "Cost centre must be non-blank and at most 100 characters.",
        )
    if command.treatment is SubscriptionBillingTreatment.sponsored and not (
        command.sponsor_reference or command.cost_center
    ):
        raise SubscriptionDataError(
            "billing_arrangements.missing_sponsor_evidence",
            "Sponsored treatment requires a sponsor reference or cost centre.",
        )


def _arrangement_overlap_exists(
    db: Session,
    *,
    scope: Scope,
    subscription_contract_id: UUID,
    contract_line_key: UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> bool:
    plane = _models(scope)
    statement = select(plane.arrangement.id).where(
        plane.arrangement.subscription_contract_id == subscription_contract_id,
        plane.arrangement.contract_line_key == contract_line_key,
        plane.arrangement.starts_at < ends_at,
        plane.arrangement.ends_at > starts_at,
        or_(
            plane.arrangement.revoked_at.is_(None),
            plane.arrangement.revoked_at > starts_at,
        ),
    )
    return (
        db.execute(
            _scoped(statement.limit(1), scope, plane.arrangement)
        ).scalar_one_or_none()
        is not None
    )


def _preview_billing_arrangement(
    db: Session,
    command: PreviewBillingArrangementCommand,
    *,
    check_overlap: bool,
) -> BillingArrangementPreview:
    _validate_arrangement_command(command)
    version, line, offer_version, _ = _load_arrangement_inputs(db, command)
    if version.state != "effective":
        raise SubscriptionStateError(
            "billing_arrangements.contract_version_inactive",
            "Only the effective contract version can receive an arrangement.",
        )
    contract_start = _stored_utc(version.starts_at)
    start = command.starts_at.astimezone(UTC)
    end = command.ends_at.astimezone(UTC)
    cadence = _cadence_from(version)
    _, start_period = period_containing(
        cadence=cadence,
        contract_start=contract_start,
        moment=start,
    )
    _, end_period = period_containing(
        cadence=cadence,
        contract_start=contract_start,
        moment=end,
    )
    if start_period.starts_at != start or end_period.starts_at != end:
        raise SubscriptionDataError(
            "billing_arrangements.unaligned_period",
            "Arrangement boundaries must align with complete service periods.",
        )
    version_end = (
        _stored_utc(version.declared_ends_at)
        if version.declared_ends_at is not None
        else None
    )
    if version_end is not None and end > version_end:
        raise SubscriptionDataError(
            "billing_arrangements.outside_contract",
            "The arrangement cannot extend beyond the contracted period.",
        )
    if check_overlap and _arrangement_overlap_exists(
        db,
        scope=command.scope,
        subscription_contract_id=command.subscription_contract_id,
        contract_line_key=command.contract_line_key,
        starts_at=start,
        ends_at=end,
    ):
        raise SubscriptionConflictError(
            "billing_arrangements.overlap",
            "The contract line already has an overlapping billing arrangement.",
        )
    maximum = ExactAmount(
        line.unit_price * line.quantity,
        line.currency,
        line.scale,
    )
    if maximum.amount <= 0:
        raise SubscriptionDataError(
            "billing_arrangements.non_positive_contract_price",
            "The arrangement requires a strictly positive contracted amount.",
        )
    cadence_fingerprint = _billing_cadence_fingerprint(version)
    facts: dict[str, object] = {
        "scope": scope_segment(command.scope),
        "subscription_contract_id": str(command.subscription_contract_id),
        "contract_version_id": str(command.contract_version_id),
        "contract_line_key": str(command.contract_line_key),
        "offer_version_id": str(offer_version.id),
        "treatment": command.treatment.value,
        "reason_code": command.reason_code.value,
        "reason": command.reason.strip(),
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "approval_policy_reference": command.approval_policy_reference.strip(),
        "approval_policy_version": command.approval_policy_version.strip(),
        "approval_policy_max_days": command.approval_policy_max_days,
        "maximum_recurring_amount": maximum.as_wire(),
        "cadence_fingerprint": cadence_fingerprint,
        "sponsor_reference": (
            command.sponsor_reference.strip() if command.sponsor_reference else None
        ),
        "cost_center": command.cost_center.strip() if command.cost_center else None,
    }
    return BillingArrangementPreview(
        subscription_contract_id=command.subscription_contract_id,
        contract_version_id=command.contract_version_id,
        contract_line_key=command.contract_line_key,
        offer_version_id=offer_version.id,
        treatment=command.treatment,
        reason_code=command.reason_code,
        reason=command.reason.strip(),
        starts_at=start,
        ends_at=end,
        approval_policy_reference=command.approval_policy_reference.strip(),
        approval_policy_version=command.approval_policy_version.strip(),
        approval_policy_max_days=command.approval_policy_max_days,
        maximum_recurring_amount=maximum,
        cadence_fingerprint=cadence_fingerprint,
        sponsor_reference=(
            command.sponsor_reference.strip() if command.sponsor_reference else None
        ),
        cost_center=command.cost_center.strip() if command.cost_center else None,
        evaluated_at=command.evaluated_at.astimezone(UTC),
        fingerprint=_fingerprint(facts),
    )


def preview_billing_arrangement(
    db: Session, command: PreviewBillingArrangementCommand
) -> BillingArrangementPreview:
    """Preview one finite non-cash treatment against exact contract facts."""
    return _preview_billing_arrangement(db, command, check_overlap=True)


def _standard_billing_decision(
    *,
    scope: Scope,
    subscription_contract_id: UUID,
    contract_version_id: UUID,
    contract_line_key: UUID,
) -> BillingArrangementDecision:
    return BillingArrangementDecision(
        scope=scope,
        subscription_contract_id=subscription_contract_id,
        contract_version_id=contract_version_id,
        contract_line_key=contract_line_key,
        status=BillingTreatmentDecisionStatus.standard,
        treatment=SubscriptionBillingTreatment.standard,
        arrangement_id=None,
        reason_code=None,
        reason=None,
        starts_at=None,
        ends_at=None,
        maximum_recurring_amount=None,
        drift_reason=None,
    )


def resolve_billing_arrangement(
    db: Session,
    *,
    scope: Scope,
    subscription_contract_id: UUID,
    contract_version_id: UUID,
    contract_line_key: UUID,
    effective_at: datetime,
) -> BillingArrangementDecision:
    """Resolve one customer-billing decision and fail closed on stored overlap."""
    _require_aware(effective_at, "effective_at")
    observed_at = effective_at.astimezone(UTC)
    plane = _models(scope)
    arrangements = list(
        db.execute(
            _scoped(
                select(plane.arrangement)
                .where(
                    plane.arrangement.subscription_contract_id
                    == subscription_contract_id,
                    plane.arrangement.contract_line_key == contract_line_key,
                    plane.arrangement.starts_at <= observed_at,
                    plane.arrangement.ends_at > observed_at,
                    or_(
                        plane.arrangement.revoked_at.is_(None),
                        plane.arrangement.revoked_at > observed_at,
                    ),
                )
                .order_by(plane.arrangement.starts_at.desc(), plane.arrangement.id),
                scope,
                plane.arrangement,
            )
        ).scalars()
    )
    if not arrangements:
        return _standard_billing_decision(
            scope=scope,
            subscription_contract_id=subscription_contract_id,
            contract_version_id=contract_version_id,
            contract_line_key=contract_line_key,
        )
    if len(arrangements) > 1:
        raise SubscriptionConflictError(
            "billing_arrangements.overlapping_effective_rows",
            "Multiple billing arrangements are effective; customer billing is blocked.",
        )
    arrangement = cast(ArrangementRow, arrangements[0])
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == contract_version_id,
                    plane.contract_version.contract_id == subscription_contract_id,
                ),
                scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    line = cast(
        LineRow | None,
        db.execute(
            _scoped(
                select(plane.line).where(
                    plane.line.contract_version_id == contract_version_id,
                    plane.line.contract_line_key == contract_line_key,
                ),
                scope,
                plane.line,
            )
        ).scalar_one_or_none(),
    )
    drift_reason: str | None = None
    if arrangement.contract_version_id != contract_version_id:
        drift_reason = "unauthorized_contract_version_change"
    elif version is None or line is None:
        drift_reason = "contract_facts_missing"
    elif arrangement.offer_version_id != line.offer_version_id:
        drift_reason = "unauthorized_offer_change"
    elif arrangement.currency != line.currency or arrangement.scale != line.scale:
        drift_reason = "currency_or_scale_mismatch"
    elif arrangement.maximum_recurring_amount != line.unit_price * line.quantity:
        drift_reason = "approved_value_changed"
    elif arrangement.cadence_fingerprint != _billing_cadence_fingerprint(version):
        drift_reason = "cadence_changed"
    return BillingArrangementDecision(
        scope=scope,
        subscription_contract_id=subscription_contract_id,
        contract_version_id=contract_version_id,
        contract_line_key=contract_line_key,
        status=(
            BillingTreatmentDecisionStatus.protected_drift
            if drift_reason is not None
            else BillingTreatmentDecisionStatus.effective
        ),
        treatment=SubscriptionBillingTreatment(arrangement.treatment),
        arrangement_id=arrangement.id,
        reason_code=BillingTreatmentReason(arrangement.reason_code),
        reason=arrangement.reason,
        starts_at=_stored_utc(arrangement.starts_at),
        ends_at=_stored_utc(arrangement.ends_at),
        maximum_recurring_amount=ExactAmount(
            arrangement.maximum_recurring_amount,
            arrangement.currency,
            arrangement.scale,
        ),
        drift_reason=drift_reason,
    )


def approve_billing_arrangement(
    db: Session, command: ApproveBillingArrangementCommand
) -> BillingArrangementResult:
    """Approve one arrangement through the module's idempotent canonical writer."""
    _require_aware(command.approved_at, "approved_at")
    if (
        not command.approved_by.strip()
        or not command.idempotency_key
        or len(command.idempotency_key) > 255
    ):
        raise SubscriptionDataError(
            "billing_arrangements.invalid_approval_evidence",
            "Approver and a bounded idempotency key are required.",
        )
    approval_fingerprint = _fingerprint(
        {
            "preview_fingerprint": command.preview_fingerprint,
            "approved_by": command.approved_by.strip(),
            "approved_at": command.approved_at.astimezone(UTC).isoformat(),
            "command_id": str(command.command_id),
            "correlation_id": str(command.correlation_id),
        }
    )
    arrangement_id = uuid4()

    def operation(session: Session) -> dict[str, object]:
        preview = _preview_billing_arrangement(
            session, command.preview, check_overlap=False
        )
        if preview.fingerprint != command.preview_fingerprint:
            raise SubscriptionConflictError(
                "billing_arrangements.stale_preview",
                "Contract or approval evidence changed; preview the arrangement again.",
            )
        if _arrangement_overlap_exists(
            session,
            scope=command.preview.scope,
            subscription_contract_id=preview.subscription_contract_id,
            contract_line_key=preview.contract_line_key,
            starts_at=preview.starts_at,
            ends_at=preview.ends_at,
        ):
            raise SubscriptionConflictError(
                "billing_arrangements.overlap",
                "The contract line already has an overlapping billing arrangement.",
            )
        plane = _models(command.preview.scope)
        session.add(
            plane.arrangement(
                **_scope_values(command.preview.scope),
                id=arrangement_id,
                subscription_contract_id=preview.subscription_contract_id,
                contract_version_id=preview.contract_version_id,
                contract_line_key=preview.contract_line_key,
                offer_version_id=preview.offer_version_id,
                treatment=preview.treatment.value,
                reason_code=preview.reason_code.value,
                reason=preview.reason,
                starts_at=preview.starts_at,
                ends_at=preview.ends_at,
                approval_policy_reference=preview.approval_policy_reference,
                approval_policy_version=preview.approval_policy_version,
                approval_policy_max_days=preview.approval_policy_max_days,
                maximum_recurring_amount=preview.maximum_recurring_amount.amount,
                currency=preview.maximum_recurring_amount.currency,
                scale=preview.maximum_recurring_amount.scale,
                cadence_fingerprint=preview.cadence_fingerprint,
                sponsor_reference=preview.sponsor_reference,
                cost_center=preview.cost_center,
                status=BillingTreatmentStatus.active.value,
                approved_by=command.approved_by.strip(),
                approved_at=command.approved_at.astimezone(UTC),
                revoked_by=None,
                revoked_at=None,
                revocation_reason=None,
                revocation_command_id=None,
                revocation_correlation_id=None,
                revocation_idempotency_key=None,
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                idempotency_key=command.idempotency_key,
                content_digest=approval_fingerprint,
            )
        )
        session.flush()
        return {"arrangement_id": str(arrangement_id)}

    try:
        outcome = _execute_once(
            db,
            scope=command.preview.scope,
            operation_scope=_ARRANGEMENT_APPROVE_SCOPE,
            key=command.idempotency_key,
            fingerprint=approval_fingerprint,
            correlation_id=command.correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "billing_arrangements.database_conflict",
            "Billing arrangement evidence conflicts with an existing command.",
        ) from exc
    stored_id = UUID(str(outcome.result["arrangement_id"]))
    decision = resolve_billing_arrangement(
        db,
        scope=command.preview.scope,
        subscription_contract_id=command.preview.subscription_contract_id,
        contract_version_id=command.preview.contract_version_id,
        contract_line_key=command.preview.contract_line_key,
        effective_at=command.preview.starts_at,
    )
    return BillingArrangementResult(stored_id, decision, outcome.replayed)


def revoke_billing_arrangement(
    db: Session, command: RevokeBillingArrangementCommand
) -> BillingArrangementRevocationResult:
    """Prospectively restore standard billing while preserving historical grants."""
    _require_aware(command.revoked_at, "revoked_at")
    if (
        not command.revoked_by.strip()
        or not command.reason.strip()
        or not command.idempotency_key
        or len(command.idempotency_key) > 255
    ):
        raise SubscriptionDataError(
            "billing_arrangements.invalid_revocation_evidence",
            "Revoker, reason, and a bounded idempotency key are required.",
        )
    fingerprint = _fingerprint(
        {
            "scope": scope_segment(command.scope),
            "arrangement_id": str(command.arrangement_id),
            "revoked_by": command.revoked_by.strip(),
            "revoked_at": command.revoked_at.astimezone(UTC).isoformat(),
            "reason": command.reason.strip(),
            "command_id": str(command.command_id),
            "correlation_id": str(command.correlation_id),
        }
    )

    def operation(session: Session) -> dict[str, object]:
        plane = _models(command.scope)
        arrangement = cast(
            ArrangementRow | None,
            session.execute(
                _scoped(
                    select(plane.arrangement)
                    .where(plane.arrangement.id == command.arrangement_id)
                    .with_for_update(),
                    command.scope,
                    plane.arrangement,
                )
            ).scalar_one_or_none(),
        )
        if arrangement is None:
            raise SubscriptionDataError(
                "billing_arrangements.not_found",
                "The billing arrangement was not found.",
            )
        if arrangement.status == BillingTreatmentStatus.revoked.value:
            raise SubscriptionConflictError(
                "billing_arrangements.already_revoked",
                "The billing arrangement was already revoked by another command.",
            )
        revoked_at = command.revoked_at.astimezone(UTC)
        if revoked_at < _stored_utc(arrangement.approved_at):
            raise SubscriptionDataError(
                "billing_arrangements.revocation_before_approval",
                "Revocation cannot predate approval.",
            )
        arrangement.status = BillingTreatmentStatus.revoked.value
        arrangement.revoked_by = command.revoked_by.strip()
        arrangement.revoked_at = revoked_at
        arrangement.revocation_reason = command.reason.strip()
        arrangement.revocation_command_id = command.command_id
        arrangement.revocation_correlation_id = command.correlation_id
        arrangement.revocation_idempotency_key = command.idempotency_key
        session.flush()
        return {"arrangement_id": str(arrangement.id)}

    try:
        outcome = _execute_once(
            db,
            scope=command.scope,
            operation_scope=_ARRANGEMENT_REVOKE_SCOPE,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            correlation_id=command.correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "billing_arrangements.revocation_conflict",
            "Revocation evidence conflicts with an existing command.",
        ) from exc
    return BillingArrangementRevocationResult(
        UUID(str(outcome.result["arrangement_id"])), outcome.replayed
    )


def _grant_output(scope: Scope, row: GrantRow) -> NonCashGrantOutputV1:
    return NonCashGrantOutputV1(
        grant_id=row.id,
        arrangement_id=row.arrangement_id,
        scope=scope,
        subscription_contract_id=row.subscription_contract_id,
        contract_version_id=row.contract_version_id,
        contract_line_key=row.contract_line_key,
        occurrence_id=row.occurrence_id,
        treatment=SubscriptionBillingTreatment(row.treatment),
        reason_code=BillingTreatmentReason(row.reason_code),
        arrangement_reason=row.arrangement_reason,
        starts_at=_stored_utc(row.starts_at),
        ends_at=_stored_utc(row.ends_at),
        reference_amount=ExactAmount(row.reference_amount, row.currency, row.scale),
        actor=row.actor,
        reason=row.reason,
        recorded_at=_stored_utc(row.recorded_at),
        command_id=row.command_id,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
    )


def record_non_cash_grant(
    db: Session, command: RecordNonCashGrantCommand
) -> NonCashGrantResult:
    """Record one append-only grant against an exact positive rated occurrence."""
    for value, field in (
        (command.starts_at, "starts_at"),
        (command.ends_at, "ends_at"),
        (command.recorded_at, "recorded_at"),
    ):
        _require_aware(value, field)
    if (
        command.ends_at <= command.starts_at
        or command.reference_amount.amount <= 0
        or not command.actor.strip()
        or not command.reason.strip()
        or not command.idempotency_key
        or len(command.idempotency_key) > 255
    ):
        raise SubscriptionDataError(
            "billing_grants.invalid_evidence",
            "A positive exact period, actor, reason, and idempotency key are required.",
        )
    start = command.starts_at.astimezone(UTC)
    end = command.ends_at.astimezone(UTC)
    plane = _models(command.scope)
    fingerprint = _fingerprint(
        {
            "scope": scope_segment(command.scope),
            "arrangement_id": str(command.arrangement_id),
            "occurrence_id": str(command.occurrence_id),
            "subscription_contract_id": str(command.subscription_contract_id),
            "contract_version_id": str(command.contract_version_id),
            "contract_line_key": str(command.contract_line_key),
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "reference_amount": command.reference_amount.as_wire(),
            "actor": command.actor.strip(),
            "reason": command.reason.strip(),
            "recorded_at": command.recorded_at.astimezone(UTC).isoformat(),
            "command_id": str(command.command_id),
            "correlation_id": str(command.correlation_id),
        }
    )
    grant_id = uuid4()

    def operation(session: Session) -> dict[str, object]:
        decision = resolve_billing_arrangement(
            session,
            scope=command.scope,
            subscription_contract_id=command.subscription_contract_id,
            contract_version_id=command.contract_version_id,
            contract_line_key=command.contract_line_key,
            effective_at=command.starts_at,
        )
        if not decision.grantable or decision.arrangement_id != command.arrangement_id:
            raise SubscriptionConflictError(
                "billing_grants.protected_drift",
                "Billing-treatment drift blocks grant creation.",
            )
        occurrence = cast(
            OccurrenceRow | None,
            session.execute(
                _scoped(
                    select(plane.occurrence).where(
                        plane.occurrence.id == command.occurrence_id,
                        plane.occurrence.contract_id
                        == command.subscription_contract_id,
                        plane.occurrence.contract_version_id
                        == command.contract_version_id,
                        plane.occurrence.contract_line_key == command.contract_line_key,
                    ),
                    command.scope,
                    plane.occurrence,
                )
            ).scalar_one_or_none(),
        )
        if occurrence is None:
            raise SubscriptionDataError(
                "billing_grants.occurrence_not_found",
                "The exact recurring charge occurrence was not found.",
            )
        occurrence_amount = ExactAmount(
            occurrence.pre_tax_amount,
            occurrence.currency,
            occurrence.amount_scale,
        )
        if (
            _stored_utc(occurrence.period_start) != start
            or _stored_utc(occurrence.period_end) != end
            or occurrence_amount != command.reference_amount
        ):
            raise SubscriptionConflictError(
                "billing_grants.occurrence_mismatch",
                "Grant evidence must exactly match the positive rated occurrence.",
            )
        arrangement = cast(
            ArrangementRow,
            session.execute(
                _scoped(
                    select(plane.arrangement).where(
                        plane.arrangement.id == command.arrangement_id
                    ),
                    command.scope,
                    plane.arrangement,
                )
            ).scalar_one(),
        )
        if (
            start < _stored_utc(arrangement.starts_at)
            or end > _stored_utc(arrangement.ends_at)
            or command.reference_amount.currency != arrangement.currency
            or command.reference_amount.scale != arrangement.scale
            or command.reference_amount.amount > arrangement.maximum_recurring_amount
        ):
            raise SubscriptionConflictError(
                "billing_grants.approval_exceeded",
                "The rated occurrence exceeds the approved treatment boundary.",
            )
        session.add(
            plane.grant(
                **_scope_values(command.scope),
                id=grant_id,
                arrangement_id=arrangement.id,
                occurrence_id=occurrence.id,
                subscription_contract_id=command.subscription_contract_id,
                contract_version_id=command.contract_version_id,
                contract_line_key=command.contract_line_key,
                treatment=arrangement.treatment,
                reason_code=arrangement.reason_code,
                arrangement_reason=arrangement.reason,
                starts_at=start,
                ends_at=end,
                reference_amount=command.reference_amount.amount,
                currency=command.reference_amount.currency,
                scale=command.reference_amount.scale,
                actor=command.actor.strip(),
                reason=command.reason.strip(),
                recorded_at=command.recorded_at.astimezone(UTC),
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                idempotency_key=command.idempotency_key,
                content_digest=fingerprint,
            )
        )
        session.flush()
        return {"grant_id": str(grant_id)}

    try:
        outcome = _execute_once(
            db,
            scope=command.scope,
            operation_scope=_GRANT_SCOPE,
            key=command.idempotency_key,
            fingerprint=fingerprint,
            correlation_id=command.correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "billing_grants.database_conflict",
            "Grant evidence conflicts with an existing period or command.",
        ) from exc
    stored_id = UUID(str(outcome.result["grant_id"]))
    stored = cast(
        GrantRow,
        db.execute(
            _scoped(
                select(plane.grant).where(plane.grant.id == stored_id),
                command.scope,
                plane.grant,
            )
        ).scalar_one(),
    )
    return NonCashGrantResult(_grant_output(command.scope, stored), outcome.replayed)


def _projection_identity(
    *,
    scope: Scope,
    contract_version_id: UUID,
    contract_line_key: UUID,
    intent: EntitlementIntent,
    effective_from: datetime,
) -> tuple[UUID, str]:
    digest = _fingerprint(
        {
            "scope": scope_segment(scope),
            "contract_version_id": str(contract_version_id),
            "contract_line_key": str(contract_line_key),
            "intent": intent.value,
            "effective_from": effective_from.isoformat(),
        }
    )
    return (
        uuid5(NAMESPACE_URL, f"dotmac:subscriptions:projection:{digest}"),
        f"subscriptions:projection:{digest}",
    )


def entitlement_projections_for_version(
    db: Session,
    *,
    scope: Scope,
    contract_version_id: UUID,
    intent: EntitlementIntent,
) -> tuple[CommercialEntitlementProjectionV1, ...]:
    """Rebuild stable commercial-intent outputs from immutable contract facts."""
    plane = _models(scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == contract_version_id
                ),
                scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError(
            "contracts.not_found", "Contract version was not found."
        )

    if intent is EntitlementIntent.intended_effective:
        effective_from = _stored_utc(version.starts_at)
        effective_until = (
            _stored_utc(version.declared_ends_at)
            if version.declared_ends_at is not None
            else None
        )
        emitted_at = _stored_utc(version.recorded_at)
    else:
        if version.state not in {"superseded", "ended"} or version.ends_at is None:
            raise SubscriptionStateError(
                "contracts.not_terminal",
                "Ended entitlement intent requires a superseded or ended version.",
            )
        effective_from = _stored_utc(version.ends_at)
        effective_until = None
        emitted_at = _stored_utc(version.superseded_at or version.ends_at)

    lines = cast(
        Iterable[LineRow],
        db.execute(
            _scoped(
                select(plane.line)
                .where(plane.line.contract_version_id == contract_version_id)
                .order_by(plane.line.contract_line_key),
                scope,
                plane.line,
            )
        ).scalars(),
    )
    outputs: list[CommercialEntitlementProjectionV1] = []
    for line in lines:
        entitlement_codes = tuple(line.entitlement_codes)
        if not entitlement_codes:
            continue
        projection_id, idempotency_key = _projection_identity(
            scope=scope,
            contract_version_id=version.id,
            contract_line_key=line.contract_line_key,
            intent=intent,
            effective_from=effective_from,
        )
        supersedes_projection_id = None
        if intent is EntitlementIntent.intended_ended:
            supersedes_projection_id, _ = _projection_identity(
                scope=scope,
                contract_version_id=version.id,
                contract_line_key=line.contract_line_key,
                intent=EntitlementIntent.intended_effective,
                effective_from=_stored_utc(version.starts_at),
            )
        outputs.append(
            CommercialEntitlementProjectionV1(
                projection_id=projection_id,
                emitted_at=emitted_at,
                scope=scope,
                subscription_contract_id=version.contract_id,
                contract_version_id=version.id,
                contract_line_key=line.contract_line_key,
                entitlement_codes=entitlement_codes,
                quantity=line.quantity,
                intent=intent,
                effective_from=effective_from,
                effective_until=effective_until,
                source_code=line.source_code,
                source_id=line.source_id,
                source_version=line.source_version,
                idempotency_key=idempotency_key,
                request_fingerprint=entitlement_projection_fingerprint(
                    entitlement_codes=entitlement_codes,
                    quantity=line.quantity,
                    effective_from=effective_from,
                    effective_until=effective_until,
                    source_code=line.source_code,
                    source_id=line.source_id,
                    source_version=line.source_version,
                ),
                supersedes_projection_id=supersedes_projection_id,
            )
        )
    return tuple(outputs)


def _validate_line(
    line: ContractLineInput,
    *,
    currency: str,
    registry: SubscriptionVocabularyRegistry,
) -> None:
    registry.require_charge_model(line.charge_model_code)
    registry.require_obligation_source(line.source_code)
    if not line.product_link_ref:
        raise SubscriptionDataError(
            "contracts.missing_product_link", "A product link reference is required."
        )
    if line.source_version < 1 or line.offer_version < 1:
        raise SubscriptionDataError(
            "contracts.invalid_source_version", "Source versions must be positive."
        )
    if any(not code for code in line.entitlement_codes) or len(
        set(line.entitlement_codes)
    ) != len(line.entitlement_codes):
        raise SubscriptionDataError(
            "contracts.invalid_entitlement_codes",
            "Entitlement codes, when present, must be non-empty and unique.",
        )
    if isinstance(line.quantity, float) or not isinstance(line.quantity, Decimal):
        raise SubscriptionDataError(
            "contracts.invalid_quantity", "Line quantity must be an exact Decimal."
        )
    if line.quantity <= 0 or line.unit_price.amount <= 0:
        raise SubscriptionDataError(
            "contracts.invalid_line",
            "Contract-line quantity and unit price must be strictly positive.",
        )
    if line.unit_price.currency != currency:
        raise SubscriptionDataError(
            "contracts.mixed_currency", "Every contract line uses contract currency."
        )


def record_contract_version(
    db: Session,
    command: RecordSubscriptionContractVersionCommand,
    *,
    registry: SubscriptionVocabularyRegistry,
    timer: DurableTimerPort,
    rating_policy_version: str = FIXED_RATING_POLICY_VERSION,
) -> ContractVersionResult:
    """Create or supersede a contract version and schedule its first occurrence."""
    for value, field in (
        (command.starts_at, "starts_at"),
        (command.recorded_at, "recorded_at"),
    ):
        _require_aware(value, field)
    if command.ends_at is not None:
        _require_aware(command.ends_at, "ends_at")
        if command.ends_at <= command.starts_at:
            raise SubscriptionDataError(
                "contracts.invalid_interval", "Contract interval is empty."
            )
    if (
        not command.idempotency_key
        or len(command.idempotency_key) > 200
        or not command.lines
        or not rating_policy_version
        or not command.actor.strip()
        or not command.reason.strip()
    ):
        raise SubscriptionDataError(
            "contracts.incomplete_version",
            "Idempotency key, contract lines, rating policy, actor, and reason "
            "are required.",
        )
    registry.require_obligation_source(command.source_code)
    for line in command.lines:
        _validate_line(line, currency=command.currency, registry=registry)
    supplied_keys = [
        line.contract_line_key for line in command.lines if line.contract_line_key
    ]
    if len(supplied_keys) != len(set(supplied_keys)):
        raise SubscriptionDataError(
            "contracts.duplicate_line_key", "A contract version cannot repeat a line."
        )
    component_keys = [
        (line.charge_model_code, line.product_link_ref) for line in command.lines
    ]
    if len(component_keys) != len(set(component_keys)):
        raise SubscriptionDataError(
            "contracts.duplicate_component",
            "A contract version cannot repeat one charge model and product link.",
        )
    digest = _fingerprint(
        {
            "command_fingerprint": _contract_digest(command),
            "rating_policy_version": rating_policy_version,
        }
    )
    plane = _models(command.scope)

    def operation(session: Session) -> dict[str, object]:
        contract: ContractRow | None = None
        if command.contract_id is not None:
            contract = cast(
                ContractRow | None,
                session.execute(
                    _scoped(
                        select(plane.contract).where(
                            plane.contract.id == command.contract_id
                        ),
                        command.scope,
                        plane.contract,
                    )
                ).scalar_one_or_none(),
            )
            if contract is None:
                raise SubscriptionDataError(
                    "contracts.not_found", "Subscription contract was not found."
                )
        else:
            contract = cast(
                ContractRow | None,
                session.execute(
                    _scoped(
                        select(plane.contract).where(
                            plane.contract.source_code == command.source_code,
                            plane.contract.source_id == command.source_id,
                        ),
                        command.scope,
                        plane.contract,
                    )
                ).scalar_one_or_none(),
            )
        if contract is None:
            contract = cast(
                ContractRow,
                plane.contract(
                    **_scope_values(command.scope),
                    id=uuid4(),
                    source_code=command.source_code,
                    source_id=command.source_id,
                ),
            )
            session.add(contract)
            session.flush()

        current = cast(
            ContractVersionRow | None,
            session.execute(
                _scoped(
                    select(plane.contract_version)
                    .where(
                        plane.contract_version.contract_id == contract.id,
                        plane.contract_version.state == "effective",
                    )
                    .order_by(plane.contract_version.version.desc())
                    .limit(1)
                    .with_for_update(),
                    command.scope,
                    plane.contract_version,
                )
            ).scalar_one_or_none(),
        )
        next_version = 1
        supersedes_id = None
        superseded_line_keys: set[UUID] = set()
        if current is not None:
            if command.starts_at <= _stored_utc(current.starts_at):
                raise SubscriptionConflictError(
                    "contracts.non_monotonic_version",
                    "A new version must start after the current version.",
                )
            open_arrangement_id = session.execute(
                _scoped(
                    select(plane.arrangement.id)
                    .where(
                        plane.arrangement.subscription_contract_id == contract.id,
                        plane.arrangement.contract_version_id == current.id,
                        plane.arrangement.ends_at > command.starts_at,
                        or_(
                            plane.arrangement.revoked_at.is_(None),
                            plane.arrangement.revoked_at > command.starts_at,
                        ),
                    )
                    .limit(1)
                    .with_for_update(),
                    command.scope,
                    plane.arrangement,
                )
            ).scalar_one_or_none()
            if open_arrangement_id is not None:
                raise SubscriptionConflictError(
                    "contracts.open_billing_arrangement",
                    "Revoke the open billing arrangement before changing "
                    "contract terms.",
                )
            current.ends_at = command.starts_at.astimezone(UTC)
            current.state = "superseded"
            current.superseded_at = command.recorded_at.astimezone(UTC)
            current.terminal_actor = command.actor
            current.terminal_reason = command.reason
            current.terminal_command_id = command.command_id
            superseded_line_keys = set(
                session.execute(
                    _scoped(
                        select(plane.line.contract_line_key).where(
                            plane.line.contract_version_id == current.id
                        ),
                        command.scope,
                        plane.line,
                    )
                ).scalars()
            )
            next_version = current.version + 1
            supersedes_id = current.id
            session.flush()

        version_id = uuid4()
        cadence = command.cadence
        version = plane.contract_version(
            **_scope_values(command.scope),
            id=version_id,
            contract_id=contract.id,
            version=next_version,
            state="effective",
            source_code=command.source_code,
            source_id=command.source_id,
            source_version=command.source_version,
            starts_at=command.starts_at.astimezone(UTC),
            ends_at=(
                command.ends_at.astimezone(UTC) if command.ends_at is not None else None
            ),
            declared_ends_at=(
                command.ends_at.astimezone(UTC) if command.ends_at is not None else None
            ),
            currency=command.currency,
            rate_basis=cadence.rate_basis.value,
            rate_unit=cadence.rate_unit.value,
            rate_quantity=cadence.rate_quantity,
            service_interval_unit=cadence.service_interval_unit.value,
            service_interval_count=cadence.service_interval_count,
            invoice_interval_unit=cadence.invoice_interval_unit.value,
            invoice_interval_count=cadence.invoice_interval_count,
            collection_timing=cadence.collection_timing.value,
            alignment=cadence.alignment.value,
            anchor_day=cadence.anchor_day,
            end_of_month_rule=cadence.end_of_month_rule.value,
            timezone_name=cadence.timezone_name,
            proration_policy=cadence.proration_policy.value,
            rating_policy_version=rating_policy_version,
            supersedes_id=supersedes_id,
            superseded_at=None,
            terminal_actor=None,
            terminal_reason=None,
            terminal_command_id=None,
            actor=command.actor,
            reason=command.reason,
            recorded_at=command.recorded_at.astimezone(UTC),
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            content_digest=digest,
        )
        session.add(version)
        # Flush the version explicitly so PostgreSQL never receives a contract
        # LINE before the version it references. Without this the ordering is
        # decided by whatever else happens to flush first — and with
        # `autoflush=False` nothing does, so the line insert reaches the
        # database first and dies on `fk_contract_lines_version`, surfacing as a
        # conflict error that reads like a real conflict and is not one.
        #
        # A module must not depend on its host assembly's session settings.
        # `dotmac-billing` already flushes its obligation for exactly this
        # reason; this is the same rule, applied where it was missing. Found by
        # Dotmac Cloud, whose `DatabaseRuntime` sets `autoflush=False`
        # deliberately, composing this module for the first time.
        session.flush()
        line_keys: list[UUID] = []
        first_period = invoice_period(cadence=cadence, contract_start=command.starts_at)
        due_at = (
            first_period.starts_at
            if cadence.collection_timing is CollectionTiming.advance
            else first_period.ends_at
        )
        for line_input in command.lines:
            offer_version = cast(
                OfferVersionRow | None,
                session.execute(
                    _scoped(
                        select(plane.offer_version).where(
                            plane.offer_version.id == line_input.offer_version_id,
                            plane.offer_version.version == line_input.offer_version,
                            plane.offer_version.state == "published",
                            plane.offer_version.effective_from <= command.starts_at,
                            (
                                plane.offer_version.effective_until.is_(None)
                                | (
                                    plane.offer_version.effective_until
                                    > command.starts_at
                                )
                            ),
                        ),
                        command.scope,
                        plane.offer_version,
                    )
                ).scalar_one_or_none(),
            )
            if offer_version is None:
                raise SubscriptionDataError(
                    "contracts.offer_version_unavailable",
                    "Every line must reference a published immutable offer version.",
                )
            if offer_version.charge_model_code != line_input.charge_model_code:
                raise SubscriptionDataError(
                    "contracts.offer_charge_model_mismatch",
                    "Contract line and offer version must use one charge model.",
                )
            line_key = line_input.contract_line_key or uuid4()
            line_keys.append(line_key)
            session.add(
                plane.line(
                    **_scope_values(command.scope),
                    id=uuid4(),
                    contract_version_id=version_id,
                    contract_line_key=line_key,
                    charge_model_code=line_input.charge_model_code,
                    source_code=line_input.source_code,
                    source_id=line_input.source_id,
                    source_version=line_input.source_version,
                    description=line_input.description,
                    product_link_ref=line_input.product_link_ref,
                    quantity=line_input.quantity,
                    unit_price=line_input.unit_price.amount,
                    currency=line_input.unit_price.currency,
                    scale=line_input.unit_price.scale,
                    offer_version_id=line_input.offer_version_id,
                    offer_version=line_input.offer_version,
                    entitlement_codes=list(line_input.entitlement_codes),
                )
            )
        session.flush()
        for line_key in line_keys:
            timer.schedule(
                session,
                scope=command.scope,
                contract_line_key=line_key,
                due_at=due_at,
                recorded_at=command.recorded_at,
            )
        for removed_line_key in superseded_line_keys - set(line_keys):
            timer.cancel(
                session,
                scope=command.scope,
                contract_line_key=removed_line_key,
                recorded_at=command.recorded_at,
            )
        session.flush()
        return {
            "contract_id": str(contract.id),
            "version_id": str(version_id),
            "version": next_version,
            "line_keys": [str(value) for value in line_keys],
            "supersedes_id": str(supersedes_id) if supersedes_id else None,
        }

    try:
        outcome = _execute_once(
            db,
            scope=command.scope,
            operation_scope=_CONTRACT_SCOPE,
            key=command.idempotency_key,
            fingerprint=digest,
            correlation_id=command.correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "contracts.database_conflict",
            "Contract version conflicts with stored state.",
        ) from exc
    version_id = UUID(str(outcome.result["version_id"]))
    entitlement_outputs = list(
        entitlement_projections_for_version(
            db,
            scope=command.scope,
            contract_version_id=version_id,
            intent=EntitlementIntent.intended_effective,
        )
    )
    supersedes_value = outcome.result.get("supersedes_id")
    if supersedes_value is not None:
        entitlement_outputs.extend(
            entitlement_projections_for_version(
                db,
                scope=command.scope,
                contract_version_id=UUID(str(supersedes_value)),
                intent=EntitlementIntent.intended_ended,
            )
        )
    return ContractVersionResult(
        contract_id=UUID(str(outcome.result["contract_id"])),
        version_id=version_id,
        version=int(str(outcome.result["version"])),
        line_keys=tuple(
            UUID(str(value))
            for value in cast(list[object], outcome.result["line_keys"])
        ),
        staged_entitlement_outputs=tuple(entitlement_outputs),
        replayed=outcome.replayed,
    )


def end_contract_version(
    db: Session,
    command: EndContractVersionCommand,
    *,
    timer: DurableTimerPort,
) -> EndContractVersionResult:
    _require_aware(command.ended_at, "ended_at")
    if not command.actor.strip() or not command.reason.strip():
        raise SubscriptionDataError(
            "contracts.missing_terminal_provenance",
            "Ending a contract version requires an actor and reason.",
        )
    plane = _models(command.scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == command.contract_version_id
                ),
                command.scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError(
            "contracts.not_found", "Contract version was not found."
        )
    if version.state == "ended":
        if (
            version.ends_at is None
            or _stored_utc(version.ends_at) != command.ended_at.astimezone(UTC)
            or version.terminal_command_id != command.command_id
        ):
            raise SubscriptionConflictError(
                "contracts.end_conflict", "Contract version already ended elsewhere."
            )
        return EndContractVersionResult(
            staged_entitlement_outputs=entitlement_projections_for_version(
                db,
                scope=command.scope,
                contract_version_id=command.contract_version_id,
                intent=EntitlementIntent.intended_ended,
            ),
            replayed=True,
        )
    if version.state != "effective" or command.ended_at.astimezone(UTC) <= _stored_utc(
        version.starts_at
    ):
        raise SubscriptionStateError(
            "contracts.invalid_end", "Only an effective contract version can end."
        )
    version.state = "ended"
    version.ends_at = command.ended_at.astimezone(UTC)
    version.superseded_at = command.ended_at.astimezone(UTC)
    version.terminal_actor = command.actor
    version.terminal_reason = command.reason
    version.terminal_command_id = command.command_id
    line_keys = db.execute(
        _scoped(
            select(plane.line.contract_line_key).where(
                plane.line.contract_version_id == command.contract_version_id
            ),
            command.scope,
            plane.line,
        )
    ).scalars()
    for line_key in line_keys:
        timer.cancel(
            db,
            scope=command.scope,
            contract_line_key=line_key,
            recorded_at=command.ended_at,
        )
    db.flush()
    return EndContractVersionResult(
        staged_entitlement_outputs=entitlement_projections_for_version(
            db,
            scope=command.scope,
            contract_version_id=command.contract_version_id,
            intent=EntitlementIntent.intended_ended,
        ),
        replayed=False,
    )


def cadence_of(
    db: Session, *, scope: Scope, contract_version_id: UUID
) -> BillingCadence:
    plane = _models(scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == contract_version_id
                ),
                scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError(
            "contracts.not_found", "Contract version was not found."
        )
    return _cadence_from(version)


def effective_version_at(
    db: Session, *, scope: Scope, contract_id: UUID, moment: datetime
) -> UUID | None:
    _require_aware(moment, "moment")
    plane = _models(scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.contract_id == contract_id,
                    plane.contract_version.starts_at <= moment,
                    (
                        plane.contract_version.ends_at.is_(None)
                        | (plane.contract_version.ends_at > moment)
                    ),
                    plane.contract_version.state.in_(
                        ("effective", "superseded", "ended")
                    ),
                ),
                scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    return version.id if version is not None else None


def offer_version_snapshot(
    db: Session, *, scope: Scope, offer_version_id: UUID
) -> OfferVersionSnapshot:
    plane = _models(scope)
    version = cast(
        OfferVersionRow | None,
        db.execute(
            _scoped(
                select(plane.offer_version).where(
                    plane.offer_version.id == offer_version_id
                ),
                scope,
                plane.offer_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError("offers.not_found", "Offer version was not found.")
    prices = cast(
        Iterable[PriceRow],
        db.execute(
            _scoped(
                select(plane.price)
                .where(plane.price.offer_version_id == offer_version_id)
                .order_by(plane.price.price_key),
                scope,
                plane.price,
            )
        ).scalars(),
    )
    return OfferVersionSnapshot(
        offer_id=version.offer_id,
        offer_version_id=version.id,
        version=version.version,
        charge_model_code=version.charge_model_code,
        pricing_mode=OfferPricingMode(version.pricing_mode),
        state=version.state,
        effective_from=_stored_utc(version.effective_from),
        effective_until=(
            _stored_utc(version.effective_until)
            if version.effective_until is not None
            else None
        ),
        source_code=version.source_code,
        source_id=version.source_id,
        source_version=version.source_version,
        prices=tuple(
            (
                price.price_key,
                price.charge_model_code,
                ExactAmount(price.amount, price.currency, price.scale),
                price.quantity,
            )
            for price in prices
        ),
    )


def _catalog_search_pattern(search: str | None) -> str | None:
    if search is None:
        return None
    normalized = search.strip()
    if len(normalized) > 200:
        raise SubscriptionDataError(
            "offers.search_too_long",
            "Offer catalog search is limited to 200 characters.",
        )
    if not normalized:
        return None
    escaped = escape_like(normalized.lower())
    return f"%{escaped}%"


def list_effective_offers(
    db: Session,
    *,
    scope: Scope,
    effective_at: datetime,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> OfferCatalogPage:
    """List one current immutable version per stable recurring offer.

    This is an owner read, not a presentation decision. It returns exact money
    and source provenance; a product adapter decides formatting, grouping,
    eligibility, availability, and actions before handing display-only values
    to a UI component.
    """
    _require_aware(effective_at, "effective_at")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise SubscriptionDataError(
            "offers.invalid_limit", "Offer catalog limit must be between 1 and 100."
        )
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise SubscriptionDataError(
            "offers.invalid_offset", "Offer catalog offset cannot be negative."
        )
    pattern = _catalog_search_pattern(search)
    plane = _models(scope)

    ranked_versions = _scoped(
        select(
            plane.offer_version.id.label("offer_version_id"),
            func.row_number()
            .over(
                partition_by=plane.offer_version.offer_id,
                order_by=(
                    plane.offer_version.effective_from.desc(),
                    plane.offer_version.version.desc(),
                    plane.offer_version.id.desc(),
                ),
            )
            .label("effective_rank"),
        ).where(
            plane.offer_version.state == "published",
            plane.offer_version.effective_from <= effective_at,
            (
                plane.offer_version.effective_until.is_(None)
                | (plane.offer_version.effective_until > effective_at)
            ),
        ),
        scope,
        plane.offer_version,
    ).subquery()

    statement = (
        select(plane.offer, plane.offer_version)
        .join(
            plane.offer_version,
            plane.offer_version.offer_id == plane.offer.id,
        )
        .join(
            ranked_versions,
            (ranked_versions.c.offer_version_id == plane.offer_version.id)
            & (ranked_versions.c.effective_rank == 1),
        )
        .where(plane.offer.status == "published")
    )
    statement = _scoped(statement, scope, plane.offer)
    statement = _scoped(statement, scope, plane.offer_version)
    if pattern is not None:
        statement = statement.where(
            or_(
                func.lower(plane.offer.code).like(pattern, escape="\\"),
                func.lower(plane.offer.name).like(pattern, escape="\\"),
                func.lower(plane.offer.description).like(pattern, escape="\\"),
            )
        )

    identities = statement.with_only_columns(plane.offer.id).order_by(None).subquery()
    total = int(db.execute(select(func.count()).select_from(identities)).scalar_one())
    rows = db.execute(
        apply_pagination(
            statement.order_by(
                func.lower(plane.offer.name),
                func.lower(plane.offer.code),
                plane.offer.id,
            ),
            limit=limit,
            offset=offset,
        )
    ).all()

    version_ids = [row[1].id for row in rows]
    prices_by_version: dict[UUID, list[OfferCatalogPrice]] = {
        version_id: [] for version_id in version_ids
    }
    if version_ids:
        price_statement = select(plane.price).where(
            plane.price.offer_version_id.in_(version_ids)
        )
        price_statement = _scoped(price_statement, scope, plane.price)
        prices = cast(
            Iterable[PriceRow],
            db.execute(
                price_statement.order_by(
                    plane.price.offer_version_id,
                    plane.price.price_key,
                )
            ).scalars(),
        )
        for price in prices:
            prices_by_version[price.offer_version_id].append(
                OfferCatalogPrice(
                    price_key=price.price_key,
                    charge_model_code=price.charge_model_code,
                    unit_price=ExactAmount(price.amount, price.currency, price.scale),
                    quantity=price.quantity,
                )
            )

    items = tuple(
        OfferCatalogItem(
            offer_id=offer.id,
            code=offer.code,
            name=offer.name,
            description=offer.description,
            offer_version_id=version.id,
            version=version.version,
            charge_model_code=version.charge_model_code,
            pricing_mode=OfferPricingMode(version.pricing_mode),
            effective_from=_stored_utc(version.effective_from),
            effective_until=(
                _stored_utc(version.effective_until)
                if version.effective_until is not None
                else None
            ),
            source_code=version.source_code,
            source_id=version.source_id,
            source_version=version.source_version,
            prices=tuple(prices_by_version[version.id]),
        )
        for offer, version in rows
    )
    return OfferCatalogPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        effective_at=effective_at.astimezone(UTC),
    )


def _load_rating_rows(
    db: Session, command: GenerateRecurringChargeCommand
) -> tuple[ContractVersionRow, LineRow, _PlaneModels]:
    plane = _models(command.scope)
    version = cast(
        ContractVersionRow | None,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == command.contract_version_id
                ),
                command.scope,
                plane.contract_version,
            )
        ).scalar_one_or_none(),
    )
    if version is None:
        raise SubscriptionDataError(
            "contracts.not_found", "Contract version was not found."
        )
    line = cast(
        LineRow | None,
        db.execute(
            _scoped(
                select(plane.line).where(
                    plane.line.contract_version_id == command.contract_version_id,
                    plane.line.contract_line_key == command.contract_line_key,
                ),
                command.scope,
                plane.line,
            )
        ).scalar_one_or_none(),
    )
    if line is None:
        raise SubscriptionDataError(
            "contracts.line_not_found", "Contract line was not found."
        )
    return version, line, plane


def generate_recurring_charge(
    db: Session,
    command: GenerateRecurringChargeCommand,
    *,
    registry: SubscriptionVocabularyRegistry,
    timer: DurableTimerPort,
) -> OccurrenceResult:
    """Rate and transactionally stage one replayable pre-tax occurrence."""
    _require_aware(command.emitted_at, "emitted_at")
    if command.period_index < 0 or command.generation < 1:
        raise SubscriptionDataError(
            "occurrences.invalid_generation", "Period index and generation are invalid."
        )
    version, line, plane = _load_rating_rows(db, command)
    if version.state not in {"effective", "superseded", "ended"}:
        raise SubscriptionStateError(
            "occurrences.contract_inactive", "Contract version is not rateable."
        )
    registry.require_charge_model(line.charge_model_code)
    registry.require_obligation_source(line.source_code)
    cadence = _cadence_from(version)
    contract_start = _stored_utc(version.starts_at)
    contract_end = _stored_utc(version.ends_at) if version.ends_at is not None else None
    period = invoice_period(
        cadence=cadence,
        contract_start=contract_start,
        index=command.period_index,
    )
    if contract_end is not None and period.starts_at >= contract_end:
        raise SubscriptionStateError(
            "occurrences.after_contract_end",
            "The requested period starts after the contract version ended.",
        )
    coverage = Interval(*command.coverage) if command.coverage else period
    rating = rate_recurring_line(
        RatingInput(
            unit_price=ExactAmount(line.unit_price, line.currency, line.scale),
            quantity=line.quantity,
            cadence=cadence,
            period=period,
            coverage=coverage,
            rating_policy_version=version.rating_policy_version,
            offer_version_ref=f"{line.offer_version_id}:{line.offer_version}",
        )
    )
    idempotency_key = occurrence_idempotency_key(
        scope=command.scope,
        contract_line_key=line.contract_line_key,
        contract_version_id=version.id,
        charge_model_code=line.charge_model_code,
        source_code=line.source_code,
        source_id=line.source_id,
        source_version=line.source_version,
        period_start=period.starts_at,
        period_end=period.ends_at,
        currency=line.currency,
    )
    occurrence_id = uuid4()
    output = RatedObligationOutputV1(
        occurrence_id=occurrence_id,
        emitted_at=command.emitted_at.astimezone(UTC),
        generation=command.generation,
        scope=command.scope,
        subscription_contract_id=version.contract_id,
        contract_version_id=version.id,
        contract_line_key=line.contract_line_key,
        charge_model_code=line.charge_model_code,
        source_code=line.source_code,
        source_id=line.source_id,
        source_version=line.source_version,
        period_start=period.starts_at,
        period_end=period.ends_at,
        currency=line.currency,
        pre_tax_amount=rating.pre_tax_amount,
        collection_timing=cadence.collection_timing,
        coverage_start=coverage.starts_at.astimezone(UTC),
        coverage_end=coverage.ends_at.astimezone(UTC),
        unit_price=ExactAmount(line.unit_price, line.currency, line.scale),
        quantity=line.quantity,
        rate_basis=cadence.rate_basis,
        rate_unit=cadence.rate_unit,
        rate_quantity=cadence.rate_quantity,
        rate_units=rating.rate_units,
        proration_policy=cadence.proration_policy,
        proration_factor=rating.proration_factor,
        timezone_name=cadence.timezone_name,
        rating_policy_version=version.rating_policy_version,
        offer_version_ref=f"{line.offer_version_id}:{line.offer_version}",
        request_fingerprint=rating.request_fingerprint,
        idempotency_key=idempotency_key,
        corrects_occurrence_id=command.corrects_occurrence_id,
    )

    def operation(session: Session) -> dict[str, object]:
        session.add(
            plane.occurrence(
                **_scope_values(command.scope),
                id=occurrence_id,
                contract_id=version.contract_id,
                contract_version_id=version.id,
                contract_line_key=line.contract_line_key,
                charge_model_code=line.charge_model_code,
                source_code=line.source_code,
                source_id=line.source_id,
                source_version=line.source_version,
                period_start=period.starts_at,
                period_end=period.ends_at,
                currency=line.currency,
                pre_tax_amount=rating.pre_tax_amount.amount,
                amount_scale=rating.pre_tax_amount.scale,
                rating_coverage_start=coverage.starts_at.astimezone(UTC),
                rating_coverage_end=coverage.ends_at.astimezone(UTC),
                rating_unit_price=line.unit_price,
                rating_quantity=line.quantity,
                rating_rate_basis=cadence.rate_basis.value,
                rating_rate_unit=cadence.rate_unit.value,
                rating_rate_quantity=cadence.rate_quantity,
                rating_rate_units=rating.rate_units,
                rating_proration_policy=cadence.proration_policy.value,
                rating_proration_factor=rating.proration_factor,
                rating_timezone_name=cadence.timezone_name,
                rating_policy_version=version.rating_policy_version,
                offer_version_ref=f"{line.offer_version_id}:{line.offer_version}",
                request_fingerprint=rating.request_fingerprint,
                idempotency_key=idempotency_key,
                generation=command.generation,
                state="emitted",
                emitted_at=command.emitted_at.astimezone(UTC),
                output_acknowledged_at=None,
                corrects_occurrence_id=command.corrects_occurrence_id,
                command_id=command.command_id,
                correlation_id=command.correlation_id,
            )
        )
        session.flush()
        next_period = invoice_period(
            cadence=cadence,
            contract_start=contract_start,
            index=command.period_index + 1,
        )
        if contract_end is None or next_period.starts_at < contract_end:
            next_due_at = (
                next_period.starts_at
                if cadence.collection_timing is CollectionTiming.advance
                else next_period.ends_at
            )
            timer.schedule(
                session,
                scope=command.scope,
                contract_line_key=line.contract_line_key,
                due_at=next_due_at,
                recorded_at=command.emitted_at,
            )
            session.flush()
        return {"occurrence_id": str(occurrence_id)}

    try:
        outcome = _execute_once(
            db,
            scope=command.scope,
            operation_scope=_OCCURRENCE_SCOPE,
            key=idempotency_key,
            fingerprint=rating.request_fingerprint,
            correlation_id=command.correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(
            "occurrences.database_conflict",
            "Occurrence identity conflicts with stored rating inputs.",
        ) from exc
    stored_id = UUID(str(outcome.result["occurrence_id"]))
    if stored_id != occurrence_id:
        output = _output_for_id(db, scope=command.scope, occurrence_id=stored_id)
    return OccurrenceResult(stored_id, outcome.replayed, output)


def _output_for_id(
    db: Session, *, scope: Scope, occurrence_id: UUID
) -> RatedObligationOutputV1:
    plane = _models(scope)
    occurrence = cast(
        OccurrenceRow | None,
        db.execute(
            _scoped(
                select(plane.occurrence).where(plane.occurrence.id == occurrence_id),
                scope,
                plane.occurrence,
            )
        ).scalar_one_or_none(),
    )
    if occurrence is None:
        raise SubscriptionDataError(
            "occurrences.not_found", "Recurring charge occurrence was not found."
        )
    version = cast(
        ContractVersionRow,
        db.execute(
            _scoped(
                select(plane.contract_version).where(
                    plane.contract_version.id == occurrence.contract_version_id
                ),
                scope,
                plane.contract_version,
            )
        ).scalar_one(),
    )
    return RatedObligationOutputV1(
        occurrence_id=occurrence.id,
        emitted_at=_stored_utc(occurrence.emitted_at),
        generation=occurrence.generation,
        scope=scope,
        subscription_contract_id=occurrence.contract_id,
        contract_version_id=occurrence.contract_version_id,
        contract_line_key=occurrence.contract_line_key,
        charge_model_code=occurrence.charge_model_code,
        source_code=occurrence.source_code,
        source_id=occurrence.source_id,
        source_version=occurrence.source_version,
        period_start=_stored_utc(occurrence.period_start),
        period_end=_stored_utc(occurrence.period_end),
        currency=occurrence.currency,
        pre_tax_amount=ExactAmount(
            occurrence.pre_tax_amount, occurrence.currency, occurrence.amount_scale
        ),
        collection_timing=CollectionTiming(version.collection_timing),
        coverage_start=_stored_utc(occurrence.rating_coverage_start),
        coverage_end=_stored_utc(occurrence.rating_coverage_end),
        unit_price=ExactAmount(
            occurrence.rating_unit_price, occurrence.currency, occurrence.amount_scale
        ),
        quantity=occurrence.rating_quantity,
        rate_basis=RateBasis(occurrence.rating_rate_basis),
        rate_unit=IntervalUnit(occurrence.rating_rate_unit),
        rate_quantity=occurrence.rating_rate_quantity,
        rate_units=occurrence.rating_rate_units,
        proration_policy=ProrationPolicy(occurrence.rating_proration_policy),
        proration_factor=occurrence.rating_proration_factor,
        timezone_name=occurrence.rating_timezone_name,
        rating_policy_version=occurrence.rating_policy_version,
        offer_version_ref=occurrence.offer_version_ref,
        request_fingerprint=occurrence.request_fingerprint,
        idempotency_key=occurrence.idempotency_key,
        corrects_occurrence_id=occurrence.corrects_occurrence_id,
    )


def unacknowledged_outputs(
    db: Session, *, scope: Scope, limit: int = 100
) -> tuple[RatedObligationOutputV1, ...]:
    if not 1 <= limit <= 1000:
        raise SubscriptionDataError(
            "occurrences.invalid_limit", "Output limit must be between 1 and 1000."
        )
    plane = _models(scope)
    ids = db.execute(
        _scoped(
            select(plane.occurrence.id)
            .where(
                plane.occurrence.state == "emitted",
                plane.occurrence.output_acknowledged_at.is_(None),
            )
            .order_by(plane.occurrence.emitted_at, plane.occurrence.id)
            .limit(limit),
            scope,
            plane.occurrence,
        )
    ).scalars()
    return tuple(_output_for_id(db, scope=scope, occurrence_id=value) for value in ids)


def occurrences_for_contract(
    db: Session, *, scope: Scope, contract_id: UUID
) -> tuple[RatedObligationOutputV1, ...]:
    """Return immutable rated facts in deterministic period and identity order."""
    plane = _models(scope)
    ids = db.execute(
        _scoped(
            select(plane.occurrence.id)
            .where(plane.occurrence.contract_id == contract_id)
            .order_by(
                plane.occurrence.period_start,
                plane.occurrence.contract_line_key,
                plane.occurrence.id,
            ),
            scope,
            plane.occurrence,
        )
    ).scalars()
    return tuple(_output_for_id(db, scope=scope, occurrence_id=value) for value in ids)


def acknowledge_output(
    db: Session, *, scope: Scope, occurrence_id: UUID, acknowledged_at: datetime
) -> None:
    _require_aware(acknowledged_at, "acknowledged_at")
    plane = _models(scope)
    occurrence = cast(
        OccurrenceRow | None,
        db.execute(
            _scoped(
                select(plane.occurrence).where(plane.occurrence.id == occurrence_id),
                scope,
                plane.occurrence,
            )
        ).scalar_one_or_none(),
    )
    if occurrence is None:
        raise SubscriptionDataError(
            "occurrences.not_found", "Occurrence was not found."
        )
    if occurrence.output_acknowledged_at is None:
        occurrence.output_acknowledged_at = acknowledged_at
        db.flush()


__all__ = [
    "OfferCatalogItem",
    "OfferCatalogPage",
    "OfferCatalogPrice",
    "OfferVersionSnapshot",
    "acknowledge_output",
    "approve_billing_arrangement",
    "cadence_of",
    "effective_version_at",
    "end_contract_version",
    "entitlement_projections_for_version",
    "generate_recurring_charge",
    "list_effective_offers",
    "occurrences_for_contract",
    "offer_version_snapshot",
    "publish_offer_version",
    "preview_billing_arrangement",
    "record_non_cash_grant",
    "record_contract_version",
    "resolve_billing_arrangement",
    "revoke_billing_arrangement",
    "unacknowledged_outputs",
    "withdraw_offer_version",
]
