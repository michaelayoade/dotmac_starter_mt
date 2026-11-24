"""Job catalogue, generation arming and per-tenant job state.

A declared :class:`~dotmac_kernel.jobs.JobSpec` becomes one timer stream per
(tenant, module, job): owner = the declaring module's code, entity kind
``job``, entity id = the job code, purpose ``run``.  The timer ``generation``
is the job generation: it is the idempotency key of one run.

Nothing here reads a clock or commits.  Every instant is passed in; the caller
(the dispatch transport, a staff command, the reconciler) owns the transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.jobs import JobSpec, resolve_job_handler
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_durable_timers.cadence import next_due
from dotmac_durable_timers.models import JobState, Timer
from dotmac_durable_timers.service import (
    SCHEDULED,
    ScheduleResult,
    TimerIdentity,
    TimerOutput,
    TimerSnapshot,
    current_timer,
    schedule_timer,
)

if TYPE_CHECKING:
    from dotmac_kernel.modules import ModuleRegistry

JOB_DUE_EVENT: Final[str] = "durable_timers.job_due.v1"
JOB_ENTITY_KIND: Final[str] = "job"
JOB_PURPOSE: Final[str] = "run"
#: How long terminal timer generations are kept before the built-in purge may
#: delete them. A scheduled generation is never eligible.
HISTORY_RETENTION: Final[timedelta] = timedelta(days=14)

OUTCOME_OK: Final[str] = "ok"
OUTCOME_FAILED: Final[str] = "failed"
OUTCOME_DEAD_LETTER: Final[str] = "dead_letter"
OUTCOME_SKIPPED_PAUSED: Final[str] = "skipped_paused"
_DETAIL_MAX: Final[int] = 500


class JobPlaneUnsupported(RuntimeError):
    """A declared job asks for a plane this runner does not serve."""


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What a handler returns on success. Failure is an exception, never a value."""

    detail: str | None = None
    counters: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JobRunContext:
    """The third handler argument. ``idempotency_key`` is stable across every
    retry and redelivery of one generation; use it to dedupe an external effect."""

    tenant_id: UUID
    module_code: str
    job_code: str
    generation: int
    timer_id: UUID
    idempotency_key: str
    attempt: int
    scheduled_for: datetime
    started_at: datetime


@dataclass(frozen=True, slots=True)
class JobEntry:
    module_code: str
    spec: JobSpec

    @property
    def key(self) -> tuple[str, str]:
        return (self.module_code, self.spec.code)

    @property
    def identity(self) -> TimerIdentity:
        return TimerIdentity(
            owner=self.module_code,
            entity_kind=JOB_ENTITY_KIND,
            entity_id=self.spec.code,
            purpose=JOB_PURPOSE,
        )


class JobCatalogue:
    """The enabled jobs of one assembly, handlers resolved once at build."""

    def __init__(self, entries: Iterable[JobEntry]) -> None:
        by_key: dict[tuple[str, str], JobEntry] = {}
        handlers: dict[tuple[str, str], Callable[..., object]] = {}
        for entry in entries:
            if entry.spec.plane != ModulePlane.TENANT:
                raise JobPlaneUnsupported(
                    f"job {entry.module_code}.{entry.spec.code} declares plane "
                    f"{ModulePlane(entry.spec.plane).value!r}; this runner serves only the "
                    "tenant plane (platform-plane jobs need a platform runner)"
                )
            by_key[entry.key] = entry
            handlers[entry.key] = resolve_job_handler(entry.spec)
        self._entries = by_key
        self._handlers = handlers

    @classmethod
    def from_registry(
        cls, registry: ModuleRegistry, disabled: Set[str] = frozenset()
    ) -> JobCatalogue:
        return cls(JobEntry(code, spec) for code, spec in registry.jobs(disabled))

    def entries(self) -> tuple[JobEntry, ...]:
        return tuple(self._entries.values())

    def get(self, module_code: str, job_code: str) -> JobEntry | None:
        return self._entries.get((module_code, job_code))

    def handler(self, entry: JobEntry) -> Callable[..., object]:
        return self._handlers[entry.key]


def tenant_timezone(db: Session, tenant_id: UUID) -> ZoneInfo:
    """The tenant's display timezone (the cron timezone), UTC when unset."""
    from dotmac_kernel.display import load_display

    return load_display(db, tenant_id).timezone


def get_state(db: Session, tenant_id: UUID, entry: JobEntry) -> JobState | None:
    return db.scalar(
        select(JobState).where(
            JobState.tenant_id == tenant_id,
            JobState.module_code == entry.module_code,
            JobState.job_code == entry.spec.code,
        )
    )


