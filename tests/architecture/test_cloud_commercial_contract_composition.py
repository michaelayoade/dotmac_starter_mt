"""Executable contract pairing for the future Dotmac Cloud assembly.

This is deliberately a test composition root.  The reusable modules remain
peers and import neither one another nor an assembly.  The production adapter
must be copied into the real Cloud assembly once that repository is named.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import pytest
from dotmac_billing.contracts import (
    AcceptRatedObligationV1,
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    DueDateBasisStatus,
    DueDateBasisV1,
    ReceivableExposureV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_collections.receivables import ReceivableObservationV1
from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.money import Currency, Money
from dotmac_subscriptions import (
    CollectionTiming,
    ExactAmount,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    RatedObligationOutputV1,
    occurrence_idempotency_key,
    rating_input_fingerprint,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
ACCOUNT_ID = UUID("20000000-0000-0000-0000-000000000001")
CONTRACT_ID = UUID("30000000-0000-0000-0000-000000000001")
CONTRACT_VERSION_ID = UUID("40000000-0000-0000-0000-000000000001")
LINE_KEY = UUID("50000000-0000-0000-0000-000000000001")
OCCURRENCE_ID = UUID("60000000-0000-0000-0000-000000000001")
SOURCE_ID = UUID("70000000-0000-0000-0000-000000000001")
CORRECTED_OCCURRENCE_ID = UUID("80000000-0000-0000-0000-000000000001")
CORRECTED_OBLIGATION_ID = UUID("90000000-0000-0000-0000-000000000001")
POSTING_GROUP_ID = UUID("a0000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class _BillingAcceptanceContext:
    """Product-owned links and frozen financial decisions supplied by Cloud."""

    billing_account_id: UUID
    subject_ref: str
    service_ref: str | None
    currency: Currency
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...] = ()
    fx_snapshot: AppliedFxSnapshotV1 | None = None
    corrected_obligation_id: UUID | None = None


def _exact_money(amount: ExactAmount, currency: Currency) -> Money:
    if amount.currency != currency.code:
        raise ValueError("rated and billing-account currencies differ")
    if amount.scale != currency.minor_units:
        raise ValueError("rated and billing-account minor-unit precision differs")
    value = Money(amount.amount, currency)
    if value.amount != amount.amount:
        raise ValueError("rated amount would be rounded during Billing acceptance")
    return value


def _require_money_identity(value: Money, currency: Currency) -> None:
    if value.currency != currency:
        raise ValueError(
            "tax/FX evidence and rated amount use different money identities"
        )


def _to_billing_obligation(
    output: RatedObligationOutputV1,
    context: _BillingAcceptanceContext,
) -> AcceptRatedObligationV1:
    """Cloud assembly mapping; no recurrence, tax, or receivable decision here."""

    if (output.corrects_occurrence_id is None) != (
        context.corrected_obligation_id is None
    ):
        raise ValueError(
            "a corrected occurrence requires its resolved Billing identity"
        )

    pre_tax = _exact_money(output.pre_tax_amount, context.currency)
    tax = Money.zero(context.currency)
    for snapshot in context.tax_snapshots:
        _require_money_identity(snapshot.taxable_basis, context.currency)
        _require_money_identity(snapshot.tax_amount, context.currency)
        tax += snapshot.tax_amount

    return AcceptRatedObligationV1(
        scope=output.scope,
        billing_account_id=context.billing_account_id,
        contract_line_ref=str(output.contract_line_key),
        contract_version=str(output.contract_version_id),
        charge_component=output.charge_model_code,
        source_system="dotmac-subscriptions",
        source_kind=output.source_code,
        source_fact_id=str(output.source_id),
        source_fact_version=str(output.source_version),
        subject_ref=context.subject_ref,
        service_ref=context.service_ref,
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.VERIFIED,
            starts_at=output.period_start,
            ends_at=output.period_end,
        ),
        collection_timing=output.collection_timing.value,
        pre_tax_amount=pre_tax,
        tax_amount=tax,
        total_amount=pre_tax + tax,
        rated_at=output.emitted_at,
        price_version_id=output.offer_version_ref,
        tax_snapshots=context.tax_snapshots,
        fx_snapshot=context.fx_snapshot,
        supersedes_obligation_id=context.corrected_obligation_id,
    )


def _to_collections_observation(
    exposure: ReceivableExposureV1,
    *,
    reason_code: str,
) -> ReceivableObservationV1:
    """Cloud assembly mapping from Billing's exposure, never its aggregate."""

    service_period_status: Literal["not_applicable", "verified", "unknown_unverified"]
    if exposure.service_period.status is ServicePeriodStatus.VERIFIED:
        service_period_status = "verified"
    elif exposure.service_period.status is ServicePeriodStatus.NOT_APPLICABLE:
        service_period_status = "not_applicable"
    else:
        service_period_status = "unknown_unverified"
    due_date_status: Literal["verified", "unknown_unverified"]
    if exposure.due_date_basis.status is DueDateBasisStatus.VERIFIED:
        due_date_status = "verified"
    else:
        due_date_status = "unknown_unverified"
    return ReceivableObservationV1(
        scope=exposure.scope,
        source_owner=exposure.source_owner,
        exposure_ref=exposure.exposure_ref,
        source_version=exposure.source_version,
        state_fingerprint=exposure.state_fingerprint,
        subject_ref=exposure.subject_ref,
        service_ref=exposure.service_ref,
        collection_timing=exposure.collection_timing,
        reason_code=reason_code,
        collectible_receivable=exposure.collectible_receivable,
        service_period_status=service_period_status,
        service_period_starts_at=exposure.service_period.starts_at,
        service_period_ends_at=exposure.service_period.ends_at,
        due_at=exposure.due_at,
        due_date_status=due_date_status,
        financial_state=exposure.financial_state,
        source_authority=exposure.source_authority,
        projection_mode=exposure.projection_mode,
        completeness=exposure.completeness,
        completeness_reason_code=exposure.completeness_reason_code,
        observed_at=exposure.observed_at,
    )


