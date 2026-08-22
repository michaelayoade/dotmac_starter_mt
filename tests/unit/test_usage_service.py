"""Normalized usage behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_usage.contracts import (
    Conflict,
    CorrectUsage,
    ProjectUsageAggregate,
    RecordUsageObservation,
)
from dotmac_usage.models import TENANT_TABLES
from dotmac_usage.service import (
    project_usage_aggregate,
    record_usage_correction,
    record_usage_observation,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_usage": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_usage import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="a", name="A"),
                Tenant(id=TENANT_B, slug="b", name="B"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def test_observation_correction_and_rebuildable_projection(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    observation = record_usage_observation(
        db,
        scope=scope,
        command=RecordUsageObservation(
            "service:1",
            "internet.bytes",
            NOW,
            NOW + timedelta(hours=1),
            Decimal("100.5"),
            "byte",
            "collector:1",
            "event:1",
        ),
    )
    correction = record_usage_correction(
        db,
        scope=scope,
        command=CorrectUsage(
            observation.id, Decimal("-0.5"), "collector rounding", NOW
        ),
    )
    aggregate = project_usage_aggregate(
        db,
        scope=scope,
        command=ProjectUsageAggregate(
            "service:1",
            "internet.bytes",
            NOW,
            NOW + timedelta(days=1),
            Decimal("100"),
            NOW,
        ),
    )
    assert correction.delta_quantity == Decimal("-0.5")
    assert aggregate.quantity == Decimal("100")


def test_duplicate_source_event_and_cross_tenant_correction_are_refused(
    db: Session,
) -> None:
    command = RecordUsageObservation(
        "service:2",
        "voice.seconds",
        NOW,
        NOW + timedelta(minutes=1),
        Decimal("60"),
        "second",
        "collector:2",
        "event:2",
    )
    observation = record_usage_observation(
        db, scope=TenantScope(TENANT_A), command=command
    )
    with pytest.raises(Conflict, match="source event"):
        record_usage_observation(db, scope=TenantScope(TENANT_A), command=command)
    with pytest.raises(Conflict, match="not found"):
        record_usage_correction(
            db,
            scope=TenantScope(TENANT_B),
            command=CorrectUsage(observation.id, Decimal("1"), "wrong", NOW),
        )
