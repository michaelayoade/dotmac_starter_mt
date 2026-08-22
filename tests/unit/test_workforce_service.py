"""Workforce behavior canaries adjudicated from Sub, ERP, and CRM."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_workforce.contracts import (
    AddTeamMember,
    CertifyWorkerSkill,
    Conflict,
    CreateSkill,
    CreateTeam,
    DispatchWork,
    RecordAvailability,
)
from dotmac_workforce.models import TENANT_TABLES
from dotmac_workforce.service import (
    add_team_member,
    certify_worker_skill,
    create_skill,
    create_team,
    dispatch_work,
    record_availability,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_workforce": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_workforce import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def test_skill_and_availability_gate_dispatch(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    team = create_team(db, scope=scope, command=CreateTeam("fiber", "Fiber"))
    skill = create_skill(
        db, scope=scope, command=CreateSkill("splice", "Fiber splicing")
    )
    add_team_member(
        db,
        scope=scope,
        command=AddTeamMember(team.id, "party:worker-1", NOW),
    )
    certify_worker_skill(
        db,
        scope=scope,
        command=CertifyWorkerSkill("party:worker-1", skill.id, 4, NOW),
    )
    record_availability(
        db,
        scope=scope,
        command=RecordAvailability(
            "party:worker-1",
            NOW,
            NOW + timedelta(hours=8),
            True,
            "schedule:1",
        ),
    )
    decision = dispatch_work(
        db,
        scope=scope,
        command=DispatchWork("work:1", team.id, "party:worker-1", skill.id, NOW, NOW),
    )
    assert decision.worker_reference == "party:worker-1"


def test_dispatch_refuses_missing_skill_or_availability(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    team = create_team(db, scope=scope, command=CreateTeam("radio", "Radio"))
    skill = create_skill(db, scope=scope, command=CreateSkill("rf", "RF"))
    add_team_member(
        db,
        scope=scope,
        command=AddTeamMember(team.id, "party:worker-2", NOW),
    )
    with pytest.raises(Conflict, match="skill"):
        dispatch_work(
            db,
            scope=scope,
            command=DispatchWork(
                "work:2", team.id, "party:worker-2", skill.id, NOW, NOW
            ),
        )