def _rated_output(
    *,
    currency: str = "NGN",
    scale: int = 2,
    amount: Decimal = Decimal("12500.00"),
) -> RatedObligationOutputV1:
    scope = TenantScope(TENANT_ID)
    period_start = datetime(2026, 8, 1, tzinfo=UTC)
    period_end = datetime(2026, 9, 1, tzinfo=UTC)
    coverage_start = datetime(2026, 8, 2, tzinfo=UTC)
    coverage_end = datetime(2026, 8, 31, tzinfo=UTC)
    unit_price = ExactAmount(amount, currency, scale)
    fingerprint = rating_input_fingerprint(
        unit_price=unit_price,
        quantity=Decimal("1"),
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=IntervalUnit.month,
        rate_quantity=Decimal("1"),
        rate_units=Decimal("1"),
        proration_policy=ProrationPolicy.none,
        proration_factor=Decimal("1"),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        currency=currency,
        timezone_name="Africa/Lagos",
        rating_policy_version="fixed.v1",
        offer_version_ref="offer-version:3",
    )
    return RatedObligationOutputV1(
        occurrence_id=OCCURRENCE_ID,
        emitted_at=datetime(2026, 8, 1, 0, 1, tzinfo=UTC),
        generation=1,
        scope=scope,
        subscription_contract_id=CONTRACT_ID,
        contract_version_id=CONTRACT_VERSION_ID,
        contract_line_key=LINE_KEY,
        charge_model_code="recurring_access",
        source_code="accepted_order_line",
        source_id=SOURCE_ID,
        source_version=4,
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        pre_tax_amount=unit_price,
        collection_timing=CollectionTiming.advance,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        unit_price=unit_price,
        quantity=Decimal("1"),
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=IntervalUnit.month,
        rate_quantity=Decimal("1"),
        rate_units=Decimal("1"),
        proration_policy=ProrationPolicy.none,
        proration_factor=Decimal("1"),
        timezone_name="Africa/Lagos",
        rating_policy_version="fixed.v1",
        offer_version_ref="offer-version:3",
        request_fingerprint=fingerprint,
        idempotency_key=occurrence_idempotency_key(
            scope=scope,
            contract_line_key=LINE_KEY,
            contract_version_id=CONTRACT_VERSION_ID,
            charge_model_code="recurring_access",
            source_code="accepted_order_line",
            source_id=SOURCE_ID,
            source_version=4,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
        ),
    )


def _context() -> _BillingAcceptanceContext:
    currency = Currency("NGN", 2)
    tax = Money.of("937.50", currency)
    return _BillingAcceptanceContext(
        billing_account_id=ACCOUNT_ID,
        subject_ref="customer:123",
        service_ref="service:456",
        currency=currency,
        tax_snapshots=(
            AppliedTaxSnapshotV1(
                treatment_code="vat.standard",
                jurisdiction_code="NG-FCT",
                policy_id="tax-policy",
                policy_version="7",
                rate=Decimal("0.075"),
                taxable_basis=Money.of("12500.00", currency),
                tax_amount=tax,
            ),
        ),
    )


