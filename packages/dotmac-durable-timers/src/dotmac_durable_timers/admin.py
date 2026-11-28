"""Staff reads and governed commands over declared jobs (service layer).

Routers and web pages call these; they validate, mutate and flush, never
commit, and never write the audit row themselves (the adapter does, with the
authenticated actor, so every command is attributable).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.exceptions import ConflictError
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_durable_timers.jobs import (
    JobCatalogue,
    JobEntry,
    arm,
    ensure_state,
    needs_arming,
)
from dotmac_durable_timers.models import JobState
from dotmac_durable_timers.service import SCHEDULED, ScheduleResult, current_timer


@dataclass(frozen=True, slots=True)
class JobView:
    """Detached, template-safe view of one job for one tenant."""

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

    @property
    def key(self) -> str:
        return f"{self.module_code}:{self.job_code}"


def _cadence(entry: JobEntry) -> str:
    spec = entry.spec
    if spec.interval_seconds is not None:
        return f"every {spec.interval_seconds}s"
    return f"cron {spec.cron}"


def list_jobs(
    db: Session, *, tenant_id: UUID, catalogue: JobCatalogue
) -> list[JobView]:
    states = {
        (s.module_code, s.job_code): s
        for s in db.scalars(select(JobState).where(JobState.tenant_id == tenant_id))
    }
    views: list[JobView] = []
    for entry in catalogue.entries():
        state = states.get(entry.key)
        snapshot = current_timer(
            db, scope=TenantScope(tenant_id), identity=entry.identity
        )
        live = snapshot is not None and snapshot.status == SCHEDULED
        views.append(
            JobView(
                module_code=entry.module_code,
                job_code=entry.spec.code,
                description=entry.spec.description,
                cadence=_cadence(entry),
                paused=bool(state and state.paused),
                paused_at=state.paused_at if state else None,
                paused_by=state.paused_by if state else None,
                generation=snapshot.generation if snapshot else None,
                next_due_at=snapshot.due_at if live and snapshot else None,
                last_run_at=state.last_run_at if state else None,
                last_outcome=state.last_outcome if state else None,
                last_detail=state.last_detail if state else None,
                consecutive_failures=state.consecutive_failures if state else 0,
                dead_lettered_at=state.dead_lettered_at if state else None,
                dead_letter_generation=state.dead_letter_generation if state else None,
            )
        )
    return views


def dead_letters(views: list[JobView]) -> list[JobView]:
    return [v for v in views if v.dead_lettered_at is not None]


def run_now(
    db: Session, *, tenant_id: UUID, entry: JobEntry, now: datetime
) -> ScheduleResult:
    """Make the job due immediately as its next generation (the live one is
    superseded; cadence resumes from the run's completion)."""
    state = ensure_state(db, tenant_id, entry, now=now)
    if state.paused:
        raise ConflictError("job is paused; resume it before running it now")
    return arm(db, tenant_id=tenant_id, entry=entry, now=now, due_at=now)


def pause(
    db: Session, *, tenant_id: UUID, entry: JobEntry, now: datetime, actor: str
) -> bool:
    """Pause scheduling. Returns False when it already was (no change)."""
    state = ensure_state(db, tenant_id, entry, now=now)
    if state.paused:
        return False
    state.paused = True
    state.paused_at = now
    state.paused_by = actor[:255]
    state.updated_at = now
    db.flush()
    return True


def resume(db: Session, *, tenant_id: UUID, entry: JobEntry, now: datetime) -> bool:
    """Resume scheduling and re-arm if no deliverable generation is live."""
    state = ensure_state(db, tenant_id, entry, now=now)
    if not state.paused:
        return False
    state.paused = False
    state.paused_at = None
    state.paused_by = None
    state.updated_at = now
    db.flush()
    if needs_arming(db, tenant_id, entry):
        arm(db, tenant_id=tenant_id, entry=entry, now=now)
    return True


__all__ = ["JobView", "dead_letters", "list_jobs", "pause", "resume", "run_now"]
