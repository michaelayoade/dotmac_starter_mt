"""Pure Billing V1 contract and behaviour canaries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from dotmac_billing.contracts import (
    DueDateBasisStatus,
    DueDateBasisV1,
    MoneyV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_billing.engine import (
    BillingRuleViolation,
    CoverageOutcome,
    Effect,
    EffectLane,
    PositionState,
    coverage_of,
    rebuild_position,
)


def test_money_requires_uppercase_iso_precision_and_no_excess_scale() -> None:
    assert MoneyV1(amount=Decimal("12.340000"), currency="NGN", minor_units=2)
    with pytest.raises(ValueError, match="uppercase ISO"):
        MoneyV1(amount=Decimal("1.00"), currency="ngn", minor_units=2)
    with pytest.raises(ValueError, match="float"):
        MoneyV1(amount=1.0, currency="NGN", minor_units=2)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="minor-unit"):
        MoneyV1(amount=Decimal("1.001"), currency="NGN", minor_units=2)
    with pytest.raises(ValueError, match="0 through 6"):
        MoneyV1(amount=Decimal("1"), currency="NGN", minor_units=7)


def test_service_period_evidence_is_structurally_total() -> None:
    ServicePeriodEvidenceV1(status=ServicePeriodStatus.NOT_APPLICABLE)
    with pytest.raises(ValueError, match="requires both"):
        ServicePeriodEvidenceV1(status=ServicePeriodStatus.VERIFIED)
    with pytest.raises(ValueError, match="must not carry"):
        ServicePeriodEvidenceV1(
            status=ServicePeriodStatus.UNKNOWN_UNVERIFIED,
            starts_at=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_unknown_due_date_basis_cannot_drive_automated_collection() -> None:
    basis = DueDateBasisV1.unknown_unverified(
        source_authority="legacy_import", evidence_ref="legacy:42"
    )
    assert basis.status is DueDateBasisStatus.UNKNOWN_UNVERIFIED
    assert not basis.automated_collection_allowed


def test_native_collectible_due_date_requires_verified_basis() -> None:
    basis = DueDateBasisV1.unknown_unverified(
        source_authority="legacy_import", evidence_ref="legacy:42"
    )
    with pytest.raises(BillingRuleViolation, match="verified due-date basis"):
        basis.require_collectible(native=True)


def test_rebuild_keeps_three_lanes_separate_and_hashes_deterministically() -> None:
    effects = (
        Effect(EffectLane.RECEIVABLE, Decimal("120.000000"), "NGN", 2),
        Effect(EffectLane.AVAILABLE_CREDIT, Decimal("20.000000"), "NGN", 2),
        Effect(EffectLane.PREPAID_FUNDING, Decimal("9.000000"), "NGN", 2),
        Effect(EffectLane.RECEIVABLE, Decimal("-20.000000"), "NGN", 2),
    )
    first = rebuild_position(effects, currency="NGN", minor_units=2)
    second = rebuild_position(tuple(reversed(effects)), currency="NGN", minor_units=2)
    assert first == PositionState(
        collectible_receivable=Decimal("100.000000"),
        available_credit=Decimal("20.000000"),
        prepaid_funding=Decimal("9.000000"),
        currency="NGN",
        minor_units=2,
        state_fingerprint=first.state_fingerprint,
    )
    assert first.state_fingerprint == second.state_fingerprint


def test_coverage_is_derived_and_not_a_document_lifecycle() -> None:
    assert (
        coverage_of(Decimal("100"), Decimal("0"), Decimal("0"))
        is CoverageOutcome.UNPAID
    )
    assert (
        coverage_of(Decimal("100"), Decimal("50"), Decimal("0"))
        is CoverageOutcome.PARTIAL
    )
    assert (
        coverage_of(Decimal("100"), Decimal("100"), Decimal("0"))
        is CoverageOutcome.PAID
    )
    assert (
        coverage_of(Decimal("100"), Decimal("101"), Decimal("0"))
        is CoverageOutcome.OVERPAID
    )
