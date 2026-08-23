"""Canaries for the reusable subscriptions public and behavior contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.modules import ModuleManifest
from dotmac_subscriptions import (
    BillingCadence,
    CadenceAlignment,
    CollectionTiming,
    CommercialEntitlementProjectionV1,
    EndOfMonthRule,
    EntitlementIntent,
    ExactAmount,
    FakeEntitlementProjectionPublisher,
    FakeRatedObligationPublisher,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    RatedObligationOutputV1,
    SubscriptionConflictError,
    SubscriptionDataError,
    SubscriptionVocabularyRegistry,
    canonical_decimal,
    entitlement_projection_fingerprint,
    occurrence_idempotency_key,
    rating_input_fingerprint,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
CONTRACT_ID = UUID("20000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("30000000-0000-0000-0000-000000000001")
LINE_KEY = UUID("40000000-0000-0000-0000-000000000001")
OCCURRENCE_ID = UUID("50000000-0000-0000-0000-000000000001")
SOURCE_ID = UUID("60000000-0000-0000-0000-000000000001")
OFFER_VERSION_ID = UUID("70000000-0000-0000-0000-000000000001")
PROJECTION_ID = UUID("80000000-0000-0000-0000-000000000001")


def _cadence() -> BillingCadence:
    return BillingCadence(
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=IntervalUnit.month,
        rate_quantity=Decimal("1"),
        service_interval_unit=IntervalUnit.month,
        service_interval_count=1,
        invoice_interval_unit=IntervalUnit.month,
        invoice_interval_count=1,
        collection_timing=CollectionTiming.advance,
        alignment=CadenceAlignment.contract_anniversary,
        timezone_name="Africa/Lagos",
        end_of_month_rule=EndOfMonthRule.clamp_to_month_end,
        proration_policy=ProrationPolicy.none,
    )


def _output() -> RatedObligationOutputV1:
    period_start = datetime(2026, 8, 1, tzinfo=UTC)
    period_end = datetime(2026, 9, 1, tzinfo=UTC)
    unit_price = ExactAmount(Decimal("12500.00"), "EUR", 2)
    fingerprint = rating_input_fingerprint(
        unit_price=unit_price,
        quantity=Decimal("1"),
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=IntervalUnit.month,
        rate_quantity=Decimal("1"),
        rate_units=Decimal("1"),
        proration_policy=ProrationPolicy.none,
        proration_factor=Decimal("1"),
        coverage_start=period_start,
        coverage_end=period_end,
        currency="EUR",
        timezone_name="Africa/Lagos",
        rating_policy_version="fixed.v1",
        offer_version_ref=f"{OFFER_VERSION_ID}:3",
    )
    return RatedObligationOutputV1(
        occurrence_id=OCCURRENCE_ID,
        emitted_at=datetime(2026, 8, 1, 0, 1, tzinfo=UTC),
        generation=2,
        scope=TenantScope(TENANT_ID),
        subscription_contract_id=CONTRACT_ID,
        contract_version_id=VERSION_ID,
        contract_line_key=LINE_KEY,
        charge_model_code="recurring_access",
        source_code="accepted_order_line",
        source_id=SOURCE_ID,
        source_version=4,
        period_start=period_start,
        period_end=period_end,
        currency="EUR",
        pre_tax_amount=unit_price,
        collection_timing=CollectionTiming.advance,
        coverage_start=period_start,
        coverage_end=period_end,
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
        offer_version_ref=f"{OFFER_VERSION_ID}:3",
        request_fingerprint=fingerprint,
        idempotency_key=occurrence_idempotency_key(
            scope=TenantScope(TENANT_ID),
            contract_line_key=LINE_KEY,
            contract_version_id=VERSION_ID,
            charge_model_code="recurring_access",
            source_code="accepted_order_line",
            source_id=SOURCE_ID,
            source_version=4,
            period_start=period_start,
            period_end=period_end,
            currency="EUR",
        ),
    )


def test_exact_amount_is_exact_and_wire_stable() -> None:
    amount = ExactAmount(Decimal("12.30"), "EUR", 2)

    assert amount.as_wire() == {"amount": "12.30", "currency": "EUR", "scale": 2}
    assert canonical_decimal(Decimal("12.3000")) == "12.3"


@pytest.mark.parametrize(
    ("amount", "currency", "scale"),
    [
        (Decimal("1.001"), "EUR", 2),
        (Decimal("1"), "eur", 2),
        (Decimal("1"), "", 2),
        (Decimal("1"), "EUR", -1),
    ],
)
def test_exact_amount_refuses_invalid_representation(
    amount: Decimal, currency: str, scale: int
) -> None:
    with pytest.raises(SubscriptionDataError):
        ExactAmount(amount, currency, scale)


def test_exact_amount_refuses_float_even_when_numerically_exact() -> None:
    with pytest.raises(SubscriptionDataError, match="float"):
        ExactAmount(float("1.5"), "EUR", 2)  # type: ignore[arg-type]


def test_rating_fingerprint_is_deterministic_and_input_sensitive() -> None:
    original = _output()
    replay = _output()

    assert replay.request_fingerprint == original.request_fingerprint
    with pytest.raises(SubscriptionConflictError, match="fingerprint"):
        replace(original, proration_factor=Decimal("0.5"))


def test_rated_output_rejects_mixed_currency() -> None:
    with pytest.raises(SubscriptionDataError, match="currency"):
        replace(
            _output(),
            pre_tax_amount=ExactAmount(Decimal("12500.00"), "GBP", 2),
        )


def test_rated_output_is_pre_tax_and_contains_no_financial_resolution() -> None:
    names = set(RatedObligationOutputV1.__dataclass_fields__)

    assert "pre_tax_amount" in names
    assert names.isdisjoint(
        {
            "tax_amount",
            "gross_amount",
            "due_at",
            "invoice_id",
            "payment_id",
            "balance",
            "resolved_amount",
        }
    )


def test_fake_rated_publisher_replays_identical_output_once() -> None:
    publisher = FakeRatedObligationPublisher()
    output = _output()

    first = publisher.stage(output)
    replay = publisher.stage(_output())

    assert first.was_duplicate is False
    assert replay.was_duplicate is True
    assert publisher.outputs == (output,)


def test_fake_rated_publisher_rejects_same_key_with_changed_fingerprint() -> None:
    publisher = FakeRatedObligationPublisher()
    output = _output()
    publisher.stage(output)

    with pytest.raises(SubscriptionConflictError):
        publisher.stage(replace(output, request_fingerprint="0" * 64))


def test_entitlement_projection_is_money_free_and_replayable() -> None:
    effective_from = datetime(2026, 8, 1, tzinfo=UTC)
    fingerprint = entitlement_projection_fingerprint(
        entitlement_codes=("service.standard",),
        quantity=Decimal("1"),
        effective_from=effective_from,
        effective_until=None,
        source_code="accepted_order_line",
        source_id=SOURCE_ID,
        source_version=4,
    )
    projection = CommercialEntitlementProjectionV1(
        projection_id=PROJECTION_ID,
        emitted_at=effective_from,
        scope=PlatformScope(),
        subscription_contract_id=CONTRACT_ID,
        contract_version_id=VERSION_ID,
        contract_line_key=LINE_KEY,
        entitlement_codes=("service.standard",),
        quantity=Decimal("1"),
        intent=EntitlementIntent.intended_effective,
        effective_from=effective_from,
        effective_until=None,
        source_code="accepted_order_line",
        source_id=SOURCE_ID,
        source_version=4,
        idempotency_key="subscriptions:projection:test",
        request_fingerprint=fingerprint,
    )
    publisher = FakeEntitlementProjectionPublisher()

    assert publisher.stage(projection).was_duplicate is False
    assert publisher.stage(projection).was_duplicate is True
    assert "currency" not in projection.__dataclass_fields__
    assert "amount" not in projection.__dataclass_fields__


def test_vocabulary_is_manifest_declared_and_duplicate_ownership_fails() -> None:
    first = ModuleManifest(
        code="product_a",
        version="1.0.0",
        charge_models=("recurring_access",),
        obligation_sources=("accepted_order_line",),
    )
    registry = SubscriptionVocabularyRegistry.from_manifests((first,))

    assert registry.require_charge_model("recurring_access") == "product_a"
    assert registry.require_obligation_source("accepted_order_line") == "product_a"
    with pytest.raises(SubscriptionDataError, match="undeclared"):
        registry.require_charge_model("unknown")

    second = ModuleManifest(
        code="product_b",
        version="1.0.0",
        charge_models=("recurring_access",),
    )
    with pytest.raises(SubscriptionDataError, match="more than one"):
        SubscriptionVocabularyRegistry.from_manifests((first, second))


def test_cadence_is_required_and_has_no_currency_or_timezone_default() -> None:
    fields = BillingCadence.__dataclass_fields__

    assert fields["timezone_name"].default.__class__.__name__ == "_MISSING_TYPE"
    assert "currency" not in fields
    assert _cadence().timezone_name == "Africa/Lagos"
