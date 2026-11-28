"""Durable timers' staff JSON API for declared jobs (contract 2, thin adapter).

Permissions: ``durable_timers.jobs.read`` (list, dead letters),
``durable_timers.jobs.run`` (run now) and ``durable_timers.jobs.manage``
(pause, resume).  Every command writes one tenant audit row naming the actor.
"""

from __future__ import annotations

from datetime import datetime

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import get_db, require_permission, require_tenant
from dotmac_kernel.exceptions import NotFoundError
from dotmac_kernel.models import Party, Tenant
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dotmac_durable_timers import admin
from dotmac_durable_timers.jobs import JobCatalogue, JobEntry
from dotmac_durable_timers.runtime import request_catalogue, utc_now

router = APIRouter(
    prefix="/durable-timers",
    tags=["durable-timers"],
    dependencies=[Depends(require_tenant)],
)


class JobRead(BaseModel):
    module_code: str
    job_code: str
    description: str
    cadence: str
    paused: bool
    paused_at: datetime | None
    paused_by: str | None
    generation: int | None
    next_due_at: datetime | None
    last_run_at: datetime | None
    last_outcome: str | None
    last_detail: str | None
    consecutive_failures: int
    dead_lettered_at: datetime | None
    dead_letter_generation: int | None


class CommandResult(BaseModel):
    module_code: str
    job_code: str
    changed: bool


def _entry(catalogue: JobCatalogue, module_code: str, job_code: str) -> JobEntry:
    entry = catalogue.get(module_code, job_code)
    if entry is None:
        raise NotFoundError("no such job")
    return entry


def _audit(
    db: Session,
    tenant: Tenant,
    actor: Party,
    *,
    action: str,
    entry: JobEntry,
    details: dict[str, object] | None = None,
) -> None:
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action=action,
        entity_type="job",
        entity_id=f"{entry.module_code}:{entry.spec.code}",
        details=details or {},
    )


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    _: Party = Depends(require_permission("durable_timers.jobs.read")),
) -> list[admin.JobView]:
    return admin.list_jobs(db, tenant_id=tenant.id, catalogue=catalogue)


@router.get("/dead-letters", response_model=list[JobRead])
def list_dead_letters(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    _: Party = Depends(require_permission("durable_timers.jobs.read")),
) -> list[admin.JobView]:
    return admin.dead_letters(
        admin.list_jobs(db, tenant_id=tenant.id, catalogue=catalogue)
    )


@router.post("/jobs/{module_code}/{job_code}/run", response_model=CommandResult)
def run_job_now(
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    actor: Party = Depends(require_permission("durable_timers.jobs.run")),
) -> CommandResult:
    entry = _entry(catalogue, module_code, job_code)
    scheduled = admin.run_now(db, tenant_id=tenant.id, entry=entry, now=now)
    _audit(
        db,
        tenant,
        actor,
        action="durable_timers.job.run_now",
        entry=entry,
        details={"generation": scheduled.generation},
    )
    return CommandResult(module_code=module_code, job_code=job_code, changed=True)


@router.post("/jobs/{module_code}/{job_code}/pause", response_model=CommandResult)
def pause_job(
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    actor: Party = Depends(require_permission("durable_timers.jobs.manage")),
) -> CommandResult:
    entry = _entry(catalogue, module_code, job_code)
    changed = admin.pause(
        db, tenant_id=tenant.id, entry=entry, now=now, actor=str(actor.id)
    )
    _audit(
        db,
        tenant,
        actor,
        action="durable_timers.job.pause",
        entry=entry,
        details={"changed": changed},
    )
    return CommandResult(module_code=module_code, job_code=job_code, changed=changed)


@router.post("/jobs/{module_code}/{job_code}/resume", response_model=CommandResult)
def resume_job(
    module_code: str,
    job_code: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    catalogue: JobCatalogue = Depends(request_catalogue),
    now: datetime = Depends(utc_now),
    actor: Party = Depends(require_permission("durable_timers.jobs.manage")),
) -> CommandResult:
    entry = _entry(catalogue, module_code, job_code)
    changed = admin.resume(db, tenant_id=tenant.id, entry=entry, now=now)
    _audit(
        db,
        tenant,
        actor,
        action="durable_timers.job.resume",
        entry=entry,
        details={"changed": changed},
    )
    return CommandResult(module_code=module_code, job_code=job_code, changed=changed)


__all__ = ["router"]
