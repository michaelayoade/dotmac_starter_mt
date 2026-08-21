"""Freshness, replay and deterministic-rebuild behavior for Platform Health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.models import Base
from dotmac_platform_health import (
    HealthConflict,
    HealthObservationInput,
    HealthState,
    rebuild_projections,
    record_observation,
    register_component,
    summarize_health,
)
from dotmac_platform_health.models import PLATFORM_MODELS, HealthObservation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_health": None}},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in PLATFORM_MODELS])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_observation_replays_exactly_and_rejects_changed_content(db: Session) -> None:
    component = register_component(
        db, code="api", display_name="API", freshness_seconds=60
    )
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    command = HealthObservationInput(
        "agent:one",
        "tick:1",
        "api",
        HealthState.HEALTHY,
        at,
        at,
        "ok",
        {"region": "west"},
    )
    first = record_observation(db, command)
    replay = record_observation(db, command)
    assert replay.replayed is True
    assert replay.observation.id == first.observation.id
    assert db.scalars(select(HealthObservation)).all() == [first.observation]
    with pytest.raises(HealthConflict, match="different content"):
        record_observation(
            db,
            HealthObservationInput(
                "agent:one", "tick:1", "api", HealthState.UNHEALTHY, at, at, "down", {}
            ),
        )
    assert component.code == "api"


def test_summary_distinguishes_fresh_stale_and_missing_and_rebuilds(
    db: Session,
) -> None:
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    register_component(db, code="worker", display_name="Worker", freshness_seconds=30)
    register_component(db, code="new", display_name="New", freshness_seconds=30)
    record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    record_observation(
        db,
        HealthObservationInput(
            "agent", "worker:1", "worker", HealthState.DEGRADED, at, at, "lag", {}
        ),
    )
    fresh = summarize_health(db, as_of=at + timedelta(seconds=20))
    assert [(item.component_code, item.freshness) for item in fresh] == [
        ("api", "fresh"),
        ("new", "missing"),
        ("worker", "fresh"),
    ]
    stale = summarize_health(db, as_of=at + timedelta(seconds=61))
    assert {item.component_code: item.freshness for item in stale} == {
        "api": "stale",
        "new": "missing",
        "worker": "stale",
    }
    before = [(item.component_code, item.state, item.observation_id) for item in stale]
    rebuild_projections(db, rebuilt_at=at + timedelta(minutes=2))
    after = [
        (item.component_code, item.state, item.observation_id)
        for item in summarize_health(db, as_of=at + timedelta(seconds=61))
    ]
    assert after == before


def test_observation_rejects_unbounded_labels_and_future_domain_time(
    db: Session,
) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at = datetime(2026, 8, 21, 8, tzinfo=UTC)
    with pytest.raises(ValueError, match="labels"):
        record_observation(
            db,
            HealthObservationInput(
                "agent",
                "tick",
                "api",
                HealthState.HEALTHY,
                at,
                at,
                "ok",
                {str(i): "v" for i in range(21)},
            ),
        )
    with pytest.raises(ValueError, match="observed_at"):
        record_observation(
            db,
            HealthObservationInput(
                "agent",
                "tick",
                "api",
                HealthState.HEALTHY,
                at + timedelta(seconds=1),
                at,
                "ok",
                {},
            ),
        )
