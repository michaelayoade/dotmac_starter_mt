"""Built-in jobs the module declares for itself: bounded retention purges.

Both run per tenant, in the tenant RLS session, through the ordinary runner (so
they are paused, audited and dead-lettered like any other job).  Each purge is
bounded per run so one tick never holds a long lock; a backlog drains over
successive ticks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from dotmac_kernel.cache import Scope
from dotmac_kernel.jobs import JobSpec
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dotmac_durable_timers.jobs import JOB_DUE_EVENT, JobOutcome, JobRunContext
from dotmac_durable_timers.service import purge_history

PURGE_BATCH: Final[int] = 1000
PURGE_BATCHES_PER_RUN: Final[int] = 10
#: Settled (``sent``) ``job_due`` rows are kept this long, then purged.
PUBLISHED_OUTBOX_RETENTION: Final[timedelta] = timedelta(days=7)


def purge_timer_history(
    session: Session, scope: Scope, run_context: JobRunContext
) -> JobOutcome:
    """Delete expired terminal timer generations; never a scheduled one."""
    deleted = 0
    for _ in range(PURGE_BATCHES_PER_RUN):
        batch = purge_history(
            session, scope=scope, before=run_context.started_at, limit=PURGE_BATCH
        )
        deleted += batch
        if batch < PURGE_BATCH:
            break
    return JobOutcome(detail=f"purged {deleted}", counters={"deleted": deleted})


def purge_published_outbox(
    session: Session, scope: Scope, run_context: JobRunContext
) -> JobOutcome:
    """Delete this tenant's long-settled ``sent`` rows of THIS module's own
    ``job_due`` event only (RLS-scoped). Another module's outbox trail is never
    this module's to retain or purge (ADR-0006 ownership)."""
    from dotmac_kernel.messaging.models import OutboxEvent

    cutoff = run_context.started_at - PUBLISHED_OUTBOX_RETENTION
    deleted = 0
    for _ in range(PURGE_BATCHES_PER_RUN):
        doomed = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.event_type == JOB_DUE_EVENT,
                OutboxEvent.status == "sent",
                OutboxEvent.sent_at < cutoff,
            )
            .order_by(OutboxEvent.sent_at)
            .limit(PURGE_BATCH)
        )
        result = session.execute(delete(OutboxEvent).where(OutboxEvent.id.in_(doomed)))
        batch = int(getattr(result, "rowcount", 0) or 0)
        deleted += batch
        if batch < PURGE_BATCH:
            break
    return JobOutcome(detail=f"purged {deleted}", counters={"deleted": deleted})


BUILTIN_JOBS: Final[tuple[JobSpec, ...]] = (
    JobSpec(
        code="timer_history_purge",
        handler="dotmac_durable_timers.builtin:purge_timer_history",
        plane="tenant",
        interval_seconds=6 * 3600,
        description="Delete expired terminal timer generations.",
    ),
    JobSpec(
        code="published_outbox_purge",
        handler="dotmac_durable_timers.builtin:purge_published_outbox",
        plane="tenant",
        interval_seconds=6 * 3600,
        description="Delete this module's own long-settled job_due outbox rows.",
    ),
)

__all__ = [
    "BUILTIN_JOBS",
    "PUBLISHED_OUTBOX_RETENTION",
    "purge_published_outbox",
    "purge_timer_history",
]
