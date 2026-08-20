"""Workforce scheduling and dispatch; callers own transactions."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_workforce.contracts import (
    AddTeamMember,
    CertifyWorkerSkill,
    Conflict,
    CreateShift,
    CreateSkill,
    CreateTeam,
    DispatchWork,
    RecordAvailability,
)
from dotmac_workforce.models import (
    DispatchDecision,
    TeamMembership,
    WorkerSkill,
    WorkforceAvailability,
    WorkforceShift,
    WorkforceSkill,
    WorkforceTeam,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-workforce requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _team(db: Session, tenant_id: UUID, team_id: UUID) -> WorkforceTeam:
    row = db.scalar(
        select(WorkforceTeam).where(
            WorkforceTeam.tenant_id == tenant_id, WorkforceTeam.id == team_id
        )
    )
    if row is None or not row.active:
        raise Conflict("workforce team was not found or is inactive")
    return row


def _skill(db: Session, tenant_id: UUID, skill_id: UUID) -> WorkforceSkill:
    row = db.scalar(
        select(WorkforceSkill).where(
            WorkforceSkill.tenant_id == tenant_id, WorkforceSkill.id == skill_id
        )
    )
    if row is None:
        raise Conflict("workforce skill was not found")
    return row


def create_team(
    db: Session, *, scope: TenantScope, command: CreateTeam
) -> WorkforceTeam:
    row = WorkforceTeam(
        tenant_id=_tenant(scope),
        code=_required(command.code, "team code"),
        name=_required(command.name, "team name"),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def create_skill(
    db: Session, *, scope: TenantScope, command: CreateSkill
) -> WorkforceSkill:
    row = WorkforceSkill(
        tenant_id=_tenant(scope),
        code=_required(command.code, "skill code"),
        name=_required(command.name, "skill name"),
    )
    db.add(row)
    db.flush()
    return row


def add_team_member(
    db: Session, *, scope: TenantScope, command: AddTeamMember
) -> TeamMembership:
    tenant_id = _tenant(scope)
    team = _team(db, tenant_id, command.team_id)
    row = TeamMembership(
        tenant_id=tenant_id,
        team_id=team.id,
        worker_reference=_required(command.worker_reference, "worker reference"),
        active=True,
        joined_at=command.joined_at,
    )
    db.add(row)
    db.flush()
    return row


def certify_worker_skill(
    db: Session, *, scope: TenantScope, command: CertifyWorkerSkill
) -> WorkerSkill:
    tenant_id = _tenant(scope)
    skill = _skill(db, tenant_id, command.skill_id)
    if not 1 <= command.proficiency <= 5:
        raise Conflict("skill proficiency must be between 1 and 5")
    row = WorkerSkill(
        tenant_id=tenant_id,
        worker_reference=_required(command.worker_reference, "worker reference"),
        skill_id=skill.id,
        proficiency=command.proficiency,
        verified_at=command.verified_at,
    )
    db.add(row)
    db.flush()
    return row


def create_shift(
    db: Session, *, scope: TenantScope, command: CreateShift
) -> WorkforceShift:
    tenant_id = _tenant(scope)
    team = _team(db, tenant_id, command.team_id)
    if command.ends_at <= command.starts_at or command.capacity < 1:
        raise Conflict("shift needs a valid window and positive capacity")
    row = WorkforceShift(
        tenant_id=tenant_id,
        team_id=team.id,
        starts_at=command.starts_at,
        ends_at=command.ends_at,
        capacity=command.capacity,
    )
    db.add(row)
    db.flush()
    return row


def record_availability(
    db: Session, *, scope: TenantScope, command: RecordAvailability
) -> WorkforceAvailability:
    if command.ends_at <= command.starts_at:
        raise Conflict("availability end must follow its start")
    row = WorkforceAvailability(
        tenant_id=_tenant(scope),
        worker_reference=_required(command.worker_reference, "worker reference"),
        starts_at=command.starts_at,
        ends_at=command.ends_at,
        available=command.available,
        source_reference=_required(command.source_reference, "source reference"),
    )
    db.add(row)
    db.flush()
    return row


def dispatch_work(
    db: Session, *, scope: TenantScope, command: DispatchWork
) -> DispatchDecision:
    tenant_id = _tenant(scope)
    team = _team(db, tenant_id, command.team_id)
    worker = _required(command.worker_reference, "worker reference")
    _skill(db, tenant_id, command.required_skill_id)
    membership = db.scalar(
        select(TeamMembership.id).where(
            TeamMembership.tenant_id == tenant_id,
            TeamMembership.team_id == team.id,
            TeamMembership.worker_reference == worker,
            TeamMembership.active.is_(True),
        )
    )
    if membership is None:
        raise Conflict("worker is not an active member of the team")
    certified = db.scalar(
        select(WorkerSkill.id).where(
            WorkerSkill.tenant_id == tenant_id,
            WorkerSkill.worker_reference == worker,
            WorkerSkill.skill_id == command.required_skill_id,
        )
    )
    if certified is None:
        raise Conflict("worker lacks the required skill")
    available = db.scalar(
        select(WorkforceAvailability.id).where(
            WorkforceAvailability.tenant_id == tenant_id,
            WorkforceAvailability.worker_reference == worker,
            WorkforceAvailability.available.is_(True),
            WorkforceAvailability.starts_at <= command.scheduled_for,
            WorkforceAvailability.ends_at > command.scheduled_for,
        )
    )
    if available is None:
        raise Conflict("worker is unavailable at the scheduled time")
    if command.shift_id is not None:
        shift = db.scalar(
            select(WorkforceShift).where(
                WorkforceShift.tenant_id == tenant_id,
                WorkforceShift.id == command.shift_id,
                WorkforceShift.team_id == team.id,
                WorkforceShift.starts_at <= command.scheduled_for,
                WorkforceShift.ends_at > command.scheduled_for,
            )
        )
        if shift is None:
            raise Conflict("shift does not cover the dispatch")
    row = DispatchDecision(
        tenant_id=tenant_id,
        work_reference=_required(command.work_reference, "work reference"),
        team_id=team.id,
        worker_reference=worker,
        required_skill_id=command.required_skill_id,
        shift_id=command.shift_id,
        scheduled_for=command.scheduled_for,
        decided_at=command.decided_at,
        rationale="membership, skill and availability verified",
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "add_team_member",
    "certify_worker_skill",
    "create_shift",
    "create_skill",
    "create_team",
    "dispatch_work",
    "record_availability",
]