def test_subscriptions_output_maps_to_billing_without_identity_or_period_drift() -> (
    None
):
    output = _rated_output()

    command = _to_billing_obligation(output, _context())

    assert command.scope == output.scope
    assert command.contract_line_ref == str(output.contract_line_key)
    assert command.contract_version == str(output.contract_version_id)
    assert command.source_fact_id == str(output.source_id)
    assert command.source_fact_version == str(output.source_version)
    assert command.service_period.starts_at == output.period_start
    assert command.service_period.ends_at == output.period_end
    assert command.service_period.starts_at != output.coverage_start
    assert command.service_period.ends_at != output.coverage_end
    assert command.collection_timing == "advance"
    assert command.pre_tax_amount == Money.of("12500.00", Currency("NGN", 2))
    assert command.tax_amount == Money.of("937.50", Currency("NGN", 2))
    assert command.total_amount == Money.of("13437.50", Currency("NGN", 2))


def test_timer_generation_is_provenance_not_billing_identity() -> None:
    output = _rated_output()

    first = _to_billing_obligation(output, _context())
    redelivery = _to_billing_obligation(replace(output, generation=9), _context())

    assert redelivery == first


def test_mapping_refuses_implicit_rounding_or_currency_precision_coercion() -> None:
    output = _rated_output(scale=3, amount=Decimal("12500.000"))

    with pytest.raises(ValueError, match="precision"):
        _to_billing_obligation(output, _context())


def test_correction_requires_the_assembly_correlation_lookup() -> None:
    corrected = replace(_rated_output(), corrects_occurrence_id=CORRECTED_OCCURRENCE_ID)

    with pytest.raises(ValueError, match="resolved Billing identity"):
        _to_billing_obligation(corrected, _context())

    command = _to_billing_obligation(
        corrected,
        replace(_context(), corrected_obligation_id=CORRECTED_OBLIGATION_ID),
    )
    assert command.supersedes_obligation_id == CORRECTED_OBLIGATION_ID


def _receivable_exposure(scope: Scope) -> ReceivableExposureV1:
    currency = Currency("NGN", 2)
    return ReceivableExposureV1(
        scope=scope,
        source_owner="dotmac-billing",
        exposure_ref="invoice:INV-001",
        billing_account_id=ACCOUNT_ID,
        subject_ref="customer:123",
        service_ref="service:456",
        collection_timing="advance",
        source_version=8,
        posting_group_watermark=POSTING_GROUP_ID,
        source_authority="internal",
        projection_mode="authoritative",
        derived_from="posting_groups",
        completeness="complete",
        completeness_reason_code=None,
        state_fingerprint="a" * 64,
        observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        service_period=ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.VERIFIED,
            starts_at=datetime(2026, 9, 1, tzinfo=UTC),
            ends_at=datetime(2026, 10, 1, tzinfo=UTC),
        ),
        due_at=datetime(2026, 8, 1, tzinfo=UTC),
        due_date_basis=DueDateBasisV1(
            status=DueDateBasisStatus.VERIFIED,
            source_authority="billing",
            evidence_ref="terms:net-0",
            payment_terms_code="net-0",
            payment_terms_version="1",
            issued_at=datetime(2026, 8, 1, tzinfo=UTC),
            effective_at=datetime(2026, 8, 1, tzinfo=UTC),
            timezone="Africa/Lagos",
            derivation_policy="calendar-day",
            derivation_version="1",
        ),
        financial_state="open",
        collectible_receivable=Money.of("13437.50", currency),
    )


def test_billing_exposure_maps_to_collections_without_aggregate_lanes() -> None:
    exposure = _receivable_exposure(TenantScope(TENANT_ID))

    observation = _to_collections_observation(
        exposure, reason_code="receivable.changed"
    )

    assert observation.scope == exposure.scope
    assert observation.exposure_ref == exposure.exposure_ref
    assert observation.subject_ref == exposure.subject_ref
    assert observation.service_ref == exposure.service_ref
    assert observation.collectible_receivable == exposure.collectible_receivable
    assert observation.service_period_starts_at == exposure.service_period.starts_at
    assert observation.service_period_ends_at == exposure.service_period.ends_at
    assert observation.due_date_status == "verified"
    assert (
        observation.automated_collection_blocker(
            as_of=datetime(2026, 8, 15, tzinfo=UTC)
        )
        == "service_period_not_started"
    )
    assert {"available_credit", "prepaid_funding", "billing_account_id"}.isdisjoint(
        observation.__dataclass_fields__
    )
