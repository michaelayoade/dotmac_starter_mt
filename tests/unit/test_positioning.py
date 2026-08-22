"""Behavioural parity contract for reusable position evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_positioning import (
    CircleFence,
    CollectionGrantInput,
    GeofenceConflict,
    GeofenceEvaluationInput,
    ObservationDisposition,
    ObservationInput,
    ObservationPolicy,
    PolygonFence,
    PositionObservationConflict,
    PositionObservationRejected,
    SourceAssignmentConflict,
    SourceAssignmentInput,
    assign_source,
    create_geofence,
    create_tracked_unit,
    evaluate_geofences,
    get_current_position,
    get_trail,
    grant_collection,
    prune_observations,
    rebuild_current_position,
    record_observations,
    revoke_collection,
    unassign_source,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
SCOPE = TenantScope(TENANT_ID)


@pytest.fixture
def db() -> Session:
    from dotmac_positioning.models import TENANT_MODELS

    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_pos": None}},
    )
    for model in TENANT_MODELS:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


def _policy(**overrides: object) -> ObservationPolicy:
    return ObservationPolicy(
        max_batch_size=int(overrides.get("max_batch_size", 200)),
        max_future_skew=overrides.get(  # type: ignore[arg-type]
            "max_future_skew", timedelta(minutes=5)
        ),
        max_accuracy_m=float(overrides.get("max_accuracy_m", 1000)),
    )


def _observation(**overrides: object) -> ObservationInput:
    observation_id = overrides.get("client_observation_id", uuid4())
    return ObservationInput(
        client_observation_id=(
            observation_id
            if isinstance(observation_id, UUID)
            else UUID(str(observation_id))
        ),
        source=str(overrides.get("source", "mobile")),
        source_unit_ref=str(overrides.get("source_unit_ref", "device-default")),
        latitude=float(overrides.get("latitude", 9.071)),
        longitude=float(overrides.get("longitude", 7.451)),
        accuracy_m=float(overrides.get("accuracy_m", 8)),
        captured_at=overrides.get("captured_at", datetime.now(UTC)),  # type: ignore[arg-type]
        context_ref=overrides.get("context_ref"),  # type: ignore[arg-type]
    )


def _unit_with_grant(
    db: Session,
    *,
    granted_at: datetime | None = None,
    source_unit_ref: str = "device-default",
) -> UUID:
    unit_id = uuid4()
    now = granted_at or datetime.now(UTC)
    create_tracked_unit(db, scope=SCOPE, tracked_unit_id=unit_id, now=now)
    assign_source(
        db,
        scope=SCOPE,
        assignment=SourceAssignmentInput(
            assignment_id=uuid4(),
            tracked_unit_id=unit_id,
            source="mobile",
            source_unit_ref=source_unit_ref,
            assigned_at=now,
        ),
    )
    grant_collection(
        db,
        scope=SCOPE,
        grant=CollectionGrantInput(
            grant_id=uuid4(),
            tracked_unit_id=unit_id,
            purpose="service_delivery",
            granted_at=now,
            expires_at=now + timedelta(days=3),
        ),
    )
    return unit_id


def test_recording_is_replay_safe_and_current_position_never_regresses(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=1))
    newest = _observation(captured_at=now, latitude=9.1, context_ref="opaque-7")
    older = _observation(captured_at=now - timedelta(minutes=2), latitude=1)

    first = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(newest, older),
    )
    replay = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(newest,),
    )

    assert [item.disposition for item in first.items] == [
        ObservationDisposition.RECORDED,
        ObservationDisposition.RECORDED,
    ]
    assert replay.items[0].disposition is ObservationDisposition.REPLAYED
    current = get_current_position(db, scope=SCOPE, tracked_unit_id=unit_id)
    assert current is not None
    assert current.latitude == 9.1
    assert [
        item.context_ref
        for item in get_trail(db, scope=SCOPE, tracked_unit_id=unit_id, limit=10)
    ] == ["opaque-7", None]


def test_equal_capture_and_accuracy_uses_receipt_time_as_stable_tie_breaker(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=1))
    first = _observation(captured_at=now, latitude=9.1)
    second = _observation(captured_at=now, latitude=9.2)

    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(first,),
    )
    result = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now + timedelta(seconds=1),
        observations=(second,),
    )

    current = get_current_position(db, scope=SCOPE, tracked_unit_id=unit_id)
    assert current is not None
    assert current.observation_id == result.items[0].observation_id
    assert current.latitude == 9.2


def test_current_position_has_an_idempotent_drift_repair(db: Session) -> None:
    from dotmac_positioning.models import CurrentPosition

    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=1))
    first = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now - timedelta(minutes=1),
        observations=(
            _observation(captured_at=now - timedelta(minutes=1), latitude=1),
        ),
    )
    second = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now, latitude=9.2),),
    )
    first_id = first.items[0].observation_id
    second_id = second.items[0].observation_id
    assert first_id is not None
    assert second_id is not None

    projection = db.scalar(
        select(CurrentPosition).where(CurrentPosition.tracked_unit_id == unit_id)
    )
    assert projection is not None
    projection.observation_id = first_id
    projection.latitude = 1
    db.flush()

    repaired = rebuild_current_position(db, scope=SCOPE, tracked_unit_id=unit_id)
    replay = rebuild_current_position(db, scope=SCOPE, tracked_unit_id=unit_id)

    assert repaired is not None
    assert repaired.observation_id == second_id
    assert repaired.latitude == 9.2
    assert replay == repaired


def test_reused_observation_identity_with_changed_evidence_conflicts(
    db: Session,
) -> None:
    unit_id = _unit_with_grant(db)
    now = datetime.now(UTC)
    observation_id = uuid4()
    original = _observation(
        client_observation_id=observation_id,
        captured_at=now,
    )
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(original,),
    )

    collision = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(
            _observation(
                client_observation_id=observation_id,
                captured_at=now,
                latitude=9.2,
            ),
        ),
    )
    assert collision.items[0].disposition is ObservationDisposition.CONFLICT
    assert collision.items[0].code == "identity_collision"


def test_observation_identity_cannot_be_reused_for_another_tracked_unit(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    first_unit = _unit_with_grant(db, granted_at=now)
    second_unit = _unit_with_grant(
        db,
        granted_at=now,
        source_unit_ref="device-second",
    )
    shared_identity = uuid4()
    evidence = _observation(
        client_observation_id=shared_identity,
        captured_at=now,
    )
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=first_unit,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(evidence,),
    )

    collision = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=second_unit,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(evidence,),
    )

    assert collision.items[0].disposition is ObservationDisposition.CONFLICT
    assert collision.items[0].code == "identity_collision"


def test_collection_grant_is_purpose_bound_expiring_and_revocable(db: Session) -> None:
    unit_id = _unit_with_grant(db)
    now = datetime.now(UTC)

    with pytest.raises(PositionObservationRejected, match="grant"):
        record_observations(
            db,
            scope=SCOPE,
            tracked_unit_id=unit_id,
            purpose="different_purpose",
            policy=_policy(),
            received_at=now,
            observations=(_observation(captured_at=now),),
        )

    revoke_collection(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        revoked_at=now,
    )
    with pytest.raises(PositionObservationRejected, match="grant"):
        record_observations(
            db,
            scope=SCOPE,
            tracked_unit_id=unit_id,
            purpose="service_delivery",
            policy=_policy(),
            received_at=now,
            observations=(_observation(captured_at=now),),
        )


def test_collection_grant_replay_requires_the_same_immutable_contract(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = uuid4()
    grant_id = uuid4()
    create_tracked_unit(db, scope=SCOPE, tracked_unit_id=unit_id, now=now)
    grant = CollectionGrantInput(
        grant_id=grant_id,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        granted_at=now,
        expires_at=now + timedelta(hours=1),
    )

    first = grant_collection(db, scope=SCOPE, grant=grant)
    replay = grant_collection(db, scope=SCOPE, grant=grant)

    assert first.id == replay.id == grant_id
    with pytest.raises(PositionObservationConflict, match=r"identity.*reused"):
        grant_collection(
            db,
            scope=SCOPE,
            grant=CollectionGrantInput(
                grant_id=grant_id,
                tracked_unit_id=unit_id,
                purpose="service_delivery",
                granted_at=now,
                expires_at=now + timedelta(hours=2),
            ),
        )


def test_product_supplies_batch_accuracy_and_clock_policy(db: Session) -> None:
    unit_id = _unit_with_grant(db)
    now = datetime.now(UTC)

    with pytest.raises(PositionObservationRejected, match="batch"):
        record_observations(
            db,
            scope=SCOPE,
            tracked_unit_id=unit_id,
            purpose="service_delivery",
            policy=_policy(max_batch_size=1),
            received_at=now,
            observations=(_observation(), _observation()),
        )
    result = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(max_accuracy_m=5, max_future_skew=timedelta(0)),
        received_at=now,
        observations=(
            _observation(accuracy_m=6, captured_at=now),
            _observation(
                accuracy_m=4,
                captured_at=now + timedelta(seconds=1),
            ),
        ),
    )
    assert [item.code for item in result.items] == [
        "invalid_accuracy",
        "future_timestamp",
    ]


def test_source_assignment_is_open_vocabulary_and_temporally_bounded(
    db: Session,
) -> None:
    unit_id = _unit_with_grant(db)
    assigned_at = datetime.now(UTC)
    assignment = SourceAssignmentInput(
        assignment_id=uuid4(),
        tracked_unit_id=unit_id,
        source="custom_gnss_v7",
        source_unit_ref="opaque-source-42",
        assigned_at=assigned_at,
    )

    stored = create_tracked_unit(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        now=assigned_at,
    )
    assert stored.id == unit_id
    from dotmac_positioning import resolve_tracked_unit

    stored_assignment = assign_source(db, scope=SCOPE, assignment=assignment)
    assert (
        resolve_tracked_unit(
            db,
            scope=SCOPE,
            source="custom_gnss_v7",
            source_unit_ref="opaque-source-42",
            at=assigned_at,
        )
        == unit_id
    )
    second_unit_id = uuid4()
    create_tracked_unit(
        db,
        scope=SCOPE,
        tracked_unit_id=second_unit_id,
        now=assigned_at,
    )
    with pytest.raises(SourceAssignmentConflict, match="overlapping"):
        assign_source(
            db,
            scope=SCOPE,
            assignment=SourceAssignmentInput(
                assignment_id=uuid4(),
                tracked_unit_id=second_unit_id,
                source="custom_gnss_v7",
                source_unit_ref="opaque-source-42",
                assigned_at=assigned_at,
            ),
        )

    moved_at = assigned_at + timedelta(hours=1)
    unassign_source(
        db,
        scope=SCOPE,
        assignment_id=stored_assignment.id,
        unassigned_at=moved_at,
    )
    assign_source(
        db,
        scope=SCOPE,
        assignment=SourceAssignmentInput(
            assignment_id=uuid4(),
            tracked_unit_id=second_unit_id,
            source="custom_gnss_v7",
            source_unit_ref="opaque-source-42",
            assigned_at=moved_at,
        ),
    )
    assert (
        resolve_tracked_unit(
            db,
            scope=SCOPE,
            source="custom_gnss_v7",
            source_unit_ref="opaque-source-42",
            at=moved_at,
        )
        == second_unit_id
    )


def test_reused_assignment_id_does_not_create_an_orphan_source_identity(
    db: Session,
) -> None:
    from dotmac_positioning.models import SourceIdentity

    assigned_at = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=assigned_at)
    assignment_id = uuid4()
    assign_source(
        db,
        scope=SCOPE,
        assignment=SourceAssignmentInput(
            assignment_id=assignment_id,
            tracked_unit_id=unit_id,
            source="custom_gnss_v7",
            source_unit_ref="original-source",
            assigned_at=assigned_at,
        ),
    )
    identity_count = db.scalar(select(func.count()).select_from(SourceIdentity))

    with pytest.raises(SourceAssignmentConflict, match="was reused"):
        assign_source(
            db,
            scope=SCOPE,
            assignment=SourceAssignmentInput(
                assignment_id=assignment_id,
                tracked_unit_id=unit_id,
                source="custom_gnss_v7",
                source_unit_ref="conflicting-source",
                assigned_at=assigned_at,
            ),
        )

    assert db.scalar(select(func.count()).select_from(SourceIdentity)) == identity_count


def test_unassigned_source_evidence_is_rejected(db: Session) -> None:
    now = datetime.now(UTC)
    unit_id = uuid4()
    create_tracked_unit(db, scope=SCOPE, tracked_unit_id=unit_id, now=now)
    grant_collection(
        db,
        scope=SCOPE,
        grant=CollectionGrantInput(
            grant_id=uuid4(),
            tracked_unit_id=unit_id,
            purpose="service_delivery",
            granted_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )

    result = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(
            _observation(
                captured_at=now,
                source="future_provider",
                source_unit_ref="unassigned-tracker",
            ),
        ),
    )

    assert result.items[0].disposition is ObservationDisposition.REJECTED
    assert result.items[0].code == "source_not_assigned"


def test_geofence_replay_requires_the_same_immutable_geometry(db: Session) -> None:
    now = datetime.now(UTC)
    geofence_id = uuid4()
    shape = CircleFence(latitude=9.071, longitude=7.451, radius_m=100)

    first = create_geofence(
        db,
        scope=SCOPE,
        geofence_id=geofence_id,
        shape=shape,
        now=now,
    )
    replay = create_geofence(
        db,
        scope=SCOPE,
        geofence_id=geofence_id,
        shape=shape,
        now=now + timedelta(minutes=1),
    )

    assert first.id == replay.id == geofence_id
    with pytest.raises(GeofenceConflict, match=r"identity.*reused"):
        create_geofence(
            db,
            scope=SCOPE,
            geofence_id=geofence_id,
            shape=CircleFence(latitude=9.071, longitude=7.451, radius_m=200),
            now=now,
        )


def test_only_product_selected_geofences_emit_neutral_entry_exit_facts(
    db: Session,
) -> None:
    unit_id = _unit_with_grant(db)
    now = datetime.now(UTC)
    circle = create_geofence(
        db,
        scope=SCOPE,
        geofence_id=uuid4(),
        shape=CircleFence(latitude=9.071, longitude=7.451, radius_m=100),
        now=now,
    )
    polygon = create_geofence(
        db,
        scope=SCOPE,
        geofence_id=uuid4(),
        shape=PolygonFence(
            points=((9.070, 7.450), (9.070, 7.452), (9.072, 7.451)),
        ),
        now=now,
    )

    inside = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now),),
    )
    inside_observation_id = inside.items[0].observation_id
    assert inside_observation_id is not None
    inside_facts = evaluate_geofences(
        db,
        scope=SCOPE,
        evaluation=GeofenceEvaluationInput(
            observation_id=inside_observation_id,
            geofence_ids=(circle.id,),
        ),
    )
    outside = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now + timedelta(minutes=1),
        observations=(
            _observation(
                captured_at=now + timedelta(minutes=1),
                latitude=1,
                longitude=1,
            ),
        ),
    )
    outside_observation_id = outside.items[0].observation_id
    assert outside_observation_id is not None
    outside_facts = evaluate_geofences(
        db,
        scope=SCOPE,
        evaluation=GeofenceEvaluationInput(
            observation_id=outside_observation_id,
            geofence_ids=(circle.id,),
        ),
    )

    assert {fact.geofence_id for fact in inside_facts} == {circle.id}
    assert {fact.transition for fact in inside_facts} == {"entry"}
    assert {fact.geofence_id for fact in outside_facts} == {circle.id}
    assert {fact.transition for fact in outside_facts} == {"exit"}

    from dotmac_positioning.models import GeofenceState

    assert (
        db.scalar(
            select(func.count())
            .select_from(GeofenceState)
            .where(GeofenceState.geofence_id == polygon.id)
        )
        == 0
    )


def test_stale_observation_cannot_regress_selected_geofence_state(db: Session) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=1))
    fence = create_geofence(
        db,
        scope=SCOPE,
        geofence_id=uuid4(),
        shape=CircleFence(latitude=9.071, longitude=7.451, radius_m=100),
        now=now,
    )
    newest = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now),),
    )
    older = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now + timedelta(seconds=1),
        observations=(
            _observation(
                captured_at=now - timedelta(minutes=1),
                latitude=1,
                longitude=1,
            ),
        ),
    )
    newest_id = newest.items[0].observation_id
    older_id = older.items[0].observation_id
    assert newest_id is not None
    assert older_id is not None

    entry = evaluate_geofences(
        db,
        scope=SCOPE,
        evaluation=GeofenceEvaluationInput(
            observation_id=newest_id,
            geofence_ids=(fence.id,),
        ),
    )
    stale = evaluate_geofences(
        db,
        scope=SCOPE,
        evaluation=GeofenceEvaluationInput(
            observation_id=older_id,
            geofence_ids=(fence.id,),
        ),
    )

    assert {fact.transition for fact in entry} == {"entry"}
    assert stale == ()

    from dotmac_positioning.models import GeofenceState

    state = db.scalar(
        select(GeofenceState).where(GeofenceState.geofence_id == fence.id)
    )
    assert state is not None
    assert state.is_inside is True
    assert state.last_observation_id == newest_id


def test_geofence_evaluation_fails_closed_for_unavailable_selection(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=1))
    result = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now),),
    )
    observation_id = result.items[0].observation_id
    assert observation_id is not None

    with pytest.raises(GeofenceConflict, match="missing or inactive"):
        evaluate_geofences(
            db,
            scope=SCOPE,
            evaluation=GeofenceEvaluationInput(
                observation_id=observation_id,
                geofence_ids=(uuid4(),),
            ),
        )


def test_retention_prunes_observations_without_owning_product_policy(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(days=2))
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now - timedelta(days=2),
        observations=(_observation(captured_at=now - timedelta(days=2)),),
    )
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now),),
    )

    assert prune_observations(db, scope=SCOPE, received_before=now) == 1
    assert len(get_trail(db, scope=SCOPE, tracked_unit_id=unit_id, limit=10)) == 1


def test_retention_preserves_observations_selected_by_product_policy(
    db: Session,
) -> None:
    now = datetime.now(UTC)
    unit_id = _unit_with_grant(db, granted_at=now - timedelta(hours=60))
    first = record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now - timedelta(days=2),
        observations=(_observation(captured_at=now - timedelta(days=2)),),
    )
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now - timedelta(days=1),
        observations=(_observation(captured_at=now - timedelta(days=1)),),
    )
    record_observations(
        db,
        scope=SCOPE,
        tracked_unit_id=unit_id,
        purpose="service_delivery",
        policy=_policy(),
        received_at=now,
        observations=(_observation(captured_at=now),),
    )
    preserved_id = first.items[0].observation_id
    assert preserved_id is not None

    assert (
        prune_observations(
            db,
            scope=SCOPE,
            received_before=now,
            preserve_observation_ids=frozenset({preserved_id}),
        )
        == 1
    )
    assert {
        point.observation_id
        for point in get_trail(db, scope=SCOPE, tracked_unit_id=unit_id, limit=10)
    } >= {preserved_id}