def ensure_state(
    db: Session, tenant_id: UUID, entry: JobEntry, *, now: datetime
) -> JobState:
    state = get_state(db, tenant_id, entry)
    if state is None:
        state = JobState(
            tenant_id=tenant_id,
            module_code=entry.module_code,
            job_code=entry.spec.code,
            paused=False,
            consecutive_failures=0,
            updated_at=now,
        )
        db.add(state)
        db.flush()
    return state


def arm(
    db: Session,
    *,
    tenant_id: UUID,
    entry: JobEntry,
    now: datetime,
    after: datetime | None = None,
    due_at: datetime | None = None,
) -> ScheduleResult:
    """Schedule the next generation (superseding any live one) in ``db``'s
    transaction. ``due_at`` forces the instant (run-now); otherwise cadence
    counts from ``after`` (default ``now``) in the tenant timezone."""
    if due_at is None:
        # Only a cron cadence reads the tenant timezone setting.
        tz = tenant_timezone(db, tenant_id) if entry.spec.cron else ZoneInfo("UTC")
        due_at = next_due(entry.spec, after=after or now, tz=tz)
    return schedule_timer(
        db,
        scope=TenantScope(tenant_id),
        identity=entry.identity,
        due_at=due_at,
        output=TimerOutput(JOB_DUE_EVENT),
        recorded_at=now,
        expires_at=now + HISTORY_RETENTION,
    )


def _outbox_status(db: Session, snapshot: TimerSnapshot) -> str | None:
    # Function-local: importing the messaging package reaches the process DB
    # authority, which package discovery must not do (see service.schedule_timer).
    from dotmac_kernel.messaging.models import OutboxEvent

    event_id = db.scalar(
        select(Timer.outbox_event_id).where(Timer.id == snapshot.timer_id)
    )
    if event_id is None:
        return None
    return db.scalar(select(OutboxEvent.status).where(OutboxEvent.id == event_id))


def needs_arming(db: Session, tenant_id: UUID, entry: JobEntry) -> bool:
    """True when an unpaused job has no live, deliverable generation.

    Covers a first run, a stream whose last generation was terminal, a
    generation whose outbox row was settled without running it (a paused fire)
    and one whose outbox row was dead-lettered.
    """
    snapshot = current_timer(db, scope=TenantScope(tenant_id), identity=entry.identity)
    if snapshot is None or snapshot.status != SCHEDULED:
        return True
    return _outbox_status(db, snapshot) in {"sent", "dead"}


def reconcile_tenant(
    db: Session, *, tenant_id: UUID, catalogue: JobCatalogue, now: datetime
) -> int:
    """Make sure every unpaused declared job has one live generation. Returns
    how many were armed. Idempotent; safe to run from several runners (the
    identity lock serialises, the partial unique index forbids two live ones)."""
    armed = 0
    for entry in catalogue.entries():
        state = get_state(db, tenant_id, entry)
        if state is not None and state.paused:
            continue
        if needs_arming(db, tenant_id, entry):
            arm(db, tenant_id=tenant_id, entry=entry, now=now)
            armed += 1
    return armed


def _detail(value: str | None) -> str | None:
    return None if value is None else value[:_DETAIL_MAX]


def record_run(
    db: Session,
    *,
    tenant_id: UUID,
    entry: JobEntry,
    now: datetime,
    generation: int,
    outcome: str,
    detail: str | None,
) -> JobState:
    state = ensure_state(db, tenant_id, entry, now=now)
    state.last_run_at = now
    state.last_generation = generation
    state.last_outcome = outcome
    state.last_detail = _detail(detail)
    state.updated_at = now
    if outcome == OUTCOME_OK:
        state.consecutive_failures = 0
        state.dead_letter_event_id = None
        state.dead_letter_generation = None
        state.dead_lettered_at = None
    elif outcome == OUTCOME_FAILED:
        state.consecutive_failures += 1
    db.flush()
    return state


__all__ = [
    "HISTORY_RETENTION",
    "JOB_DUE_EVENT",
    "JOB_ENTITY_KIND",
    "JOB_PURPOSE",
    "OUTCOME_DEAD_LETTER",
    "OUTCOME_FAILED",
    "OUTCOME_OK",
    "OUTCOME_SKIPPED_PAUSED",
    "JobCatalogue",
    "JobEntry",
    "JobOutcome",
    "JobPlaneUnsupported",
    "JobRunContext",
    "JobState",
    "arm",
    "ensure_state",
    "get_state",
    "needs_arming",
    "reconcile_tenant",
    "record_run",
    "tenant_timezone",
]
