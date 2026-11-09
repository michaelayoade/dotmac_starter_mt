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
    WarrantRefusal,
    WarrantRefused,
)
from dotmac_qualification.models import TENANT_TABLES
from dotmac_qualification.service import (
    open_qualification,
    read_decision,
    record_decision,
    record_evidence,
    verify_warrant,
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


def _decided(
    db: Session,
    *,
    subject: str = "location:loc-1",
    outcome: DecisionOutcome = DecisionOutcome.ELIGIBLE,
    tenant: uuid.UUID = TENANT_A,
):
    scope = TenantScope(tenant)
    case = open_qualification(
        db, scope=scope, command=OpenQualification(subject, "spec:fiber")
    )
    record_evidence(
        db,
        scope=scope,
        command=RecordEvidence(
            case.id, "POSITIONING", NOW, NOW + timedelta(days=7), {"zone": "Z1"}
        ),
    )
    return record_decision(
        db,
        scope=scope,
        command=RecordDecision(
            case.id,
            outcome,
            NOW + timedelta(hours=1),
            NOW + timedelta(days=2),
            "private rationale",
        ),
    )


def test_decision_read_returns_verifier_facts_without_rationale_or_evidence(
    db: Session,
) -> None:
    decision = _decided(db)
    read = read_decision(db, scope=TenantScope(TENANT_A), decision_id=decision.id)

    assert read is not None
    assert read.decision_id == decision.id
    assert read.case_id == decision.case_id
    assert read.subject_reference == "location:loc-1"
    assert read.specification_reference == "spec:fiber"
    assert read.outcome is DecisionOutcome.ELIGIBLE
    assert read.expires_at == NOW + timedelta(days=2)
    assert not {"rationale", "facts"} & set(type(read).__slots__)
    assert (
        read_decision(db, scope=TenantScope(TENANT_A), decision_id=uuid.uuid4()) is None
    )


def test_an_eligible_unexpired_decision_for_the_subject_is_a_warrant(
    db: Session,
) -> None:
    decision = _decided(db)
    scope = TenantScope(TENANT_A)

    for at in (NOW + timedelta(hours=1), NOW + timedelta(days=1)):
        warrant = verify_warrant(
            db,
            scope=scope,
            decision_id=decision.id,
            subject_reference=" location:loc-1 ",
            at=at,
        )
        assert warrant.decision_id == decision.id


def test_the_warrant_fails_closed_for_every_other_state(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    eligible = _decided(db)
    ineligible = _decided(
        db, subject="location:loc-2", outcome=DecisionOutcome.INELIGIBLE
    )
    review = _decided(
        db, subject="location:loc-3", outcome=DecisionOutcome.MANUAL_REVIEW
    )
    inside = NOW + timedelta(days=1)

    def refusal(decision_id: uuid.UUID, subject: str, at: datetime) -> WarrantRefusal:
        with pytest.raises(WarrantRefused) as raised:
            verify_warrant(
                db,
                scope=scope,
                decision_id=decision_id,
                subject_reference=subject,
                at=at,
            )
        return raised.value.reason

    assert refusal(uuid.uuid4(), "location:loc-1", inside) is WarrantRefusal.NOT_FOUND
    assert (
        refusal(eligible.id, "location:other", inside)
        is WarrantRefusal.SUBJECT_MISMATCH
    )
    # Subject comparison is exact, not case-folded.
    assert (
        refusal(eligible.id, "LOCATION:LOC-1", inside)
        is WarrantRefusal.SUBJECT_MISMATCH
    )
    assert (
        refusal(ineligible.id, "location:loc-2", inside) is WarrantRefusal.NOT_ELIGIBLE
    )
    assert refusal(review.id, "location:loc-3", inside) is WarrantRefusal.NOT_ELIGIBLE
    assert (
        refusal(eligible.id, "location:loc-1", NOW) is WarrantRefusal.NOT_YET_EFFECTIVE
    )
    # Expiry is exclusive: the instant of expiry no longer warrants.
    assert (
        refusal(eligible.id, "location:loc-1", NOW + timedelta(days=2))
        is WarrantRefusal.EXPIRED
    )
    assert issubclass(WarrantRefused, Conflict)


def test_the_warrant_needs_an_explicit_aware_instant_and_subject(db: Session) -> None:
    decision = _decided(db)
    scope = TenantScope(TENANT_A)

    with pytest.raises(ValueError, match="timezone-aware"):
        verify_warrant(
            db,
            scope=scope,
            decision_id=decision.id,
            subject_reference="location:loc-1",
            at=datetime(2026, 8, 21, 12, 0),
        )
    with pytest.raises(ValueError, match="subject reference"):
        verify_warrant(
            db,
            scope=scope,
            decision_id=decision.id,
            subject_reference="  ",
            at=NOW + timedelta(days=1),
        )


def test_another_tenants_decision_is_not_a_warrant(db: Session) -> None:
    decision = _decided(db, tenant=TENANT_A)
    other = TenantScope(TENANT_B)

    assert read_decision(db, scope=other, decision_id=decision.id) is None
    with pytest.raises(WarrantRefused) as raised:
        verify_warrant(
            db,
            scope=other,
            decision_id=decision.id,
            subject_reference="location:loc-1",
            at=NOW + timedelta(days=1),
        )
    assert raised.value.reason is WarrantRefusal.NOT_FOUND
