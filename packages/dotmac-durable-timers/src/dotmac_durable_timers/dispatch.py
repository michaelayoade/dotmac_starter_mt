"""``JobDispatchTransport``: the relay transport for ``durable_timers.job_due.v1``.

A ``DeliveryTransport`` (and ``DeadLetterAware``) for the kernel outbox worker.
It adds no queue, claim, lease or retry engine; claiming, leasing, backoff
settling and dead-lettering stay with the kernel relay.

One due event is processed in ONE tenant-RLS transaction:

1. ``accept_trigger`` locks the job's identity and accepts only the CURRENT
   generation (at most once per generation; a racing runner or a redelivery
   sees a newer generation and is recorded as stale, running nothing);
2. the handler runs with ``(session, TenantScope, JobRunContext)``;
3. the next generation is scheduled; the outcome is recorded;
4. the worker commits.  A crash anywhere before that commit rolls the whole
   transaction back, including the acceptance, so the relay's redelivery runs
   the generation again under the SAME idempotency key.

A failing handler is rolled back to a savepoint (acceptance included), the
failure is recorded and committed, and the relay is told how to retry:
``DeliveryDeferred(retry_after=<job backoff>)`` while the job's own
``max_attempts`` has not been reached, ``DeliveryTerminal`` once it has.  The
attempt number is ``attempts + deferrals + 1``, both carried on the claimed
event, so no extra counter exists.  The relay clamps ``retry_after`` into its
policy range (30 s to 1 h by default).

Dead letters (from this transport or from the relay's own policy) reach
``dead_letter``: it records the outcome, writes one audit row and raises a
health observation through the assembly-bound sink (a failing sink is logged,
never raised), before the outbox row is settled dead.

A handler must not commit or roll back the session it is given: the run is one
transaction owned by this transport and the relay worker.

There is no staff queue; the job's next generation is armed by the reconciler,
so a dead letter does not stop the job.

The module reads no clock: ``clock`` is injected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.delivery import DeliveryDeferred, DeliveryTerminal
from dotmac_kernel.messaging.relay import ClaimedEvent
from dotmac_kernel.models import Tenant
from sqlalchemy.orm import Session

from dotmac_durable_timers.health import JobDeadLetter, JobHealthSink
from dotmac_durable_timers.jobs import (
    JOB_DUE_EVENT,
    JOB_ENTITY_KIND,
    JOB_PURPOSE,
    OUTCOME_DEAD_LETTER,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_SKIPPED_PAUSED,
    JobCatalogue,
    JobEntry,
    JobOutcome,
    JobRunContext,
    arm,
    ensure_state,
    get_state,
    record_run,
)
from dotmac_durable_timers.service import (
    AcceptanceOutcome,
    TimerError,
    TimerTrigger,
    accept_trigger,
)

logger = logging.getLogger("dotmac_durable_timers.dispatch")

AUDIT_ACTION_DEAD_LETTER = "durable_timers.job.dead_letter"


class InvalidJobResult(TypeError):
    """A handler returned something other than a ``JobOutcome``."""


def idempotency_key(entry: JobEntry, tenant_id: object, generation: int) -> str:
    return f"{tenant_id}:{entry.module_code}:{entry.spec.code}:{generation}"


class JobDispatchTransport:
    def __init__(
        self,
        *,
        catalogue: JobCatalogue,
        health: JobHealthSink,
        clock: Callable[[], datetime],
    ) -> None:
        self._catalogue = catalogue
        self._health = health
        self._clock = clock

    # ── helpers ─────────────────────────────────────────────────────────────

    def _resolve(self, event: ClaimedEvent) -> tuple[TimerTrigger, JobEntry]:
        if event.event_type != JOB_DUE_EVENT:
            raise DeliveryTerminal(f"unexpected event type {event.event_type!r}")
        try:
            trigger = TimerTrigger.from_payload(event.payload)
        except TimerError as exc:
            raise DeliveryTerminal(f"invalid trigger: {exc.code}") from exc
        identity = trigger.identity
        entry = self._catalogue.get(identity.owner, identity.entity_id)
        if (
            entry is None
            or identity.entity_kind != JOB_ENTITY_KIND
            or identity.purpose != JOB_PURPOSE
        ):
            raise DeliveryTerminal("job is not declared by any enabled module")
        return trigger, entry

    # ── DeliveryTransport ───────────────────────────────────────────────────

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        trigger, entry = self._resolve(event)
        now = self._clock()
        tenant_id = event.tenant_id
        scope = TenantScope(tenant_id)

        tenant = tenant_db.get(Tenant, tenant_id)
        if (
            tenant is None
            or not tenant.is_active
            or tenant.suspended_at is not None
            or tenant.deleted_at is not None
        ):
            # An inactive tenant runs nothing and arms nothing; the event is
            # settled and the stream simply stops until the tenant is back
            # (the reconciler re-arms it then).
            logger.info("job event for inactive tenant %s settled unrun", tenant_id)
            return

        state = get_state(tenant_db, tenant_id, entry)
        if state is not None and state.dead_letter_event_id == event.id:
            # The dead-letter hook already recorded this event, so the relay is
            # only re-delivering it; never run the handler again.
            raise DeliveryTerminal("event already dead-lettered")
        if state is not None and state.paused:
            # Settled without accepting: the generation row stays `scheduled`
            # (not lost); resume re-arms it as the next generation.
            record_run(
                tenant_db,
                tenant_id=tenant_id,
                entry=entry,
                now=now,
                generation=trigger.generation,
                outcome=OUTCOME_SKIPPED_PAUSED,
                detail=None,
            )
            return

        attempt = event.attempts + event.deferrals + 1
        try:
            with tenant_db.begin_nested():
                result = accept_trigger(
                    tenant_db, scope=scope, trigger=trigger, accepted_at=now
                )
                if result.outcome is not AcceptanceOutcome.CURRENT or result.replayed:
                    return  # stale / canceled / already run: nothing to do
                context = JobRunContext(
                    tenant_id=tenant_id,
                    module_code=entry.module_code,
                    job_code=entry.spec.code,
                    generation=trigger.generation,
                    timer_id=trigger.timer_id,
                    idempotency_key=idempotency_key(
                        entry, tenant_id, trigger.generation
                    ),
                    attempt=attempt,
                    scheduled_for=trigger.due_at,
                    started_at=now,
                )
                outcome = self._catalogue.handler(entry)(tenant_db, scope, context)
                if not isinstance(outcome, JobOutcome):
                    raise InvalidJobResult("handler must return a JobOutcome")
                # The next generation, in this same transaction.
                arm(
                    tenant_db,
                    tenant_id=tenant_id,
                    entry=entry,
                    now=now,
                    after=max(now, trigger.due_at),
                )
                record_run(
                    tenant_db,
                    tenant_id=tenant_id,
                    entry=entry,
                    now=now,
                    generation=trigger.generation,
                    outcome=OUTCOME_OK,
                    detail=outcome.detail,
                )
            return
        except Exception as exc:
            # The savepoint rolled back the handler's writes AND the acceptance.
            logger.warning(
                "job %s.%s generation %s attempt %s failed: %s",
                entry.module_code,
                entry.spec.code,
                trigger.generation,
                attempt,
                type(exc).__name__,
            )
            record_run(
                tenant_db,
                tenant_id=tenant_id,
                entry=entry,
                now=now,
                generation=trigger.generation,
                outcome=OUTCOME_FAILED,
                detail=type(exc).__name__,
            )
            # Commit the failure record now: the worker rolls back on raise.
            tenant_db.commit()
            reason = f"{type(exc).__name__} on attempt {attempt}"
            if attempt >= entry.spec.max_attempts:
                raise DeliveryTerminal(f"job attempts exhausted: {reason}") from exc
            backoff = entry.spec.backoff_seconds
            retry_after = backoff[min(attempt - 1, len(backoff) - 1)]
            raise DeliveryDeferred(reason, retry_after=float(retry_after)) from exc

    # ── DeadLetterAware ─────────────────────────────────────────────────────

    def dead_letter(self, event: ClaimedEvent, tenant_db: Session) -> None:
        now = self._clock()
        module_code = "unknown"
        job_code = "unknown"
        generation: int | None = None
        error = "unknown"
        try:
            trigger, entry = self._resolve(event)
        except DeliveryTerminal:
            pass  # still audited and observed, without job attribution
        else:
            module_code, job_code = entry.module_code, entry.spec.code
            generation = trigger.generation
            state = ensure_state(tenant_db, event.tenant_id, entry, now=now)
            error = state.last_detail or "unknown"
            if state.dead_letter_event_id != event.id:
                state.last_outcome = OUTCOME_DEAD_LETTER
                state.last_run_at = now
                state.last_generation = generation
                state.dead_letter_event_id = event.id
                state.dead_letter_generation = generation
                state.dead_lettered_at = now
                state.updated_at = now
                tenant_db.flush()
                self._audit(
                    tenant_db, event, module_code, job_code, generation, error, now
                )
        if generation is None:
            self._audit(tenant_db, event, module_code, job_code, None, error, now)
        try:
            self._health.job_dead_lettered(
                JobDeadLetter(
                    event_id=event.id,
                    tenant_id=event.tenant_id,
                    module_code=module_code,
                    job_code=job_code,
                    generation=generation,
                    attempts=event.attempts + event.deferrals + 1,
                    error=error,
                    observed_at=now,
                )
            )
        except Exception:
            # The audit row and the job state are the durable record. A failing
            # sink must never keep the event claimed: it would be reclaimed and
            # the handler run again every lease period.
            logger.exception(
                "health sink failed for dead-lettered event %s (job %s.%s)",
                event.id,
                module_code,
                job_code,
            )

    @staticmethod
    def _audit(
        db: Session,
        event: ClaimedEvent,
        module_code: str,
        job_code: str,
        generation: int | None,
        error: str,
        now: datetime,
    ) -> None:
        write_audit_event(
            db,
            tenant_id=event.tenant_id,
            actor_type="system",
            actor_id="durable_timers.runner",
            action=AUDIT_ACTION_DEAD_LETTER,
            entity_type="job",
            entity_id=f"{module_code}:{job_code}",
            is_success=False,
            occurred_at=now,
            details={
                "event_id": str(event.id),
                "generation": generation,
                "attempts": event.attempts + event.deferrals + 1,
                "error": error,
            },
        )


__all__ = [
    "AUDIT_ACTION_DEAD_LETTER",
    "InvalidJobResult",
    "JobDispatchTransport",
    "idempotency_key",
]
