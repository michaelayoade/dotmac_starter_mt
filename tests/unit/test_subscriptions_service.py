"""One service behavior on the platform plane; PostgreSQL proves both planes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.cache import PlatformScope, Scope
from dotmac_kernel.idempotency_models import PlatformIdempotencyRecord
from dotmac_kernel.modules import ModuleManifest
from dotmac_subscriptions import (
    BillingCadence,
    CadenceAlignment,
    CollectionTiming,
    ContractLineInput,
    EndContractVersionCommand,
    EndOfMonthRule,
    ExactAmount,
    GenerateRecurringChargeCommand,
    IntervalUnit,
    OfferCatalogPage,
    OfferPriceInput,
    OfferPricingMode,
    ProrationPolicy,
    PublishOfferVersionCommand,
    RateBasis,
    RecordSubscriptionContractVersionCommand,
    SubscriptionConflictError,
    SubscriptionDataError,
    SubscriptionVocabularyRegistry,
    TimerCancelResult,
    TimerScheduleResult,
    WithdrawOfferVersionCommand,
    cadence_of,
    effective_version_at,
    end_contract_version,
    generate_recurring_charge,
    list_effective_offers,
    offer_version_snapshot,
    publish_offer_version,
    record_contract_version,
    unacknowledged_outputs,
    withdraw_offer_version,
)
from dotmac_subscriptions.models import (
    PlatformOffer,
    PlatformOfferVersion,
    PlatformOfferVersionPrice,
    PlatformRecurringChargeOccurrence,
    PlatformSubscriptionContract,
    PlatformSubscriptionContractLine,
    PlatformSubscriptionContractVersion,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 18, tzinfo=UTC)


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
                charge_models=("recurring_access", "dedicated_negotiated"),
                obligation_sources=("accepted_order_line",),
            ),
        )
    )


class FakeTimer:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, datetime]] = []
        self.cancel_calls: list[UUID] = []

    def schedule(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        due_at: datetime,
        recorded_at: datetime,
    ) -> TimerScheduleResult:
        del db, scope, recorded_at
        self.calls.append((contract_line_key, due_at))
        return TimerScheduleResult(generation=1, due_at=due_at)

    def cancel(
        self,
        db: Session,
        *,
        scope: Scope,
        contract_line_key: UUID,
        recorded_at: datetime,
    ) -> TimerCancelResult:
        del db, scope, recorded_at
        self.cancel_calls.append(contract_line_key)
        return TimerCancelResult(canceled=True)


def _cadence(timing: CollectionTiming = CollectionTiming.advance) -> BillingCadence:
    return BillingCadence(
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=IntervalUnit.month,
        rate_quantity=Decimal("1"),
        service_interval_unit=IntervalUnit.month,
        service_interval_count=1,
        invoice_interval_unit=IntervalUnit.month,
        invoice_interval_count=1,
        collection_timing=timing,
        alignment=CadenceAlignment.contract_anniversary,
        timezone_name="Africa/Lagos",
        end_of_month_rule=EndOfMonthRule.clamp_to_month_end,
        proration_policy=ProrationPolicy.none,
    )


def _publish(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> tuple[UUID, PublishOfferVersionCommand]:
    command = PublishOfferVersionCommand(
        scope=PlatformScope(),
        offer_id=None,
        offer_code="access.standard",
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
    )
    result = publish_offer_version(db, command, registry=registry)
    return result.offer_version_id, command


def _contract_command(
    offer_version_id: UUID,
    *,
    contract_id: UUID | None = None,
    source_id: UUID | None = None,
    line_key: UUID | None = None,
    starts_at: datetime = NOW,
    recorded_at: datetime = NOW,
    idempotency_key: str = "contract:test:1",
) -> RecordSubscriptionContractVersionCommand:
    source_id = source_id or uuid4()
    return RecordSubscriptionContractVersionCommand(
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
                contract_line_key=line_key or uuid4(),
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
        recorded_at=recorded_at,
        command_id=uuid4(),
        correlation_id=uuid4(),
        idempotency_key=idempotency_key,
    )


def test_contract_priced_offer_has_no_fake_reference_price_and_line_stays_positive(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, template = _publish(db, registry)
    price_less = replace(
        template,
        offer_id=None,
        offer_code="access.dedicated",
        offer_name="Dedicated negotiated access",
        charge_model_code="dedicated_negotiated",
        pricing_mode=OfferPricingMode.contract_price,
        prices=(),
        source_id=uuid4(),
        command_id=uuid4(),
    )
    published = publish_offer_version(db, price_less, registry=registry)
    snapshot = offer_version_snapshot(
        db, scope=PlatformScope(), offer_version_id=published.offer_version_id
    )
    assert snapshot.pricing_mode is OfferPricingMode.contract_price
    assert snapshot.charge_model_code == "dedicated_negotiated"
    assert snapshot.prices == ()

    command = _contract_command(published.offer_version_id)
    line = replace(
        command.lines[0],
        charge_model_code="dedicated_negotiated",
        product_link_ref="product:access:dedicated",
    )
    zero_line = replace(
        command,
        lines=(replace(line, unit_price=ExactAmount(Decimal("0.00"), "EUR", 2)),),
    )
    with pytest.raises(SubscriptionDataError, match="strictly positive"):
        record_contract_version(db, zero_line, registry=registry, timer=FakeTimer())
    recorded = record_contract_version(
        db, replace(command, lines=(line,)), registry=registry, timer=FakeTimer()
    )
    assert recorded.version == 1


def test_catalog_priced_offer_requires_a_positive_reference_price(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, template = _publish(db, registry)
    with pytest.raises(SubscriptionDataError, match="catalog-priced"):
        publish_offer_version(
            db,
            replace(template, offer_id=None, prices=(), command_id=uuid4()),
            registry=registry,
        )
    zero = replace(
        template.prices[0], unit_price=ExactAmount(Decimal("0.00"), "EUR", 2)
    )
    with pytest.raises(SubscriptionDataError, match="strictly positive"):
        publish_offer_version(
            db,
            replace(
                template,
                offer_id=None,
                offer_code="access.zero",
                prices=(zero,),
                source_id=uuid4(),
                command_id=uuid4(),
            ),
            registry=registry,
        )


def test_publish_contract_rate_and_replay_share_one_transactional_path(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, publish_command = _publish(db, registry)
    assert (
        publish_offer_version(db, publish_command, registry=registry).was_duplicate
        is True
    )

    line_key = uuid4()
    source_id = uuid4()
    command = RecordSubscriptionContractVersionCommand(
        scope=PlatformScope(),
        contract_id=None,
        source_code="accepted_order_line",
        source_id=source_id,
        source_version=1,
        starts_at=NOW,
        ends_at=None,
        currency="EUR",
        cadence=_cadence(),
        lines=(
            ContractLineInput(
                contract_line_key=line_key,
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
        idempotency_key="contract:accepted-order:1",
    )
    timer = FakeTimer()
    first = record_contract_version(db, command, registry=registry, timer=timer)
    replay = record_contract_version(db, command, registry=registry, timer=timer)

    assert replay == first.__class__(
        contract_id=first.contract_id,
        version_id=first.version_id,
        version=1,
        line_keys=(line_key,),
        staged_entitlement_outputs=first.staged_entitlement_outputs,
        replayed=True,
    )
    assert len(first.staged_entitlement_outputs) == 1
    assert first.staged_entitlement_outputs[0].entitlement_codes == (
        "service.standard",
    )
    assert len(timer.calls) == 1
    assert (
        effective_version_at(
            db, scope=PlatformScope(), contract_id=first.contract_id, moment=NOW
        )
        == first.version_id
    )

    generate = GenerateRecurringChargeCommand(
        scope=PlatformScope(),
        contract_version_id=first.version_id,
        contract_line_key=line_key,
        period_index=0,
        generation=1,
        emitted_at=NOW,
        command_id=uuid4(),
        correlation_id=uuid4(),
    )
    occurrence = generate_recurring_charge(db, generate, registry=registry, timer=timer)
    duplicate = generate_recurring_charge(db, generate, registry=registry, timer=timer)

    assert occurrence.replayed is False
    assert occurrence.staged_output.pre_tax_amount == ExactAmount(
        Decimal("100.00"), "EUR", 2
    )
    assert duplicate.occurrence_id == occurrence.occurrence_id
    assert duplicate.replayed is True
    assert len(timer.calls) == 2
    assert unacknowledged_outputs(db, scope=PlatformScope()) == (
        occurrence.staged_output,
    )

    ended_at = datetime(2026, 9, 1, tzinfo=UTC)
    ended = end_contract_version(
        db,
        EndContractVersionCommand(
            scope=PlatformScope(),
            contract_version_id=first.version_id,
            ended_at=ended_at,
            actor="contract-owner",
            reason="commercial term ended",
            command_id=uuid4(),
        ),
        timer=timer,
    )
    assert ended.replayed is False
    assert ended.staged_entitlement_outputs[0].effective_from == ended_at
    assert (
        ended.staged_entitlement_outputs[0].supersedes_projection_id
        == first.staged_entitlement_outputs[0].projection_id
    )
    assert timer.cancel_calls == [line_key]


def test_same_offer_version_with_a_different_command_is_a_conflict(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, command = _publish(db, registry)

    with pytest.raises(SubscriptionConflictError, match="already published"):
        publish_offer_version(
            db,
            replace(command, command_id=uuid4()),
            registry=registry,
        )


def test_effective_offer_catalog_selects_one_latest_version_with_exact_prices(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    first_version_id, first_command = _publish(db, registry)
    first_version = db.get(PlatformOfferVersion, first_version_id)
    assert first_version is not None
    boundary = datetime(2026, 9, 1, tzinfo=UTC)
    second = publish_offer_version(
        db,
        replace(
            first_command,
            offer_id=first_version.offer_id,
            version=2,
            prices=(
                replace(
                    first_command.prices[0],
                    unit_price=ExactAmount(Decimal("125.50"), "EUR", 2),
                ),
            ),
            effective_from=boundary,
            source_version=2,
            command_id=uuid4(),
        ),
        registry=registry,
    )

    before = list_effective_offers(
        db, scope=PlatformScope(), effective_at=NOW, limit=20, offset=0
    )
    after = list_effective_offers(
        db, scope=PlatformScope(), effective_at=boundary, limit=20, offset=0
    )

    assert before == OfferCatalogPage(
        items=before.items,
        total=1,
        limit=20,
        offset=0,
        effective_at=NOW,
    )
    assert before.items[0].offer_version_id == first_version_id
    assert after.items[0].offer_version_id == second.offer_version_id
    assert after.items[0].code == "access.standard"
    assert after.items[0].name == "Standard access"
    assert after.items[0].prices[0].unit_price == ExactAmount(
        Decimal("125.50"), "EUR", 2
    )


def test_effective_offer_catalog_searches_and_pages_stable_offers(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, command = _publish(db, registry)
    for code, name in (
        ("access.business", "Business Fiber"),
        ("access.home", "Home Fiber"),
    ):
        publish_offer_version(
            db,
            replace(
                command,
                offer_id=None,
                offer_code=code,
                offer_name=name,
                source_id=uuid4(),
                command_id=uuid4(),
            ),
            registry=registry,
        )

    page = list_effective_offers(
        db,
        scope=PlatformScope(),
        effective_at=NOW,
        search="fiber",
        limit=1,
        offset=1,
    )

    assert page.total == 2
    assert page.limit == 1
    assert page.offset == 1
    assert [item.name for item in page.items] == ["Home Fiber"]


def test_effective_offer_catalog_treats_like_wildcards_as_literal_search(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    _, command = _publish(db, registry)
    for code, name in (
        ("access.fifty-percent", "Fiber 50%"),
        ("access.five-hundred", "Fiber 500"),
    ):
        publish_offer_version(
            db,
            replace(
                command,
                offer_id=None,
                offer_code=code,
                offer_name=name,
                source_id=uuid4(),
                command_id=uuid4(),
            ),
            registry=registry,
        )

    page = list_effective_offers(
        db,
        scope=PlatformScope(),
        effective_at=NOW,
        search="50%",
    )

    assert page.total == 1
    assert [item.code for item in page.items] == ["access.fifty-percent"]


def test_effective_offer_catalog_excludes_withdrawn_versions(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    withdraw_offer_version(
        db,
        WithdrawOfferVersionCommand(
            scope=PlatformScope(),
            offer_version_id=offer_version_id,
            reason="no longer sold",
            command_id=uuid4(),
            withdrawn_at=NOW,
        ),
    )

    page = list_effective_offers(
        db,
        scope=PlatformScope(),
        effective_at=NOW,
    )

    assert page.total == 0
    assert page.items == ()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"offset": -1}, "offset"),
        ({"search": "x" * 201}, "search"),
    ],
)
def test_effective_offer_catalog_rejects_unbounded_queries(
    db: Session, kwargs: dict[str, object], match: str
) -> None:
    with pytest.raises(SubscriptionDataError, match=match):
        list_effective_offers(
            db,
            scope=PlatformScope(),
            effective_at=NOW,
            **kwargs,
        )


def test_contract_refuses_duplicate_charge_component_and_product_link(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    command = _contract_command(offer_version_id)
    repeated = replace(command.lines[0], contract_line_key=uuid4())

    with pytest.raises(SubscriptionDataError, match="cannot repeat"):
        record_contract_version(
            db,
            replace(command, lines=(command.lines[0], repeated)),
            registry=registry,
            timer=FakeTimer(),
        )


@pytest.mark.parametrize("invalid_key", ["", "x" * 201])
def test_contract_requires_a_bounded_idempotency_key(
    db: Session,
    registry: SubscriptionVocabularyRegistry,
    invalid_key: str,
) -> None:
    offer_version_id, _ = _publish(db, registry)

    with pytest.raises(SubscriptionDataError, match="Idempotency key"):
        record_contract_version(
            db,
            _contract_command(offer_version_id, idempotency_key=invalid_key),
            registry=registry,
            timer=FakeTimer(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("actor", ""), ("actor", "   "), ("reason", ""), ("reason", "   ")],
)
def test_contract_requires_actor_and_reason_provenance(
    db: Session,
    registry: SubscriptionVocabularyRegistry,
    field: str,
    value: str,
) -> None:
    offer_version_id, _ = _publish(db, registry)
    command = _contract_command(offer_version_id)

    with pytest.raises(SubscriptionDataError, match="actor"):
        record_contract_version(
            db,
            replace(command, **{field: value}),
            registry=registry,
            timer=FakeTimer(),
        )


def test_contract_refuses_mixed_currency_lines(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    command = _contract_command(offer_version_id)

    with pytest.raises(SubscriptionDataError, match="contract currency"):
        record_contract_version(
            db,
            replace(
                command,
                lines=(
                    replace(
                        command.lines[0],
                        unit_price=ExactAmount(Decimal("100.00"), "GBP", 2),
                    ),
                ),
            ),
            registry=registry,
            timer=FakeTimer(),
        )


def test_contract_supersession_is_contiguous_and_preserves_line_lineage(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    line_key = uuid4()
    source_id = uuid4()
    first_command = _contract_command(
        offer_version_id,
        source_id=source_id,
        line_key=line_key,
    )
    timer = FakeTimer()
    first = record_contract_version(
        db,
        first_command,
        registry=registry,
        timer=timer,
    )
    boundary = datetime(2026, 9, 1, tzinfo=UTC)
    second_command = replace(
        _contract_command(
            offer_version_id,
            contract_id=first.contract_id,
            source_id=source_id,
            line_key=line_key,
            starts_at=boundary,
            recorded_at=boundary,
            idempotency_key="contract:test:2",
        ),
        lines=(replace(first_command.lines[0], quantity=Decimal("2")),),
    )
    second = record_contract_version(
        db,
        second_command,
        registry=registry,
        timer=timer,
    )

    stored_first = db.get(PlatformSubscriptionContractVersion, first.version_id)
    assert stored_first is not None
    assert stored_first.state == "superseded"
    assert stored_first.ends_at == boundary.replace(tzinfo=None)
    assert second.version == 2
    assert second.line_keys == (line_key,)
    assert (
        cadence_of(
            db,
            scope=PlatformScope(),
            contract_version_id=second.version_id,
        )
        == _cadence()
    )
    assert (
        effective_version_at(
            db,
            scope=PlatformScope(),
            contract_id=first.contract_id,
            moment=boundary,
        )
        == second.version_id
    )


def test_same_occurrence_identity_with_different_coverage_is_a_conflict(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    command = _contract_command(offer_version_id)
    timer = FakeTimer()
    contract = record_contract_version(
        db,
        command,
        registry=registry,
        timer=timer,
    )
    generate = GenerateRecurringChargeCommand(
        scope=PlatformScope(),
        contract_version_id=contract.version_id,
        contract_line_key=contract.line_keys[0],
        period_index=0,
        generation=1,
        emitted_at=NOW,
        command_id=uuid4(),
        correlation_id=uuid4(),
    )
    generate_recurring_charge(db, generate, registry=registry, timer=timer)

    with pytest.raises(SubscriptionConflictError, match="different"):
        generate_recurring_charge(
            db,
            replace(
                generate,
                coverage=(NOW, datetime(2026, 8, 20, tzinfo=UTC)),
                generation=2,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
            registry=registry,
            timer=timer,
        )


def test_corrupt_recorded_rating_fingerprint_fails_replay(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    offer_version_id, _ = _publish(db, registry)
    timer = FakeTimer()
    contract = record_contract_version(
        db,
        _contract_command(offer_version_id),
        registry=registry,
        timer=timer,
    )
    generate = GenerateRecurringChargeCommand(
        scope=PlatformScope(),
        contract_version_id=contract.version_id,
        contract_line_key=contract.line_keys[0],
        period_index=0,
        generation=1,
        emitted_at=NOW,
        command_id=uuid4(),
        correlation_id=uuid4(),
    )
    first = generate_recurring_charge(db, generate, registry=registry, timer=timer)
    stored = db.get(PlatformRecurringChargeOccurrence, first.occurrence_id)
    assert stored is not None
    stored.request_fingerprint = "0" * 64
    db.flush()

    with pytest.raises(SubscriptionConflictError, match="fingerprint"):
        generate_recurring_charge(db, generate, registry=registry, timer=timer)


def test_occurrence_generation_requires_an_existing_contract_version(
    db: Session, registry: SubscriptionVocabularyRegistry
) -> None:
    with pytest.raises(SubscriptionDataError, match="not found"):
        generate_recurring_charge(
            db,
            GenerateRecurringChargeCommand(
                scope=PlatformScope(),
                contract_version_id=uuid4(),
                contract_line_key=uuid4(),
                period_index=0,
                generation=1,
                emitted_at=NOW,
                command_id=uuid4(),
                correlation_id=uuid4(),
            ),
            registry=registry,
            timer=FakeTimer(),
        )
