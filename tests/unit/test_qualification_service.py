"""Qualification behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_qualification.contracts import (
    Conflict,
    DecisionOutcome,
    OpenQualification,
    RecordDecision,
    RecordEvidence,
)
from dotmac_qualification.models import TENANT_TABLES
from dotmac_qualification.service import (
    open_qualification,
    record_decision,
    record_evidence,
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
        execution_options={"schema_translate_map": {"mod_qual": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_qualification import models

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


def test_time_bounded_evidence_supports_one_owned_decision(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    case = open_qualification(
        db, scope=scope, command=OpenQualification("customer:1", "spec:fiber")
    )
    evidence = record_evidence(
        db,
        scope=scope,
        command=RecordEvidence(
            case.id, "POSITIONING", NOW, NOW + timedelta(days=7), {"zone": "ABUJA-1"}
        ),
    )
    decision = record_decision(
        db,
        scope=scope,
        command=RecordDecision(
            case.id,
            DecisionOutcome.ELIGIBLE,
            NOW + timedelta(hours=1),
            NOW + timedelta(days=2),
            "coverage evidence accepted",
        ),
    )
    assert evidence.facts == {"zone": "ABUJA-1"}
    assert decision.outcome == DecisionOutcome.ELIGIBLE
    assert case.closed_at == decision.decided_at


def test_expired_evidence_cannot_support_a_decision(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    case = open_qualification(
        db, scope=scope, command=OpenQualification("customer:2", "spec:lte")
    )
    record_evidence(
        db,
        scope=scope,
        command=RecordEvidence(
            case.id,
            "NETWORK_OBSERVATION",
            NOW - timedelta(days=2),
            NOW - timedelta(days=1),
            {"reachable": True},
        ),
    )
    with pytest.raises(Conflict, match="valid evidence"):
        record_decision(
            db,
            scope=scope,
            command=RecordDecision(
                case.id, DecisionOutcome.ELIGIBLE, NOW, NOW + timedelta(days=1), "stale"
            ),
        )


def test_cross_tenant_case_access_is_refused_and_rollback_discards(db: Session) -> None:
    case = open_qualification(
        db,
        scope=TenantScope(TENANT_A),
        command=OpenQualification("customer:3", "spec:fiber"),
    )
    with pytest.raises(Conflict):
        record_evidence(
            db,
            scope=TenantScope(TENANT_B),
            command=RecordEvidence(
                case.id, "POSITIONING", NOW, NOW + timedelta(days=1), {"zone": "x"}
            ),
        )
    db.rollback()
    assert db.get(type(case), case.id) is None
