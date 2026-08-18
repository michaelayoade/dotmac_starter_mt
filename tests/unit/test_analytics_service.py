"""Source-parity and hardening tests for the analytics metric store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from dotmac_analytics import (
    DimensionKind,
    DimensionSpec,
    DimensionValue,
    InvalidAnalyticsContract,
    MetricDeclaration,
    MetricDeclarationRegistry,
    MetricGranularity,
    MetricIdentityConflict,
    MetricPointInput,
    MetricSelector,
    MetricValueKind,
    RecordMetricBatchCommand,
    SourceProvenance,
    compare_periods,
    get_history,
    get_latest,
    projection_digest,
    rebuild_projection,
    record_batch,
)
from dotmac_analytics.models import (
    TENANT_MODELS,
    MetricIngestReceipt,
    MetricObservation,
    MetricPoint,
)
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT_ID = UUID("00000000-0000-0000-0000-000000000099")
START = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_analytics": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            IdempotencyRecord.__table__,
            *(model.__table__ for model in TENANT_MODELS),
        ],
    )
    session = Session(engine)
    session.add_all(
        [
            Tenant(id=TENANT_ID, slug="one", name="One"),
            Tenant(id=OTHER_TENANT_ID, slug="two", name="Two"),
        ]
    )
    session.flush()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def declarations() -> MetricDeclarationRegistry:
    return MetricDeclarationRegistry(
        (
            MetricDeclaration(
                owner_code="billing",
                metric_code="billing.revenue.collected",
                schema_version=1,
                display_name="Collected revenue",
                value_kind=MetricValueKind.MONEY,
                unit_code="money",
                granularities=(MetricGranularity.DAY,),
                dimensions=(
                    DimensionSpec("channel", DimensionKind.ENUM, ("bank", "cash")),
                ),
            ),
        )
    )


def _command(
    *,
    tenant_id: UUID = TENANT_ID,
    event_id: str = "billing-day-1",
    value: str = "100.000000000001",
    observed_at: datetime = START + timedelta(days=1),
    received_at: datetime | None = None,
    channel: str = "bank",
    period_start: datetime = START,
) -> RecordMetricBatchCommand:
    return RecordMetricBatchCommand(
        tenant_id=tenant_id,
        provenance=SourceProvenance(
            source_owner="billing",
            source_event_id=event_id,
            source_schema_version=1,
            source_reference=f"billing-run:{event_id}",
            adapter_code="billing.analytics.v1",
        ),
        observed_at=observed_at,
        received_at=received_at or observed_at + timedelta(seconds=1),
        points=(
            MetricPointInput(
                metric_code="billing.revenue.collected",
                schema_version=1,
                period_start=period_start,
                period_end=period_start + timedelta(days=1),
                granularity=MetricGranularity.DAY,
                value=Decimal(value),
                currency_code="NGN",
                dimensions=(DimensionValue("channel", channel),),
            ),
        ),
    )


def _selector(channel: str = "bank") -> MetricSelector:
    return MetricSelector(
        metric_code="billing.revenue.collected",
        schema_version=1,
        granularity=MetricGranularity.DAY,
        dimensions=(DimensionValue("channel", channel),),
        currency_code="NGN",
    )


def test_ingest_replay_and_changed_identity_conflict(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    first = record_batch(db, command=_command(), declarations=declarations)
    replay = record_batch(
        db,
        command=_command(received_at=START + timedelta(days=3)),
        declarations=declarations,
    )
    assert first.receipt_id == replay.receipt_id
    assert first.accepted_points == 1
    assert replay.replayed is True

    with pytest.raises(MetricIdentityConflict):
        record_batch(
            db,
            command=_command(value="101.000000000001"),
            declarations=declarations,
        )
    assert db.query(MetricIngestReceipt).count() == 1
    assert db.query(MetricObservation).count() == 1


def test_replay_is_independent_of_point_order_and_decimal_spelling(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    original = _command(value="100.0")
    ngn = original.points[0]
    usd = replace(ngn, value=Decimal("1.25"), currency_code="USD")
    first = replace(original, points=(ngn, usd))
    replay = replace(
        original,
        received_at=START + timedelta(days=3),
        points=(
            replace(usd, value=Decimal("1.250")),
            replace(ngn, value=Decimal("100.00")),
        ),
    )

    accepted = record_batch(db, command=first, declarations=declarations)
    repeated = record_batch(db, command=replay, declarations=declarations)

    assert repeated.receipt_id == accepted.receipt_id
    assert repeated.replayed is True
    assert db.query(MetricIngestReceipt).count() == 1
    assert db.query(MetricObservation).count() == 2


def test_changed_source_reference_conflicts_for_the_same_source_event(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    command = _command()
    record_batch(db, command=command, declarations=declarations)
    changed = replace(
        command,
        provenance=replace(
            command.provenance,
            source_reference="billing-run:different-source",
        ),
    )

    with pytest.raises(MetricIdentityConflict):
        record_batch(db, command=changed, declarations=declarations)


def test_latest_history_and_comparison_preserve_exact_decimals(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    record_batch(db, command=_command(), declarations=declarations)
    record_batch(
        db,
        command=_command(
            event_id="billing-day-2",
            value="150.000000000002",
            observed_at=START + timedelta(days=2),
        ),
        declarations=declarations,
    )
    # A newer correction for the same coordinate becomes the projection winner.
    latest = get_latest(
        db,
        tenant_id=TENANT_ID,
        selectors=(_selector(),),
        declarations=declarations,
    )
    assert latest[0].value == Decimal("150.000000000002")

    history = get_history(
        db,
        tenant_id=TENANT_ID,
        selector=_selector(),
        declarations=declarations,
        start=START,
        end=START + timedelta(days=2),
        limit=20,
    )
    assert [point.value for point in history] == [Decimal("150.000000000002")]

    comparison = compare_periods(
        db,
        tenant_id=TENANT_ID,
        selector=_selector(),
        declarations=declarations,
        current_period_start=START,
        current_period_end=START + timedelta(days=1),
        prior_period_start=START - timedelta(days=1),
        prior_period_end=START,
    )
    assert comparison.current_value == Decimal("150.000000000002")
    assert comparison.prior_value is None
    assert comparison.delta is None


def test_an_activated_declaration_cannot_change_in_place(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    record_batch(db, command=_command(), declarations=declarations)
    changed = MetricDeclarationRegistry(
        (
            MetricDeclaration(
                owner_code="billing",
                metric_code="billing.revenue.collected",
                schema_version=1,
                display_name="A changed meaning",
                value_kind=MetricValueKind.MONEY,
                unit_code="money",
                granularities=(MetricGranularity.DAY,),
                dimensions=(
                    DimensionSpec("channel", DimensionKind.ENUM, ("bank", "cash")),
                ),
            ),
        )
    )
    with pytest.raises(InvalidAnalyticsContract, match="cannot change declaration"):
        record_batch(
            db,
            command=_command(event_id="changed-declaration"),
            declarations=changed,
        )


def test_comparison_names_both_periods_and_zero_prior_has_no_percentage(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    prior_start = START - timedelta(days=1)
    record_batch(
        db,
        command=_command(
            event_id="billing-prior",
            value="0",
            period_start=prior_start,
        ),
        declarations=declarations,
    )
    record_batch(
        db,
        command=_command(event_id="billing-current", value="25"),
        declarations=declarations,
    )

    comparison = compare_periods(
        db,
        tenant_id=TENANT_ID,
        selector=_selector(),
        declarations=declarations,
        current_period_start=START,
        current_period_end=START + timedelta(days=1),
        prior_period_start=prior_start,
        prior_period_end=START,
    )
    assert comparison.current_value == Decimal("25")
    assert comparison.prior_value == Decimal("0")
    assert comparison.delta == Decimal("25")
    assert comparison.currency_code == "NGN"
    assert comparison.percentage_change is None


def test_tenant_is_part_of_every_service_query(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    record_batch(db, command=_command(), declarations=declarations)
    record_batch(
        db,
        command=_command(
            tenant_id=OTHER_TENANT_ID,
            value="999",
            event_id="other-tenant",
        ),
        declarations=declarations,
    )
    one = get_latest(
        db,
        tenant_id=TENANT_ID,
        selectors=(_selector(),),
        declarations=declarations,
    )
    two = get_latest(
        db,
        tenant_id=OTHER_TENANT_ID,
        selectors=(_selector(),),
        declarations=declarations,
    )
    assert one[0].value == Decimal("100.000000000001")
    assert two[0].value == Decimal("999")


def test_rebuild_repairs_projection_drift_and_records_evidence(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    record_batch(db, command=_command(), declarations=declarations)
    before = projection_digest(db, tenant_id=TENANT_ID)
    point = db.query(MetricPoint).filter_by(tenant_id=TENANT_ID).one()
    point.value_numeric = Decimal("777")
    db.flush()
    assert projection_digest(db, tenant_id=TENANT_ID) != before

    result = rebuild_projection(
        db,
        tenant_id=TENANT_ID,
        rebuilt_by="analytics-reconciler",
        rebuilt_at=START + timedelta(days=3),
    )
    assert result.before_digest != result.after_digest
    assert result.after_digest == before
    assert get_latest(
        db,
        tenant_id=TENANT_ID,
        selectors=(_selector(),),
        declarations=declarations,
    )[0].value == Decimal("100.000000000001")


def test_service_never_commits_or_rolls_back(
    db: Session, declarations: MetricDeclarationRegistry
) -> None:
    calls: list[str] = []
    event.listen(db, "after_commit", lambda session: calls.append("commit"))
    event.listen(db, "after_rollback", lambda session: calls.append("rollback"))
    record_batch(db, command=_command(), declarations=declarations)
    rebuild_projection(
        db,
        tenant_id=TENANT_ID,
        rebuilt_by="test",
        rebuilt_at=START + timedelta(days=3),
    )
    assert calls == []
