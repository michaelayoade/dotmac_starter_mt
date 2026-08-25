"""Response-time promises, and the four things they are deliberately not.

A response obligation is one promise about time on one subject: answer within
N seconds, resolve within M. The clock that measures it is the only thing this
module owns.

It is NOT the consequence. `dotmac-operational-escalations` decides whether a
breach should escalate, under which policy version, to what level and who
answered — for tickets, outages and inboxes alike. A breach here is an
append-only OBSERVATION with no status, and the commands RETURN the escalation
requests a product forwards.

It is NOT a scheduler. `dotmac-durable-timers` owns scheduling; this module
exposes a bounded, indexed sweep the assembly drives, and never rescans.

It is NOT delivery. Messaging and Integrator own transport and its outcomes.

It is NOT the subject. `subject_reference` is opaque — a ticket, a conversation,
a work order. The module never reads what the promise is about, which is what
lets four domains share one clock.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ResponseObligationError(Exception):
    """Base refusal."""


class Conflict(ResponseObligationError):
    """The policy, target or clock state is inadmissible."""


class ObligationKind(enum.StrEnum):
    """The four promises a support desk actually makes.

    They are separate clocks, not one "SLA", because they start and stop at
    different moments and a desk can hit one while missing another. Collapsing
    them is how "we answered in 3 minutes" and "they waited 4 hours in the
    queue" end up as the same number.
    """

    FIRST_RESPONSE = "FIRST_RESPONSE"
    NEXT_RESPONSE = "NEXT_RESPONSE"
    QUEUE_WAIT = "QUEUE_WAIT"
    RESOLUTION = "RESOLUTION"


class ClockStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    MET = "MET"
    BREACHED = "BREACHED"
    CANCELLED = "CANCELLED"


class ObservationKind(enum.StrEnum):
    """A threshold the clock crossed. Both are facts, neither is a decision."""

    WARNING = "WARNING"
    BREACH = "BREACH"


@dataclass(frozen=True, slots=True)
class RegisterPolicy:
    """A named promise set for one subject type.

    `subject_type` is an open product-declared string, not an enum: the source
    already carried ticket, work_order, project and project_task, and a shared
    owner that enumerates its consumers' subjects has to be edited every time a
    new one adopts it.
    """

    code: str
    name: str
    subject_type: str


@dataclass(frozen=True, slots=True)
class SetTarget:
    """One promise: this kind of response, at this priority, within this long.

    `priority` is nullable and a NULL row is the DEFAULT that applies when no
    priority-specific row matches — the source's shape, kept because it lets a
    desk state "4 hours, except urgent which is 30 minutes" in two rows rather
    than one row per priority forever.

    `warning_seconds` is measured back from the due instant, not forward from
    the start: a warning is "you have this long left", and expressing it from
    the start silently changes meaning every time the target moves.
    """

    policy_id: UUID
    kind: ObligationKind
    target_seconds: int
    priority: str | None = None
    warning_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class StartClock:
    """Begin measuring one promise against one subject.

    `dedup_key` makes starting idempotent. A conversation that arrives twice
    through a retried webhook must not acquire two first-response clocks, since
    the second would be measured from the wrong instant and breach later.
    """

    policy_code: str
    subject_reference: str
    kind: ObligationKind
    started_at: datetime
    dedup_key: str
    priority: str | None = None


class PauseReason(enum.StrEnum):
    """Why a clock stopped counting. Recorded, never inferred.

    `OUTSIDE_BUSINESS_HOURS` is how a calendar reaches this module: the product
    pauses at close and resumes at open. Deliberately no calendar engine lives
    here — see the README. A pause with no recorded reason cannot answer "why
    was this clock stopped for fourteen hours", which is the first question
    asked about every disputed breach.
    """

    WAITING_ON_CUSTOMER = "WAITING_ON_CUSTOMER"
    WAITING_ON_THIRD_PARTY = "WAITING_ON_THIRD_PARTY"
    OUTSIDE_BUSINESS_HOURS = "OUTSIDE_BUSINESS_HOURS"
    SUSPENDED_BY_OPERATOR = "SUSPENDED_BY_OPERATOR"


@dataclass(frozen=True, slots=True)
class PauseClock:
    clock_id: UUID
    reason: PauseReason
    paused_at: datetime
    actor_reference: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeClock:
    clock_id: UUID
    resumed_at: datetime
    actor_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteClock:
    """The promise was kept — or missed, if this lands after `due_at`.

    Completion does not erase a breach. A clock completed late is `BREACHED`
    with a completion time, because "we answered, eventually" and "we answered
    in time" are different facts and only one of them is the promise.
    """

    clock_id: UUID
    completed_at: datetime
    actor_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CancelClock:
    """The promise stopped applying — the subject was merged, withdrawn, or
    resolved by something other than a response. Not a breach, not a success."""

    clock_id: UUID
    cancelled_at: datetime
    reason: str
    actor_reference: str | None = None


@dataclass(frozen=True, slots=True)
class SweepDueClocks:
    """Record every threshold crossed up to `observed_at`.

    Bounded by `limit` and driven by the assembly's timer, never a rescan loop:
    the index is (tenant, status, due_at), so the sweep reads the front of the
    queue rather than the table.
    """

    observed_at: datetime
    limit: int = 100


@dataclass(frozen=True, slots=True)
class EscalationRequested:
    """What a product forwards to `dotmac-operational-escalations`.

    Returned, never delivered: modules never import each other. The `dedup_key`
    is derived from the observation, so a re-run of the sweep asks for the same
    escalation rather than a second one.
    """

    observation_id: UUID
    clock_id: UUID
    subject_type: str
    subject_reference: str
    kind: ObligationKind
    observation: ObservationKind
    severity: str
    dedup_key: str
    due_at: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SweptObligations:
    warned: tuple[UUID, ...]
    breached: tuple[UUID, ...]
    escalation_requests: tuple[EscalationRequested, ...]


__all__ = [
    "CancelClock",
    "ClockStatus",
    "CompleteClock",
    "Conflict",
    "EscalationRequested",
    "ObligationKind",
    "ObservationKind",
    "PauseClock",
    "PauseReason",
    "RegisterPolicy",
    "ResponseObligationError",
    "ResumeClock",
    "SetTarget",
    "StartClock",
    "SweepDueClocks",
    "SweptObligations",
]
