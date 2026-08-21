"""FX-policy behavior canaries adjudicated from ERP."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_fx_policy.contracts import (
    Conflict,
    CreateRateType,
    DetermineRate,
    RecordRateObservation,
    RegisterRateSource,
    SetSelectionPolicy,
)
from dotmac_fx_policy.models import TENANT_TABLES
from dotmac_fx_policy.service import (
    create_rate_type,
    determine_rate,
    record_rate_observation,
    register_rate_source,
    set_selection_policy,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_fx_policy": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_fx_policy import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def _configured(db: Session) -> tuple[TenantScope, object, object]:
    scope = TenantScope(TENANT_A)
    rate_type = create_rate_type(
        db,
        scope=scope,
        command=CreateRateType("SPOT", "Spot", is_default=True),
    )
    source = register_rate_source(
        db,
        scope=scope,
        command=RegisterRateSource("MANUAL", "Manual", priority=10),
    )
    return scope, rate_type, source


def test_latest_effective_direct_observation_is_selected_and_evidenced(
    db: Session,
) -> None:
    scope, rate_type, source = _configured(db)
    policy = set_selection_policy(
        db,
        scope=scope,
        command=SetSelectionPolicy(
            rate_type.id,
            "USD",
            "NGN",
            NOW - timedelta(days=30),
            preferred_source_id=source.id,
            allow_inverse=True,
        ),
    )
    selected = record_rate_observation(
        db,
        scope=scope,
        command=RecordRateObservation(
            rate_type.id,
            source.id,
            "USD",
            "NGN",
            Decimal("1500.5000000000"),
            NOW - timedelta(days=2),
            NOW - timedelta(days=2),
            "manual:2026-08-18",
        ),
    )
    record_rate_observation(
        db,
        scope=scope,
        command=RecordRateObservation(
            rate_type.id,
            source.id,
            "USD",
            "NGN",
            Decimal("1550"),
            NOW + timedelta(days=1),
            NOW,
            "manual:2026-08-21",
        ),
    )

    result = determine_rate(
        db,
        scope=scope,
        command=DetermineRate("quote:1", "USD", "NGN", "SPOT", NOW),
    )

    assert result.rate == Decimal("1500.5000000000")
    assert result.observation_id == selected.id
    assert result.policy_id == policy.id
    assert result.source_code == "MANUAL"
    assert result.inverted is False
    assert result.determination_id is not None


def test_inverse_fallback_is_explicitly_controlled_by_policy(db: Session) -> None:
    scope, rate_type, source = _configured(db)
    set_selection_policy(
        db,
        scope=scope,
        command=SetSelectionPolicy(
            rate_type.id,
            "USD",
            "NGN",
            NOW - timedelta(days=30),
            preferred_source_id=source.id,
            allow_inverse=True,
        ),
    )
    record_rate_observation(
        db,
        scope=scope,
        command=RecordRateObservation(
            rate_type.id,
            source.id,
            "NGN",
            "USD",
            Decimal("0.000666666666666667"),
            NOW - timedelta(hours=1),
            NOW - timedelta(hours=1),
            "manual:inverse",
        ),
    )

    result = determine_rate(
        db,
        scope=scope,
        command=DetermineRate("quote:2", "USD", "NGN", "SPOT", NOW),
    )

    assert result.inverted is True
    assert result.rate == Decimal(1) / Decimal("0.000666666666666667")


def test_identity_pair_needs_no_policy_or_observation(db: Session) -> None:
    result = determine_rate(
        db,
        scope=TenantScope(TENANT_A),
        command=DetermineRate("quote:identity", "NGN", "ngn", "SPOT", NOW),
    )
    assert result.rate == Decimal(1)
    assert result.source_code == "identity"
    assert result.observation_id is None
    assert result.determination_id is None


def test_nonpositive_observation_and_missing_policy_fail_closed(db: Session) -> None:
    scope, rate_type, source = _configured(db)
    with pytest.raises(Conflict, match="positive"):
        record_rate_observation(
            db,
            scope=scope,
            command=RecordRateObservation(
                rate_type.id,
                source.id,
                "USD",
                "NGN",
                Decimal("0"),
                NOW,
                NOW,
                "manual:zero",
            ),
        )
    with pytest.raises(Conflict, match="policy"):
        determine_rate(
            db,
            scope=scope,
            command=DetermineRate("quote:missing", "USD", "NGN", "SPOT", NOW),
        )
