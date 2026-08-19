"""Behaviour canaries for provider-neutral immutable media observations.

Written before the package implementation. These are the product-first parity
and correction proofs: Mkt's normalized hierarchy and repeat-sync behavior are
preserved, while mutable overwrite becomes immutable replay/restatement plus a
rebuildable current projection.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_media_observations import (
    CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION,
    ClaimStatus,
    CountValue,
    DecimalValue,
    DerivedRatio,
    DurationValue,
    EntityDisposition,
    EntityObservation,
    ExactMoney,
    HierarchyObservation,
    InvalidObservation,
    JsonValue,
    MetricDefinitionDeclaration,
    MetricObservation,
    MetricSemantic,
    MetricValueType,
    NodeTypeDeclaration,
    NormalizedObservationCase,
    ObservationConflict,
    ObservationKind,
    ObservationSource,
    ProviderRestatement,
    RatioValue,
    RecordStatus,
    UnsupportedObservation,
    declare_metric,
    declare_node_type,
    derive_ratio,
    emit_analytics_fact,
    read_current_entity,
    read_hierarchy,
    read_period_metrics,
    reconcile_projections,
    record_entity,
    record_hierarchy,
    record_metric,
    record_restatement,
    report_hierarchy_drift,
    report_metric_drift,
    run_normalized_conformance,
)
from dotmac_media_observations.models import (
    ALL_TABLES,
    APPEND_ONLY_TABLES,
    CurrentEntity,
    CurrentHierarchy,
    CurrentMetric,
    MetricPeriod,
    ObservationEnvelope,
    ObservationReceipt,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
T0 = datetime(2026, 8, 18, 8, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_mediaobs": None}},
    )
    for table in ALL_TABLES:
        table.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _source(
    identity: str,
    *,
    observed_at: datetime = T0,
    received_at: datetime | None = None,
    receipt: str | None = None,
) -> ObservationSource:
    return ObservationSource(
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        source_observation_id=identity,
        observed_at=observed_at,
        received_at=received_at or observed_at + timedelta(minutes=2),
        transport_receipt_ref=receipt or f"receipt-{identity}",
        normalization_version=1,
    )


def _node(db: Session, code: str = "campaign", *, version: int = 1) -> None:
    declare_node_type(
        db,
        NodeTypeDeclaration(
            tenant_id=TENANT,
            code=code,
            version=version,
            label=code.title(),
            traits={"aggregate": True},
            declared_by="media-contract-test",
            declared_at=T0,
        ),
    )


def _metric(
    db: Session,
    *,
    code: str = "reported_impressions",
    value_type: MetricValueType = MetricValueType.COUNT,
    unit: str = "impression",
    semantic: MetricSemantic = MetricSemantic.IMPRESSIONS,
) -> None:
    declare_metric(
        db,
        MetricDefinitionDeclaration(
            tenant_id=TENANT,
            code=code,
            version=1,
            label=code.replace("_", " ").title(),
            value_type=value_type,
            unit=unit,
            semantic=semantic,
            declared_by="media-contract-test",
            declared_at=T0,
        ),
    )


def _entity(
    identity: str,
    *,
    entity_ref: str = "campaign-42",
    name: str = "North launch",
    observed_at: datetime = T0,
    receipt: str | None = None,
    disposition: EntityDisposition = EntityDisposition.PRESENT,
) -> EntityObservation:
    return EntityObservation(
        source=_source(identity, observed_at=observed_at, receipt=receipt),
        external_account_ref="account-7",
        entity_ref=entity_ref,
        node_code="campaign",
        node_version=1,
        name=name,
        state="enabled",
        disposition=disposition,
        properties={"objective": "awareness"},
    )


def _conformance_case() -> NormalizedObservationCase:
    node_declaration = NodeTypeDeclaration(
        tenant_id=TENANT,
        code="campaign",
        version=1,
        label="Campaign",
        traits={"aggregate": True},
        declared_by="conformance-fake",
        declared_at=T0,
    )
    metric_declaration = MetricDefinitionDeclaration(
        tenant_id=TENANT,
        code="reported_impressions",
        version=1,
        label="Reported impressions",
        value_type=MetricValueType.COUNT,
        unit="impression",
        semantic=MetricSemantic.IMPRESSIONS,
        declared_by="conformance-fake",
        declared_at=T0,
    )
    return NormalizedObservationCase(
        node_declarations=(node_declaration,),
        metric_declarations=(metric_declaration,),
        observations=(
            _entity(
                "conformance-parent",
                entity_ref="campaign-parent",
                name="Parent campaign",
            ),
            _entity(
                "conformance-child",
                entity_ref="campaign-child",
                name="Child campaign",
            ),
            HierarchyObservation(
                source=_source("conformance-hierarchy"),
                external_account_ref="account-7",
                child_entity_ref="campaign-child",
                parent_entity_ref="campaign-parent",
            ),
            MetricObservation(
                source=_source("conformance-metric"),
                external_account_ref="account-7",
                entity_ref="campaign-child",
                metric_code="reported_impressions",
                metric_version=1,
                period_start=T0,
                period_end=T0 + timedelta(days=1),
                value=CountValue(9),
            ),
        ),
    )


class _FakeNormalizedProducer:
    normalized_observation_spi_version = CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION

    def normalized_case(self) -> NormalizedObservationCase:
        return _conformance_case()


def test_three_level_hierarchy_and_current_state_preserve_mkt_parity(
    db: Session,
) -> None:
    for code in ("campaign", "group", "advertisement"):
        _node(db, code)
    for command in (
        _entity("entity-c", entity_ref="c", name="Campaign"),
        replace(
            _entity("entity-g", entity_ref="g", name="Group"),
            node_code="group",
        ),
        replace(
            _entity("entity-a", entity_ref="a", name="Advertisement"),
            node_code="advertisement",
        ),
    ):
        record_entity(db, command)
    record_hierarchy(
        db,
        HierarchyObservation(
            source=_source("edge-g-c"),
            external_account_ref="account-7",
            child_entity_ref="g",
            parent_entity_ref="c",
        ),
    )
    record_hierarchy(
        db,
        HierarchyObservation(
            source=_source("edge-a-g"),
            external_account_ref="account-7",
            child_entity_ref="a",
            parent_entity_ref="g",
        ),
    )

    hierarchy = read_hierarchy(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
    )
    assert [(edge.child_entity_ref, edge.parent_entity_ref) for edge in hierarchy] == [
        ("a", "g"),
        ("g", "c"),
    ]
    assert {edge.drift_code for edge in hierarchy} == {None}


def test_exact_replay_is_idempotent_and_attaches_each_transport_receipt(
    db: Session,
) -> None:
    _node(db)
    first = record_entity(db, _entity("same", receipt="transport-a"))
    replay = record_entity(db, _entity("same", receipt="transport-b"))

    assert first.status is RecordStatus.RECORDED
    assert replay.status is RecordStatus.REPLAYED
    assert replay.observation_id == first.observation_id
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1
    assert db.scalar(select(func.count()).select_from(ObservationReceipt)) == 2


def test_replayed_transport_receipt_identity_requires_the_same_receipt_time(
    db: Session,
) -> None:
    _node(db)
    command = _entity("same", receipt="transport-a")
    record_entity(db, command)

    with pytest.raises(ObservationConflict) as caught:
        record_entity(
            db,
            replace(
                command,
                source=replace(
                    command.source,
                    received_at=command.source.received_at + timedelta(seconds=1),
                ),
            ),
        )

    assert caught.value.report.code == "transport_receipt_conflict"
    assert db.scalar(select(func.count()).select_from(ObservationReceipt)) == 1


def test_reusing_source_identity_with_changed_content_is_a_conflict(
    db: Session,
) -> None:
    _node(db)
    record_entity(db, _entity("same"))

    with pytest.raises(ObservationConflict) as caught:
        record_entity(db, _entity("same", name="Changed behind the same identity"))

    report = caught.value.report
    assert report.code == "observation_identity_conflict"
    assert report.source_observation_id == "same"
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1


def test_source_identity_cannot_be_reused_for_another_observation_kind(
    db: Session,
) -> None:
    _node(db)
    _metric(db)
    record_entity(db, _entity("shared-source-id"))

    with pytest.raises(ObservationConflict) as caught:
        record_metric(
            db,
            MetricObservation(
                source=_source("shared-source-id"),
                external_account_ref="account-7",
                entity_ref="campaign-42",
                metric_code="reported_impressions",
                metric_version=1,
                period_start=T0,
                period_end=T0 + timedelta(days=1),
                value=CountValue(1),
            ),
        )

    assert caught.value.report.code == "observation_identity_conflict"
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1
    assert db.scalar(select(func.count()).select_from(MetricPeriod)) == 0


def test_changed_identity_conflicts_before_new_content_is_interpreted(
    db: Session,
) -> None:
    _node(db)
    command = _entity("shared-source-id")
    record_entity(db, command)

    with pytest.raises(ObservationConflict) as caught:
        record_entity(
            db,
            replace(
                command,
                node_code="undeclared_node",
                node_version=99,
            ),
        )

    assert caught.value.report.code == "observation_identity_conflict"
    assert caught.value.report.source_observation_id == "shared-source-id"
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1


def test_restatement_appends_history_and_moves_only_the_projection(db: Session) -> None:
    _node(db)
    original = record_entity(db, _entity("entity-v1", name="Old name"))
    replacement = _entity(
        "entity-v2",
        name="Corrected name",
        observed_at=T0 + timedelta(minutes=1),
    )

    corrected = record_restatement(
        db,
        ProviderRestatement(
            replaces_observation_id=original.observation_id,
            replacement=replacement,
        ),
    )

    assert corrected.status is RecordStatus.RESTATED
    assert corrected.observation_id != original.observation_id
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 2
    current = read_current_entity(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
    )
    assert current is not None
    assert current.name == "Corrected name"
    assert current.observation_id == corrected.observation_id


def test_direct_entity_restatement_cannot_cross_subject_or_source(db: Session) -> None:
    _node(db)
    original = record_entity(db, _entity("entity-original", entity_ref="entity-a"))

    for forged in (
        replace(
            _entity("entity-other-subject", entity_ref="entity-b"),
            restates_observation_id=original.observation_id,
        ),
        replace(
            _entity("entity-other-installation", entity_ref="entity-a"),
            source=replace(
                _source("entity-other-installation"),
                installation_ref="installation-beta",
            ),
            restates_observation_id=original.observation_id,
        ),
        replace(
            _entity("entity-other-source", entity_ref="entity-a"),
            source=replace(
                _source("entity-other-source"),
                source_system="another-media-source",
            ),
            restates_observation_id=original.observation_id,
        ),
    ):
        with pytest.raises(InvalidObservation, match="restatement"):
            record_entity(db, forged)

    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1


def test_direct_hierarchy_restatement_cannot_change_its_child(db: Session) -> None:
    original = record_hierarchy(
        db,
        HierarchyObservation(
            source=_source("edge-original"),
            external_account_ref="account-7",
            child_entity_ref="child-a",
            parent_entity_ref="parent-a",
        ),
    )

    with pytest.raises(InvalidObservation, match="restatement"):
        record_hierarchy(
            db,
            HierarchyObservation(
                source=_source("edge-other-child"),
                external_account_ref="account-7",
                child_entity_ref="child-b",
                parent_entity_ref="parent-a",
                restates_observation_id=original.observation_id,
            ),
        )

    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1


def test_direct_metric_restatement_cannot_change_its_period_subject(
    db: Session,
) -> None:
    _metric(db)
    original_command = MetricObservation(
        source=_source("metric-original"),
        external_account_ref="account-7",
        entity_ref="campaign-42",
        metric_code="reported_impressions",
        metric_version=1,
        period_start=T0,
        period_end=T0 + timedelta(days=1),
        value=CountValue(10),
    )
    original = record_metric(db, original_command)

    with pytest.raises(InvalidObservation, match="restatement"):
        record_metric(
            db,
            replace(
                original_command,
                source=_source("metric-other-period"),
                period_start=T0 + timedelta(days=1),
                period_end=T0 + timedelta(days=2),
                restates_observation_id=original.observation_id,
            ),
        )

    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 1
    assert db.scalar(select(func.count()).select_from(MetricPeriod)) == 1


def test_out_of_order_delivery_has_a_deterministic_projection(db: Session) -> None:
    _node(db)
    newer = _entity(
        "entity-new",
        name="Newer source state",
        observed_at=T0 + timedelta(hours=2),
    )
    older = _entity("entity-old", name="Older source state", observed_at=T0)

    record_entity(db, newer)
    record_entity(db, older)
    current = read_current_entity(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
    )
    assert current is not None and current.name == "Newer source state"


def test_naive_provider_or_receipt_timestamp_is_refused(db: Session) -> None:
    _node(db)
    aware = _source("aware")
    for field_name in ("observed_at", "received_at"):
        with pytest.raises(InvalidObservation, match="timezone-aware"):
            replace(aware, **{field_name: T0.replace(tzinfo=None)})


def test_period_read_window_refuses_naive_or_reversed_instants(db: Session) -> None:
    common = {
        "tenant_id": TENANT,
        "installation_ref": "installation-alpha",
        "source_system": "external-media",
        "external_account_ref": "account-7",
        "entity_ref": "campaign-42",
    }
    for period_start, period_end in (
        (T0.replace(tzinfo=None), T0 + timedelta(days=1)),
        (T0, (T0 + timedelta(days=1)).replace(tzinfo=None)),
    ):
        with pytest.raises(InvalidObservation, match="timezone-aware"):
            read_period_metrics(
                db,
                period_start=period_start,
                period_end=period_end,
                **common,
            )

    with pytest.raises(InvalidObservation, match=r"\[start,end\)"):
        read_period_metrics(
            db,
            period_start=T0 + timedelta(days=1),
            period_end=T0,
            **common,
        )


def test_a_missing_parent_is_not_silently_re_rooted(db: Session) -> None:
    _node(db)
    record_entity(db, _entity("child", entity_ref="child"))
    record_hierarchy(
        db,
        HierarchyObservation(
            source=_source("edge"),
            external_account_ref="account-7",
            child_entity_ref="child",
            parent_entity_ref="missing-parent",
        ),
    )

    [edge] = read_hierarchy(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
    )
    assert edge.parent_entity_ref == "missing-parent"
    assert edge.drift_code == "missing_parent"
    assert report_hierarchy_drift(db, tenant_id=TENANT).count == 1


def test_a_reported_cycle_is_preserved_and_flagged(db: Session) -> None:
    _node(db)
    for ref in ("a", "b"):
        record_entity(db, _entity(f"entity-{ref}", entity_ref=ref))
    for child, parent in (("a", "b"), ("b", "a")):
        record_hierarchy(
            db,
            HierarchyObservation(
                source=_source(f"edge-{child}"),
                external_account_ref="account-7",
                child_entity_ref=child,
                parent_entity_ref=parent,
            ),
        )

    hierarchy = read_hierarchy(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
    )
    assert {(edge.child_entity_ref, edge.parent_entity_ref) for edge in hierarchy} == {
        ("a", "b"),
        ("b", "a"),
    }
    assert {edge.drift_code for edge in hierarchy} == {"cycle"}


def test_count_values_refuse_float_and_bool() -> None:
    assert CountValue(7).value == 7
    for value in (7.0, True, Decimal("7")):
        with pytest.raises(InvalidObservation, match="integer"):
            CountValue(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value_type", [CountValue, DurationValue])
def test_integral_metric_values_refuse_numbers_outside_signed_64_bit_storage(
    value_type: type[CountValue] | type[DurationValue],
) -> None:
    with pytest.raises(InvalidObservation, match="signed 64-bit"):
        value_type(2**63)


@pytest.mark.parametrize("value_type", [DecimalValue, RatioValue])
@pytest.mark.parametrize(
    "value",
    [Decimal("100000000000000000000"), Decimal("0.0000000000000000001")],
)
def test_decimal_metric_values_refuse_values_that_storage_would_round_or_overflow(
    value_type: type[DecimalValue] | type[RatioValue], value: Decimal
) -> None:
    with pytest.raises(InvalidObservation, match=r"NUMERIC\(38,18\)"):
        value_type(value)


def test_money_retains_exact_amount_currency_and_minor_unit_provenance() -> None:
    money = ExactMoney(amount=Decimal("50000.00"), currency="NGN", minor_unit=2)
    assert money.minor_units == 5_000_000

    with pytest.raises(InvalidObservation, match="exactly representable"):
        ExactMoney(amount=Decimal("1.001"), currency="NGN", minor_unit=2)
    with pytest.raises(InvalidObservation, match="Decimal"):
        ExactMoney(amount=1.25, currency="NGN", minor_unit=2)  # type: ignore[arg-type]


def test_metric_periods_are_half_open_and_partially_overlapping_is_refused(
    db: Session,
) -> None:
    _node(db)
    _metric(db)
    record_entity(db, _entity("entity"))
    first = MetricObservation(
        source=_source("metric-1"),
        external_account_ref="account-7",
        entity_ref="campaign-42",
        metric_code="reported_impressions",
        metric_version=1,
        period_start=T0,
        period_end=T0 + timedelta(days=1),
        value=CountValue(100),
    )
    record_metric(db, first)

    # Adjacent is valid under [start, end).
    record_metric(
        db,
        replace(
            first,
            source=_source("metric-2", observed_at=T0 + timedelta(days=1)),
            period_start=T0 + timedelta(days=1),
            period_end=T0 + timedelta(days=2),
            value=CountValue(50),
        ),
    )
    with pytest.raises(ObservationConflict, match="overlap"):
        record_metric(
            db,
            replace(
                first,
                source=_source("metric-overlap"),
                period_start=T0 + timedelta(hours=12),
                period_end=T0 + timedelta(days=1, hours=12),
            ),
        )


def test_metric_restatement_reuses_the_period_and_replaces_only_current_value(
    db: Session,
) -> None:
    _node(db)
    _metric(db)
    record_entity(db, _entity("entity"))
    original_command = MetricObservation(
        source=_source("metric-original"),
        external_account_ref="account-7",
        entity_ref="campaign-42",
        metric_code="reported_impressions",
        metric_version=1,
        period_start=T0,
        period_end=T0 + timedelta(days=1),
        value=CountValue(100),
    )
    original = record_metric(db, original_command)
    corrected = record_restatement(
        db,
        ProviderRestatement(
            replaces_observation_id=original.observation_id,
            replacement=replace(
                original_command,
                source=_source(
                    "metric-correction", observed_at=T0 + timedelta(minutes=5)
                ),
                value=CountValue(105),
            ),
        ),
    )

    [metric] = read_period_metrics(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
        period_start=T0,
        period_end=T0 + timedelta(days=1),
    )
    assert corrected.status is RecordStatus.RESTATED
    assert metric.observation_id == corrected.observation_id
    assert metric.value == CountValue(105)
    assert db.scalar(select(func.count()).select_from(MetricPeriod)) == 1
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 3


def test_metric_value_must_match_the_versioned_definition(db: Session) -> None:
    _node(db)
    _metric(db)
    record_entity(db, _entity("entity"))
    command = MetricObservation(
        source=_source("metric"),
        external_account_ref="account-7",
        entity_ref="campaign-42",
        metric_code="reported_impressions",
        metric_version=1,
        period_start=T0,
        period_end=T0 + timedelta(days=1),
        value=DecimalValue(Decimal("1.5")),
    )

    with pytest.raises(InvalidObservation, match="expects count"):
        record_metric(db, command)


def test_provider_conversion_claim_remains_labelled_and_is_not_attribution(
    db: Session,
) -> None:
    _node(db)
    _metric(
        db,
        code="reported_conversion_value",
        value_type=MetricValueType.MONEY,
        unit="currency",
        semantic=MetricSemantic.CONVERSION_VALUE,
    )
    record_entity(db, _entity("entity"))
    result = record_metric(
        db,
        MetricObservation(
            source=_source("conversion"),
            external_account_ref="account-7",
            entity_ref="campaign-42",
            metric_code="reported_conversion_value",
            metric_version=1,
            period_start=T0,
            period_end=T0 + timedelta(days=1),
            value=ExactMoney(amount=Decimal("50000.00"), currency="NGN", minor_unit=2),
        ),
    )

    fact = emit_analytics_fact(
        db, tenant_id=TENANT, observation_id=result.observation_id
    )
    assert fact.claim_status is ClaimStatus.PROVIDER_REPORTED
    assert fact.content_fingerprint == result.fingerprint
    assert fact.restates_observation_id is None
    assert fact.payload.metric_code == "reported_conversion_value"
    assert fact.payload.semantic is MetricSemantic.CONVERSION_VALUE
    assert fact.payload.unit == "currency"
    assert fact.payload.value == ExactMoney(
        amount=Decimal("50000.00"), currency="NGN", minor_unit=2
    )
    assert fact.attribution_status == "not_attribution"
    assert not hasattr(fact, "lead_id")
    assert not hasattr(fact, "customer_id")
    assert not hasattr(fact, "revenue")


def test_provider_deletion_is_state_not_local_destructive_deletion(db: Session) -> None:
    _node(db)
    record_entity(db, _entity("present"))
    deleted = record_entity(
        db,
        _entity(
            "deleted",
            observed_at=T0 + timedelta(hours=1),
            disposition=EntityDisposition.DELETED,
        ),
    )
    current = read_current_entity(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
    )
    assert current is not None
    assert current.disposition is EntityDisposition.DELETED
    assert current.observation_id == deleted.observation_id
    assert db.scalar(select(func.count()).select_from(ObservationEnvelope)) == 2


def test_projection_rebuild_repairs_drift_from_immutable_facts(db: Session) -> None:
    _node(db)
    result = record_entity(db, _entity("entity"))
    current = db.scalar(select(CurrentEntity))
    assert current is not None
    current.observation_id = uuid.uuid4()
    db.flush()

    assert report_metric_drift(db, tenant_id=TENANT).count == 0
    preview = reconcile_projections(
        db,
        tenant_id=TENANT,
        actor_ref="test-auditor",
        reason="prove repair",
        apply=False,
    )
    assert preview.drift_count == 1
    assert not preview.applied

    repaired = reconcile_projections(
        db,
        tenant_id=TENANT,
        actor_ref="test-auditor",
        reason="repair proven projection drift",
        apply=True,
    )
    assert repaired.applied and repaired.drift_count == 1
    assert db.scalar(select(CurrentEntity.observation_id)) == result.observation_id


def test_metric_reads_include_complete_provenance(db: Session) -> None:
    _node(db)
    _metric(db)
    record_entity(db, _entity("entity"))
    command = MetricObservation(
        source=_source("metric", receipt="receipt-metric-a"),
        external_account_ref="account-7",
        entity_ref="campaign-42",
        metric_code="reported_impressions",
        metric_version=1,
        period_start=T0,
        period_end=T0 + timedelta(days=1),
        value=CountValue(9),
    )
    result = record_metric(db, command)
    record_metric(
        db,
        replace(
            command,
            source=replace(
                command.source,
                transport_receipt_ref="receipt-metric-b",
                received_at=T0 + timedelta(minutes=3),
            ),
        ),
    )

    [metric] = read_period_metrics(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
        period_start=T0,
        period_end=T0 + timedelta(days=1),
    )
    assert metric.observation_id == result.observation_id
    assert metric.source_observation_id == "metric"
    assert metric.source_observed_at == T0
    assert metric.received_at == T0 + timedelta(minutes=2)
    assert metric.content_fingerprint == result.fingerprint
    assert metric.restates_observation_id is None
    assert [
        (receipt.transport_receipt_ref, receipt.received_at)
        for receipt in metric.transport_receipts
    ] == [
        ("receipt-metric-a", T0 + timedelta(minutes=2)),
        ("receipt-metric-b", T0 + timedelta(minutes=3)),
    ]
    assert metric.transport_receipt_refs == (
        "receipt-metric-a",
        "receipt-metric-b",
    )
    assert metric.normalization_version == 1


def test_analytics_entity_and_hierarchy_facts_preserve_the_normalized_payload(
    db: Session,
) -> None:
    _node(db)
    parent = record_entity(db, _entity("parent", entity_ref="parent"))
    child = record_entity(db, _entity("child", entity_ref="child"))
    edge = record_hierarchy(
        db,
        HierarchyObservation(
            source=_source("edge"),
            external_account_ref="account-7",
            child_entity_ref="child",
            parent_entity_ref="parent",
        ),
    )

    entity_fact = emit_analytics_fact(
        db, tenant_id=TENANT, observation_id=child.observation_id
    )
    assert entity_fact.content_fingerprint == child.fingerprint
    assert entity_fact.payload.entity_ref == "child"
    assert entity_fact.payload.node_code == "campaign"
    assert entity_fact.payload.node_version == 1
    assert entity_fact.payload.name == "North launch"
    assert entity_fact.payload.state == "enabled"
    assert entity_fact.payload.disposition is EntityDisposition.PRESENT
    assert entity_fact.payload.properties == {"objective": "awareness"}
    assert entity_fact.transport_receipts[0].transport_receipt_ref == "receipt-child"

    hierarchy_fact = emit_analytics_fact(
        db, tenant_id=TENANT, observation_id=edge.observation_id
    )
    assert hierarchy_fact.content_fingerprint == edge.fingerprint
    assert hierarchy_fact.payload.child_entity_ref == "child"
    assert hierarchy_fact.payload.parent_entity_ref == "parent"
    assert parent.observation_id != child.observation_id


def test_exact_decimal_entity_properties_round_trip_without_type_loss(
    db: Session,
) -> None:
    _node(db)
    properties = {
        "daily_budget": Decimal("123.4500"),
        "configuration": [Decimal("0.125"), {"threshold": Decimal("7.00")}],
    }
    outcome = record_entity(
        db, replace(_entity("decimal-state"), properties=properties)
    )

    current = read_current_entity(
        db,
        tenant_id=TENANT,
        installation_ref="installation-alpha",
        source_system="external-media",
        external_account_ref="account-7",
        entity_ref="campaign-42",
    )
    emitted = emit_analytics_fact(
        db, tenant_id=TENANT, observation_id=outcome.observation_id
    )
    assert current is not None and current.properties == properties
    assert emitted.payload.properties == properties


def test_all_refused_observations_expose_a_typed_rejection_report(
    db: Session,
) -> None:
    with pytest.raises(InvalidObservation) as invalid:
        CountValue(1.5)  # type: ignore[arg-type]
    assert invalid.value.report.code == "invalid_observation"

    with pytest.raises(UnsupportedObservation) as unsupported:
        record_entity(db, _entity("undeclared"))
    assert unsupported.value.report.code == "unsupported_observation"


def test_derived_ratio_is_explicitly_not_a_provider_observation() -> None:
    ratio = derive_ratio(Decimal("3"), Decimal("4"), unit="fraction")
    assert isinstance(ratio, DerivedRatio)
    assert ratio.value == RatioValue(Decimal("0.75"))
    assert ratio.claim_status is ClaimStatus.DERIVED_PROJECTION


@pytest.mark.parametrize(
    "properties",
    (
        {"email": "person@example.test"},
        {"audience_members": ["opaque-person"]},
        {"phone_number": "+000000000"},
        {"audiences": ["lookalike-1"]},
        {"profiles": [{"opaque_ref": "profile-1"}]},
        {"users": ["external-user-1"]},
        {"metadata": {"contacts": ["external-contact-1"]}},
        {"lead_id": "local-lead-1"},
        {"opportunity_id": "local-opportunity-1"},
        {"party_ref": "local-party-1"},
        {"customer_ids": ["local-customer-1"]},
        {"subscriber_ref": "local-subscriber-1"},
        {"quote_id": "local-quote-1"},
        {"order_id": "local-order-1"},
        {"authoritative_revenue": "50000.00"},
        {"attribution": "official"},
    ),
)
def test_aggregate_properties_refuse_person_identity_or_business_consequences(
    db: Session,
    properties: dict[str, JsonValue],
) -> None:
    _node(db)
    with pytest.raises(InvalidObservation, match="aggregate-only"):
        record_entity(db, replace(_entity("bad"), properties=properties))


def test_append_only_model_set_excludes_only_rebuildable_projections() -> None:
    names = {table.name for table in APPEND_ONLY_TABLES}
    assert {
        "node_definitions",
        "metric_definitions",
        "observations",
        "observation_receipts",
        "entity_observations",
        "hierarchy_observations",
        "metric_periods",
        "metric_observations",
        "reconciliation_evidence",
    } <= names
    assert (
        not {
            CurrentEntity.__tablename__,
            CurrentHierarchy.__tablename__,
            CurrentMetric.__tablename__,
        }
        & names
    )


def test_provider_free_normalization_conformance_replays_a_stable_fixture(
    db: Session,
) -> None:
    case = _conformance_case()
    report = run_normalized_conformance(db, _FakeNormalizedProducer())

    assert report.spi_version == CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION
    assert report.observation_count == 4
    assert report.replay_count == 4
    assert report.installation_ref == "installation-alpha"
    assert report.source_system == "external-media"
    assert report.node_declarations == case.node_declarations
    assert report.metric_declarations == case.metric_declarations
    assert report.observation_kinds == (
        ObservationKind.ENTITY,
        ObservationKind.ENTITY,
        ObservationKind.HIERARCHY,
        ObservationKind.METRIC,
    )
    assert len(report.observation_ids) == 4
    assert len(report.content_fingerprints) == 4
    assert len(report.facts) == 4
    assert (
        tuple(fact.observation_id for fact in report.facts)
        == report.observation_ids
    )
    assert tuple(
        fact.content_fingerprint for fact in report.facts
    ) == report.content_fingerprints
    assert tuple(fact.source_observation_id for fact in report.facts) == (
        "conformance-parent",
        "conformance-child",
        "conformance-hierarchy",
        "conformance-metric",
    )
    assert {fact.kind for fact in report.facts} == set(ObservationKind)
    assert all(fact.normalization_version == 1 for fact in report.facts)
    assert tuple(
        receipt.transport_receipt_ref
        for fact in report.facts
        for receipt in fact.transport_receipts
    ) == (
        "receipt-conformance-parent",
        "receipt-conformance-child",
        "receipt-conformance-hierarchy",
        "receipt-conformance-metric",
    )


@pytest.mark.parametrize(
    ("declared_version", "expected_error"),
    (
        (None, UnsupportedObservation),
        (2, UnsupportedObservation),
        ("1", InvalidObservation),
        (True, InvalidObservation),
    ),
)
def test_normalized_conformance_refuses_missing_malformed_or_incompatible_spi(
    db: Session,
    declared_version: object,
    expected_error: type[InvalidObservation | UnsupportedObservation],
) -> None:
    class Producer:
        def normalized_case(self) -> NormalizedObservationCase:
            return _conformance_case()

    producer = Producer()
    if declared_version is not None:
        producer.normalized_observation_spi_version = declared_version  # type: ignore[attr-defined]

    with pytest.raises(expected_error, match="SPI version"):
        run_normalized_conformance(db, producer)  # type: ignore[arg-type]


def test_normalized_conformance_refuses_a_missing_case_factory(db: Session) -> None:
    class Producer:
        normalized_observation_spi_version = (
            CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION
        )

    with pytest.raises(UnsupportedObservation, match="normalized case"):
        run_normalized_conformance(db, Producer())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "message"),
    (
        (object(), "NormalizedObservationCase"),
        (
            NormalizedObservationCase(
                node_declarations=(object(),),  # type: ignore[arg-type]
                metric_declarations=(),
                observations=(_entity("malformed-node-declaration"),),
            ),
            "node declaration",
        ),
        (
            NormalizedObservationCase(
                node_declarations=(),
                metric_declarations=(object(),),  # type: ignore[arg-type]
                observations=(_entity("malformed-metric-declaration"),),
            ),
            "metric declaration",
        ),
        (
            NormalizedObservationCase(
                node_declarations=(),
                metric_declarations=(),
                observations=(object(),),  # type: ignore[arg-type]
            ),
            "observation command",
        ),
    ),
)
def test_normalized_conformance_refuses_malformed_case_members_with_typed_errors(
    db: Session,
    case: object,
    message: str,
) -> None:
    class Producer:
        normalized_observation_spi_version = (
            CURRENT_NORMALIZED_OBSERVATION_SPI_VERSION
        )

        def normalized_case(self) -> object:
            return case

    with pytest.raises(InvalidObservation, match=message):
        run_normalized_conformance(db, Producer())  # type: ignore[arg-type]
