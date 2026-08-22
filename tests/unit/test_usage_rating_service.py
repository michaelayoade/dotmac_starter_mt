"""Usage-rating behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_usage_rating.contracts import Conflict, CreateRatingRule, RateUsage
from dotmac_usage_rating.models import TENANT_TABLES
from dotmac_usage_rating.service import create_rating_rule, rate_usage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_usage_rate": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_usage_rating import models

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


def test_effective_rule_creates_a_pre_tax_obligation(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    rule = create_rating_rule(
        db,
        scope=scope,
        command=CreateRatingRule(
            "data-ngn",
            "internet.gigabytes",
            "gigabyte",
            Decimal("12.50"),
            "NGN",
            NOW - timedelta(days=1),
            NOW + timedelta(days=30),
        ),
    )
    obligation = rate_usage(
        db,
        scope=scope,
        command=RateUsage("usage:1", "service:1", rule.id, Decimal("2"), NOW, NOW),
    )
    assert obligation.net_amount == Decimal("25.00")
    assert obligation.currency == "NGN"


def test_expired_rule_duplicate_usage_and_cross_tenant_rule_are_refused(
    db: Session,
) -> None:
    rule = create_rating_rule(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateRatingRule(
            "voice-ngn",
            "voice.minutes",
            "minute",
            Decimal("5"),
            "NGN",
            NOW - timedelta(days=2),
            NOW - timedelta(days=1),
        ),
    )
    command = RateUsage("usage:2", "service:2", rule.id, Decimal("1"), NOW, NOW)
    with pytest.raises(Conflict, match="effective"):
        rate_usage(db, scope=TenantScope(TENANT_A), command=command)
    with pytest.raises(Conflict, match="not found"):
        rate_usage(db, scope=TenantScope(TENANT_B), command=command)
