"""Complimentary/sponsored lifecycle on the platform plane; PostgreSQL proves both.

Every case here is a scenario preserved from `dotmac_sub`'s
`tests/test_subscription_billing_treatments.py` and
`tests/test_subscription_billing_grants.py`, re-expressed against the
module-owned contract line instead of Sub's `Subscription`.  The structural
half of each invariant — CHECK constraints, the append-only trigger, the
term-freeze trigger and RLS — is proved on real PostgreSQL in
`tests/test_subscriptions_isolation.py`; SQLite cannot enforce any of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.cache import PlatformScope, Scope
from dotmac_kernel.idempotency_models import PlatformIdempotencyRecord
from dotmac_kernel.modules import ModuleManifest
from dotmac_subscriptions import (
    PORTED_BILLING_TREATMENT_REASONS,
    ApprovalPolicySnapshot,
    ApproveBillingArrangementCommand,
    BillingArrangementDecisionStatus,
    BillingArrangementResult,
    BillingArrangementStatus,
    BillingCadence,
    BillingTreatment,
    BillingTreatmentReasonDeclaration,
    CadenceAlignment,
    CollectionTiming,
    ContractLineInput,
    EndOfMonthRule,
    ExactAmount,
    FakeNonCashGrantPublisher,
    GenerateRecurringChargeCommand,
    IntervalUnit,
    OfferPriceInput,
    OfferPricingMode,
    PreviewBillingArrangementCommand,
    ProrationPolicy,
    PublishOfferVersionCommand,
    RateBasis,
    RecordNonCashGrantCommand,
    RecordSubscriptionContractVersionCommand,
    RevokeBillingArrangementCommand,
    SubscriptionConflictError,
    SubscriptionDataError,
    SubscriptionStateError,
    SubscriptionVocabularyRegistry,
    TimerCancelResult,
    TimerScheduleResult,
    approve_billing_arrangement,
    billing_arrangements_for_line,
    generate_recurring_charge,
    non_cash_grants_for_line,
    preview_billing_arrangement,
    publish_offer_version,
    record_contract_version,
    record_non_cash_grant,
    resolve_billing_arrangement,
    revoke_billing_arrangement,
)
from dotmac_subscriptions.models import (
    PlatformOffer,
    PlatformOfferVersion,
    PlatformOfferVersionPrice,
    PlatformRecurringChargeOccurrence,
    PlatformSubscriptionBillingArrangement,
    PlatformSubscriptionBillingGrant,
    PlatformSubscriptionContract,
    PlatformSubscriptionContractLine,
    PlatformSubscriptionContractVersion,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 1, tzinfo=UTC)
POLICY = ApprovalPolicySnapshot(
    policy_ref="product.billing.treatment_max_days",
    policy_version="v1",
    maximum_days=366,
)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_subscriptions": None}},
    )
    PlatformIdempotencyRecord.__table__.create(engine)
    for model in (
        PlatformOffer,
        PlatformOfferVersion,
        PlatformOfferVersionPrice,
        PlatformSubscriptionContract,
        PlatformSubscriptionContractVersion,
        PlatformSubscriptionContractLine,
        PlatformRecurringChargeOccurrence,
        PlatformSubscriptionBillingArrangement,
        PlatformSubscriptionBillingGrant,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def registry() -> SubscriptionVocabularyRegistry:
    return SubscriptionVocabularyRegistry.from_manifests(
        (
            ModuleManifest(
                code="product",
                version="1.0.0",
                charge_models=("recurring_access",),
                obligation_sources=("accepted_order_line",),
            ),
        )
    )


class FakeTimer:
    def schedule(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        due_at: datetime,
        recorded_at: datetime,
    ) -> TimerScheduleResult:
        del db, scope, contract_line_key, recorded_at
        return TimerScheduleResult(generation=1, due_at=due_at)

    def cancel(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        recorded_at: datetime,
    ) -> TimerCancelResult:
        del db, scope, contract_line_key, recorded_at
        return TimerCancelResult(canceled=True)


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
        timezone_name="UTC",
        end_of_month_rule=EndOfMonthRule.clamp_to_month_end,
        proration_policy=ProrationPolicy.none,
    )


def _offer(
    db: Session, registry: SubscriptionVocabularyRegistry, *, code: str = "access.std"
) -> UUID:
    return publish_offer_version(
        db,
        PublishOfferVersionCommand(
            scope=PlatformScope(),
            offer_id=None,
            offer_code=code,
            offer_name="Standard access",
            charge_model_code="recurring_access",
            pricing_mode=OfferPricingMode.catalog_price,
            version=1,
            prices=(
                OfferPriceInput(
                    price_key="access",
                    charge_model_code="recurring_access",
                    unit_price=ExactAmount(Decimal("100.00"), "EUR", 2),
                    quantity=Decimal("1"),
                ),
            ),
            effective_from=NOW,
            effective_until=None,
            source_code="accepted_order_line",
            source_id=uuid4(),
            source_version=1,
            command_id=uuid4(),
        ),
        registry=registry,
    ).offer_version_id


def _contract(
    db: Session,
    registry: SubscriptionVocabularyRegistry,
    *,
    offer_version_id: UUID,
    contract_id: UUID | None = None,
    key: str = "contract:treatments:1",
    starts_at: datetime = NOW,
) -> tuple[UUID, UUID]:
    source_id = uuid4()
    result = record_contract_version(
        db,
        RecordSubscriptionContractVersionCommand(
            scope=PlatformScope(),
            contract_id=contract_id,
            source_code="accepted_order_line",
            source_id=source_id,
            source_version=1,
            starts_at=starts_at,
            ends_at=None,
            currency="EUR",
            cadence=_cadence(),
            lines=(
                ContractLineInput(
                    contract_line_key=None,
                    charge_model_code="recurring_access",
                    source_code="accepted_order_line",
                    source_id=source_id,
                    source_version=1,
                    description="Access",
                    product_link_ref="product:access:standard",
                    quantity=Decimal("1"),
                    unit_price=ExactAmount(Decimal("100.00"), "EUR", 2),
                    offer_version_id=offer_version_id,
                    offer_version=1,
                    entitlement_codes=("service.standard",),
                ),
            ),
            actor="order-owner",
            reason="accepted order",
            recorded_at=NOW,
            command_id=uuid4(),
            correlation_id=uuid4(),
            idempotency_key=key,
        ),
        registry=registry,
        timer=FakeTimer(),
    )
    return result.version_id, result.line_keys[0]


def _preview_command(
    line_key: UUID, **overrides: object
) -> PreviewBillingArrangementCommand:
    base: dict[str, object] = {
        "scope": PlatformScope(),
        "contract_line_key": line_key,
        "treatment": BillingTreatment.complimentary,
        "reason_code": "internal_service",
        "reason": "Internal monitoring service",
        "starts_at": NOW,
        "ends_at": NOW + timedelta(days=30),
        "approval_policy": POLICY,
        "sponsor_reference": None,
        "cost_center": None,
        "evaluated_at": NOW,
    }
    base.update(overrides)
    return PreviewBillingArrangementCommand(**base)


def _approve(
    db: Session,
    registry: SubscriptionVocabularyRegistry,
    line_key: UUID,
    *,
    key: str = "approve:1",
    **overrides: object,
) -> BillingArrangementResult:
    preview_command = _preview_command(line_key, **overrides)
    preview = preview_billing_arrangement(db, preview_command, registry=registry)
    return approve_billing_arrangement(
        db,
        ApproveBillingArrangementCommand(
            scope=PlatformScope(),
            contract_line_key=line_key,
            treatment=preview.treatment,
            reason_code=preview.reason_code,
            reason=preview.reason,
            starts_at=preview.starts_at,
            ends_at=preview.ends_at,
            approval_policy=POLICY,
            sponsor_reference=preview.sponsor_reference,
            cost_center=preview.cost_center,
            approved_by="finance-owner",
            approved_at=NOW,
            preview_evaluated_at=preview.evaluated_at,
            preview_fingerprint=preview.fingerprint,
            command_id=uuid4(),
            correlation_id=uuid4(),
            idempotency_key=key,
        ),
        registry=registry,
    )


# ── the seven reasons are an OPEN declared registry, not an enum ─────────────


def test_the_seven_ported_reasons_are_declared_and_owned_by_this_module() -> None:
    registry = SubscriptionVocabularyRegistry.from_manifests(())

    assert set(PORTED_BILLING_TREATMENT_REASONS) == {
        "internal_service",
        "staff_benefit",
        "partner_service",
        "community_support",
        "commercial_concession",
        "sponsored_service",
        "other_approved",
    }
    for code in PORTED_BILLING_TREATMENT_REASONS:
        assert registry.require_billing_treatment_reason(code) == "subscriptions"


def test_a_product_may_declare_an_eighth_reason_without_a_module_release() -> None:
    registry = SubscriptionVocabularyRegistry.from_manifests(
        (),
        reason_declarations=(
            BillingTreatmentReasonDeclaration(
                module="isp", codes=("regulatory_universal_service",)
            ),
        ),
    )

    assert (
        registry.require_billing_treatment_reason("regulatory_universal_service")
        == "isp"
    )
    with pytest.raises(SubscriptionDataError, match="undeclared"):
        registry.require_billing_treatment_reason("invented_on_the_spot")


def test_one_reason_code_never_has_two_owners() -> None:
    with pytest.raises(SubscriptionDataError, match="more than one module"):
        SubscriptionVocabularyRegistry.from_manifests(
            (),
            reason_declarations=(
                BillingTreatmentReasonDeclaration(
                    module="isp", codes=("internal_service",)
                ),
            ),
        )


# ── approval ────────────────────────────────────────────────────────────────


def test_an_undeclared_reason_code_is_refused_at_the_write_path(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    with pytest.raises(SubscriptionDataError, match="undeclared"):
        preview_billing_arrangement(
            db,
            _preview_command(line_key, reason_code="made_up"),
            registry=registry,
        )


def test_a_treatment_without_an_end_date_is_refused(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    with pytest.raises(SubscriptionDataError, match="finite end"):
        preview_billing_arrangement(
            db,
            _preview_command(line_key, ends_at=None),
            registry=registry,
        )


def test_a_treatment_beyond_the_supplied_approval_horizon_is_refused(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    short_policy = replace(POLICY, maximum_days=20)

    with pytest.raises(SubscriptionDataError, match="maximum horizon"):
        preview_billing_arrangement(
            db,
            _preview_command(line_key, approval_policy=short_policy),
            registry=registry,
        )


def test_the_approved_ceiling_is_the_positive_contracted_line_value(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    result = _approve(db, registry, line_key)

    assert result.maximum_recurring_amount == ExactAmount(Decimal("100.00"), "EUR", 2)
    assert result.status is BillingArrangementStatus.active
    assert result.replayed is False


def test_sponsored_treatment_requires_sponsor_or_cost_centre_evidence(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    with pytest.raises(SubscriptionDataError, match="sponsor reference or cost centre"):
        preview_billing_arrangement(
            db,
            _preview_command(
                line_key,
                treatment=BillingTreatment.sponsored,
                reason_code="sponsored_service",
            ),
            registry=registry,
        )
    preview = preview_billing_arrangement(
        db,
        _preview_command(
            line_key,
            treatment=BillingTreatment.sponsored,
            reason_code="sponsored_service",
            cost_center="cc-4400",
        ),
        registry=registry,
    )
    assert preview.cost_center == "cc-4400"


def test_a_treatment_must_start_prospectively_and_on_a_service_boundary(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    with pytest.raises(SubscriptionDataError, match="prospectively"):
        preview_billing_arrangement(
            db,
            _preview_command(
                line_key,
                starts_at=NOW - timedelta(days=2),
                ends_at=NOW + timedelta(days=30),
            ),
            registry=registry,
        )
    with pytest.raises(SubscriptionDataError, match="complete contract service period"):
        preview_billing_arrangement(
            db,
            _preview_command(line_key, ends_at=NOW + timedelta(days=17)),
            registry=registry,
        )


def test_approval_replays_on_the_same_key_and_refuses_a_stale_preview(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    preview = preview_billing_arrangement(
        db, _preview_command(line_key), registry=registry
    )
    command = ApproveBillingArrangementCommand(
        scope=PlatformScope(),
        contract_line_key=line_key,
        treatment=preview.treatment,
        reason_code=preview.reason_code,
        reason=preview.reason,
        starts_at=preview.starts_at,
        ends_at=preview.ends_at,
        approval_policy=POLICY,
        sponsor_reference=preview.sponsor_reference,
        cost_center=preview.cost_center,
        approved_by="finance-owner",
        approved_at=NOW,
        preview_evaluated_at=preview.evaluated_at,
        preview_fingerprint=preview.fingerprint,
        command_id=uuid4(),
        correlation_id=uuid4(),
        idempotency_key="approve:1",
    )
    with pytest.raises(SubscriptionConflictError, match="evidence changed"):
        approve_billing_arrangement(
            db,
            replace(
                command,
                preview_fingerprint="0" * 64,
                idempotency_key="approve:stale",
            ),
            registry=registry,
        )

    first = approve_billing_arrangement(db, command, registry=registry)
    second = approve_billing_arrangement(db, command, registry=registry)

    assert second.arrangement_id == first.arrangement_id
    assert second.replayed is True


def test_an_overlapping_treatment_on_one_line_fails_closed(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    _approve(db, registry, line_key)

    with pytest.raises(SubscriptionConflictError, match="overlapping"):
        _approve(
            db,
            registry,
            line_key,
            key="approve:2",
            ends_at=NOW + timedelta(days=61),
        )


# ── revocation ──────────────────────────────────────────────────────────────


def test_revocation_is_prospective_replayable_and_terminal(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    approved = _approve(db, registry, line_key)
    command = RevokeBillingArrangementCommand(
        scope=PlatformScope(),
        arrangement_id=approved.arrangement_id,
        reason="service migrated to a paid plan",
        revoked_by="finance-owner",
        revoked_at=NOW + timedelta(days=5),
        command_id=uuid4(),
        correlation_id=uuid4(),
        idempotency_key="revoke:1",
    )
    revoked = revoke_billing_arrangement(db, command)
    replay = revoke_billing_arrangement(db, command)

    assert revoked.status is BillingArrangementStatus.revoked
    assert replay.replayed is True
    assert revoked.starts_at == NOW
    assert (
        resolve_billing_arrangement(
            db,
            scope=PlatformScope(),
            contract_line_key=line_key,
            as_of=NOW + timedelta(days=6),
        ).status
        is BillingArrangementDecisionStatus.standard
    )


def test_a_revoked_arrangement_cannot_be_revoked_again(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    approved = _approve(db, registry, line_key)
    revoke_billing_arrangement(
        db,
        RevokeBillingArrangementCommand(
            scope=PlatformScope(),
            arrangement_id=approved.arrangement_id,
            reason="first",
            revoked_by="finance-owner",
            revoked_at=NOW + timedelta(days=5),
            command_id=uuid4(),
            correlation_id=uuid4(),
            idempotency_key="revoke:a",
        ),
    )

    with pytest.raises(SubscriptionStateError, match="cannot transition"):
        revoke_billing_arrangement(
            db,
            RevokeBillingArrangementCommand(
                scope=PlatformScope(),
                arrangement_id=approved.arrangement_id,
                reason="second",
                revoked_by="finance-owner",
                revoked_at=NOW + timedelta(days=6),
                command_id=uuid4(),
                correlation_id=uuid4(),
                idempotency_key="revoke:b",
            ),
        )


# ── resolution ──────────────────────────────────────────────────────────────


def test_standard_billing_is_the_absence_of_an_arrangement(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))

    decision = resolve_billing_arrangement(
        db, scope=PlatformScope(), contract_line_key=line_key, as_of=NOW
    )

    assert decision.status is BillingArrangementDecisionStatus.standard
    assert decision.suppress_customer_billing is False
    assert decision.grantable is False


def test_an_effective_arrangement_suppresses_charging_and_permits_a_grant(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, line_key = _contract(db, registry, offer_version_id=_offer(db, registry))
    _approve(db, registry, line_key)

    decision = resolve_billing_arrangement(
        db, scope=PlatformScope(), contract_line_key=line_key, as_of=NOW
    )

    assert decision.status is BillingArrangementDecisionStatus.effective
    assert decision.suppress_customer_billing is True
    assert decision.grantable is True
    assert decision.contracted_amount == ExactAmount(Decimal("100.00"), "EUR", 2)


def test_a_superseding_contract_version_becomes_protected_drift_not_a_grant(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    """PostgreSQL refuses this insert outright; the resolver still fails closed.

    `su_0003` installs `contract_versions_treatment_term_freeze`, so on real
    PostgreSQL a new contract version cannot be recorded while an arrangement
    is open — proved in `tests/test_subscriptions_isolation.py`. SQLite has no
    trigger, which lets this case prove the OTHER half: if such a row ever
    exists anyway (a backfill, an imported history), the resolver suppresses
    charging and refuses a grant rather than inventing coverage.
    """
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    _approve(db, registry, line_key)
    contract_id = billing_arrangements_for_line(
        db, scope=PlatformScope(), contract_line_key=line_key
    )[0].contract_id
    dearer = _offer(db, registry, code="access.dearer")
    _contract(
        db,
        registry,
        offer_version_id=dearer,
        contract_id=contract_id,
        key="contract:treatments:2",
        starts_at=NOW + timedelta(days=1),
    )

    decision = resolve_billing_arrangement(
        db,
        scope=PlatformScope(),
        contract_line_key=line_key,
        as_of=NOW + timedelta(days=2),
    )

    assert decision.status is BillingArrangementDecisionStatus.protected_drift
    assert decision.suppress_customer_billing is True
    assert decision.grantable is False
    assert decision.authorized_contract_version_id == version_id


# ── grants: the G3 invariant ────────────────────────────────────────────────


def _occurrence(
    db: Session,
    registry: SubscriptionVocabularyRegistry,
    *,
    version_id: UUID,
    line_key: UUID,
) -> UUID:
    return generate_recurring_charge(
        db,
        GenerateRecurringChargeCommand(
            scope=PlatformScope(),
            contract_version_id=version_id,
            contract_line_key=line_key,
            period_index=0,
            generation=1,
            emitted_at=NOW,
            command_id=uuid4(),
            correlation_id=uuid4(),
        ),
        registry=registry,
        timer=FakeTimer(),
    ).occurrence_id


def test_a_grant_keeps_the_positive_price_and_records_the_exact_foregone_amount(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    occurrence_id = _occurrence(db, registry, version_id=version_id, line_key=line_key)
    publisher = FakeNonCashGrantPublisher()

    command = RecordNonCashGrantCommand(
        scope=PlatformScope(),
        arrangement_id=approved.arrangement_id,
        recurring_occurrence_id=occurrence_id,
        foregone_amount=None,
        actor="finance-owner",
        recorded_at=NOW,
        command_id=uuid4(),
        correlation_id=uuid4(),
    )
    result = record_non_cash_grant(db, command, publisher=publisher)
    replay = record_non_cash_grant(db, command, publisher=publisher)

    output = result.staged_output
    assert output.contracted_amount == ExactAmount(Decimal("100.00"), "EUR", 2)
    assert output.foregone_amount == ExactAmount(Decimal("100.00"), "EUR", 2)
    assert output.approved_maximum_amount == ExactAmount(Decimal("100.00"), "EUR", 2)
    assert output.treatment == "complimentary"
    assert output.reason_code == "internal_service"
    assert replay.replayed is True
    assert replay.grant_id == result.grant_id
    assert len(publisher.outputs) == 1
    stored = non_cash_grants_for_line(
        db, scope=PlatformScope(), contract_line_key=line_key
    )
    assert len(stored) == 1
    assert stored[0].contracted_amount > 0


def test_a_grant_above_the_approved_cap_is_refused(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    occurrence_id = _occurrence(db, registry, version_id=version_id, line_key=line_key)

    with pytest.raises(SubscriptionDataError, match="never exceeds"):
        record_non_cash_grant(
            db,
            RecordNonCashGrantCommand(
                scope=PlatformScope(),
                arrangement_id=approved.arrangement_id,
                recurring_occurrence_id=occurrence_id,
                foregone_amount=ExactAmount(Decimal("100.01"), "EUR", 2),
                actor="finance-owner",
                recorded_at=NOW,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
        )


def test_a_zero_valued_grant_is_refused_because_it_conceals_the_waiver(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    occurrence_id = _occurrence(db, registry, version_id=version_id, line_key=line_key)

    with pytest.raises(SubscriptionDataError, match="strictly positive"):
        record_non_cash_grant(
            db,
            RecordNonCashGrantCommand(
                scope=PlatformScope(),
                arrangement_id=approved.arrangement_id,
                recurring_occurrence_id=occurrence_id,
                foregone_amount=ExactAmount(Decimal("0.00"), "EUR", 2),
                actor="finance-owner",
                recorded_at=NOW,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
        )


def test_a_partial_grant_below_the_contracted_amount_is_recorded_exactly(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    occurrence_id = _occurrence(db, registry, version_id=version_id, line_key=line_key)

    result = record_non_cash_grant(
        db,
        RecordNonCashGrantCommand(
            scope=PlatformScope(),
            arrangement_id=approved.arrangement_id,
            recurring_occurrence_id=occurrence_id,
            foregone_amount=ExactAmount(Decimal("40.00"), "EUR", 2),
            actor="finance-owner",
            recorded_at=NOW,
            command_id=uuid4(),
            correlation_id=uuid4(),
        ),
    )

    assert result.staged_output.foregone_amount.amount == Decimal("40.00")
    assert result.staged_output.contracted_amount.amount == Decimal("100.00")


def test_a_grant_is_refused_once_the_treatment_drifts(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    occurrence_id = _occurrence(db, registry, version_id=version_id, line_key=line_key)
    revoke_billing_arrangement(
        db,
        RevokeBillingArrangementCommand(
            scope=PlatformScope(),
            arrangement_id=approved.arrangement_id,
            reason="withdrawn",
            revoked_by="finance-owner",
            revoked_at=NOW + timedelta(days=1),
            command_id=uuid4(),
            correlation_id=uuid4(),
            idempotency_key="revoke:drift",
        ),
    )

    with pytest.raises(SubscriptionStateError, match="not effective"):
        record_non_cash_grant(
            db,
            RecordNonCashGrantCommand(
                scope=PlatformScope(),
                arrangement_id=approved.arrangement_id,
                recurring_occurrence_id=occurrence_id,
                foregone_amount=None,
                actor="finance-owner",
                recorded_at=NOW,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
        )


def test_a_grant_never_covers_another_lines_occurrence(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    version_id, line_key = _contract(
        db, registry, offer_version_id=_offer(db, registry)
    )
    approved = _approve(db, registry, line_key)
    other_version_id, other_line_key = _contract(
        db,
        registry,
        offer_version_id=_offer(db, registry, code="access.other"),
        key="contract:treatments:other",
    )
    foreign_occurrence = _occurrence(
        db, registry, version_id=other_version_id, line_key=other_line_key
    )
    del version_id

    with pytest.raises(SubscriptionDataError, match="own contract line"):
        record_non_cash_grant(
            db,
            RecordNonCashGrantCommand(
                scope=PlatformScope(),
                arrangement_id=approved.arrangement_id,
                recurring_occurrence_id=foreign_occurrence,
                foregone_amount=None,
                actor="finance-owner",
                recorded_at=NOW,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
        )
