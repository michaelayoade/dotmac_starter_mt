"""Hosted parity canaries for the positioning module's native geometry owner."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_positioning import (
    CircleFence,
    CollectionGrantInput,
    GeofenceConflict,
    GeofenceEvaluationInput,
    ObservationInput,
    ObservationPolicy,
    PolygonFence,
    SourceAssignmentInput,
    _native,
    assign_source,
    create_geofence,
    create_tracked_unit,
    evaluate_geofences,
    grant_collection,
    record_observations,
    service,
)
from dotmac_positioning.models import (
    TENANT_MODELS,
    Geofence,
    GeofenceState,
    PositionObservation,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

SQUARE = [(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0)]


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected"),
    [
        (1.0, 1.0, True),
        (3.0, 1.0, False),
        (0.0, 1.0, True),
        (2.0, 1.0, False),
        (1.0, 0.0, True),
        (1.0, 2.0, False),
        (0.0, 0.0, True),
        (2.0, 2.0, False),
    ],
)
def test_polygon_ray_crossing_boundary_contract(
    latitude: float, longitude: float, expected: bool
) -> None:
    assert _native.contains_batch(
        latitude=latitude,
        longitude=longitude,
        shapes=[(None, SQUARE), (None, list(reversed(SQUARE)))],
    ) == [expected, expected]


@pytest.mark.parametrize(
    ("latitude", "longitude", "circle", "expected"),
    [
        (0.0, 0.0, (0.0, 0.0, 1.0), True),
        # One equatorial degree is 111194.92664455874 m for R=6371000.
        (0.0, 1.0, (0.0, 0.0, 111194.92), False),
        (0.0, 1.0, (0.0, 0.0, 111194.93), True),
        (0.0, -179.9, (0.0, 179.9, 22238.98), False),
        (0.0, -179.9, (0.0, 179.9, 22238.99), True),
        (0.0, 180.0, (0.0, 0.0, 20015086.79), False),
        (0.0, 180.0, (0.0, 0.0, 20015086.80), True),
        (90.0, 90.0, (90.0, -90.0, 0.001), True),
    ],
)
def test_circle_spherical_distance_golden_fixtures(
    latitude: float,
    longitude: float,
    circle: tuple[float, float, float],
    expected: bool,
) -> None:
    assert _native.contains_batch(
        latitude=latitude, longitude=longitude, shapes=[(circle, [])]
    ) == [expected]


def test_batch_keeps_mixed_shape_order_and_empty_batch() -> None:
    assert _native.contains_batch(latitude=1.0, longitude=1.0, shapes=[]) == []
    assert _native.contains_batch(
        latitude=1.0,
        longitude=1.0,
        shapes=[((40.0, 40.0, 1.0), []), (None, SQUARE), ((1.0, 1.0, 1.0), [])],
    ) == [False, True, True]


def test_polygon_does_not_invent_antimeridian_wrapping() -> None:
    points = [(-1.0, 179.0), (-1.0, -179.0), (1.0, -179.0), (1.0, 179.0)]
    assert _native.contains_batch(
        latitude=0.0, longitude=0.0, shapes=[(None, points)]
    ) == [True]
    assert _native.contains_batch(
        latitude=0.0, longitude=180.0, shapes=[(None, points)]
    ) == [False]


def test_empty_native_polygon_fails_without_panicking() -> None:
    with pytest.raises(IndexError):
        _native.contains_batch(latitude=0.0, longitude=0.0, shapes=[(None, [])])


@pytest.fixture
def geometry_db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_pos": None}},
    )
    for model in TENANT_MODELS:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _retained_observation(db: Session) -> tuple[TenantScope, GeofenceEvaluationInput]:
    scope = TenantScope(uuid4())
    now = datetime.now(UTC)
    unit_id = uuid4()
    create_tracked_unit(db, scope=scope, tracked_unit_id=unit_id, now=now)
    assign_source(
        db,
        scope=scope,
        assignment=SourceAssignmentInput(uuid4(), unit_id, "test", "device", now),
    )
    grant_collection(
        db,
        scope=scope,
        grant=CollectionGrantInput(
            uuid4(), unit_id, "test", now, now + timedelta(hours=1)
        ),
    )
    recorded = record_observations(
        db,
        scope=scope,
        tracked_unit_id=unit_id,
        purpose="test",
        policy=ObservationPolicy(1, timedelta(minutes=1), 10.0),
        received_at=now,
        observations=(ObservationInput(uuid4(), "test", "device", 1.0, 1.0, 1.0, now),),
    )
    observation_id = recorded.items[0].observation_id
    assert observation_id is not None
    return scope, GeofenceEvaluationInput(observation_id, ())


def test_service_batches_selection_once_before_state_writes(
    geometry_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = geometry_db
    scope, evaluation = _retained_observation(db)
    polygon = create_geofence(
        db,
        scope=scope,
        geofence_id=uuid4(),
        shape=PolygonFence(tuple(SQUARE)),
        now=datetime.now(UTC),
    )
    circle = create_geofence(
        db,
        scope=scope,
        geofence_id=uuid4(),
        shape=CircleFence(1.0, 1.0, 10.0),
        now=datetime.now(UTC),
    )
    calls: list[object] = []
    native = _native.contains_batch

    def spy(
        *, latitude: float, longitude: float, shapes: list[_native.Shape]
    ) -> list[bool]:
        assert db.scalar(select(func.count()).select_from(GeofenceState)) == 0
        calls.append(shapes)
        return native(latitude=latitude, longitude=longitude, shapes=shapes)

    monkeypatch.setattr(service._native, "contains_batch", spy)
    facts = evaluate_geofences(
        db,
        scope=scope,
        evaluation=GeofenceEvaluationInput(
            evaluation.observation_id, (polygon.id, circle.id, polygon.id)
        ),
    )
    assert calls == [[(None, SQUARE), ((1.0, 1.0, 10.0), [])]]
    assert {fact.geofence_id for fact in facts} == {polygon.id, circle.id}
    assert {fact.transition for fact in facts} == {"entry"}
    assert db.scalar(select(func.count()).select_from(GeofenceState)) == 2


@pytest.mark.parametrize(
    ("shape_kind", "points", "message"),
    [
        ("circle", None, "circle geofence .* is incomplete"),
        ("polygon", None, "has an unsupported shape"),
        ("unknown", None, "has an unsupported shape"),
    ],
)
def test_invalid_stored_shape_preserves_domain_error_before_native_call(
    shape_kind: str,
    points: list[list[float]] | None,
    message: str,
    geometry_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = geometry_db
    scope = TenantScope(uuid4())
    observation = PositionObservation(
        id=uuid4(), tenant_id=scope.tenant_id, latitude=1.0, longitude=1.0
    )
    fence = Geofence(
        id=uuid4(),
        tenant_id=scope.tenant_id,
        shape_kind=shape_kind,
        polygon_points=points,
        is_active=True,
        created_at=datetime.now(UTC),
    )
    valid = Geofence(
        id=uuid4(),
        shape_kind="circle",
        center_latitude=1.0,
        center_longitude=1.0,
        radius_m=1.0,
    )

    def unexpected(**kwargs: object) -> list[bool]:
        pytest.fail("invalid geometry reached native computation")

    monkeypatch.setattr(service._native, "contains_batch", unexpected)
    with pytest.raises(GeofenceConflict, match=message):
        service._evaluate_selected_geofences(
            db, observation=observation, fences=(valid, fence)
        )
    assert db.scalar(select(func.count()).select_from(GeofenceState)) == 0


@pytest.mark.parametrize(
    ("points", "error"),
    [([], IndexError), ([[0.0]], ValueError), ([[0.0, 1.0, 2.0]], ValueError)],
)
def test_malformed_polygon_preserves_python_shape_error(
    points: list[list[float]], error: type[Exception]
) -> None:
    fence = Geofence(id=uuid4(), shape_kind="polygon", polygon_points=points)
    with pytest.raises(error):
        service._native_shape(fence)
