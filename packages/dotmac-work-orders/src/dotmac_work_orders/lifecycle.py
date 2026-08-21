"""Persistence-free work-order execution lifecycle, ported from Sub."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Status(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES: Final[frozenset[Status]] = frozenset(
    {Status.COMPLETED, Status.CANCELED}
)


class Event(StrEnum):
    ACCEPT = "accept"
    EN_ROUTE = "en_route"
    ARRIVED = "arrived"
    START = "start"
    PAUSE = "pause"
    HOLD = "hold"
    RESUME = "resume"
    COMPLETE = "complete"
    UNABLE_TO_COMPLETE = "unable_to_complete"


class InvalidTransition(ValueError):
    """The event is not valid from the work order's current status."""


_ALLOWED_FROM: Final[dict[Event, frozenset[Status]]] = {
    Event.ACCEPT: frozenset({Status.SCHEDULED, Status.DISPATCHED}),
    Event.EN_ROUTE: frozenset({Status.SCHEDULED, Status.DISPATCHED, Status.PAUSED}),
    Event.ARRIVED: frozenset(
        {
            Status.SCHEDULED,
            Status.DISPATCHED,
            Status.IN_PROGRESS,
            Status.PAUSED,
        }
    ),
    Event.START: frozenset({Status.SCHEDULED, Status.DISPATCHED}),
    Event.PAUSE: frozenset({Status.IN_PROGRESS}),
    Event.HOLD: frozenset({Status.IN_PROGRESS}),
    Event.RESUME: frozenset({Status.PAUSED}),
    Event.COMPLETE: frozenset({Status.IN_PROGRESS}),
    Event.UNABLE_TO_COMPLETE: frozenset(
        {
            Status.SCHEDULED,
            Status.DISPATCHED,
            Status.IN_PROGRESS,
            Status.PAUSED,
        }
    ),
}

_TARGET: Final[dict[Event, Status | None]] = {
    Event.ACCEPT: None,
    Event.EN_ROUTE: Status.DISPATCHED,
    Event.ARRIVED: None,
    Event.START: Status.IN_PROGRESS,
    Event.PAUSE: Status.PAUSED,
    Event.HOLD: Status.PAUSED,
    Event.RESUME: Status.IN_PROGRESS,
    Event.COMPLETE: Status.COMPLETED,
    Event.UNABLE_TO_COMPLETE: Status.CANCELED,
}


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    previous_status: Status
    new_status: Status
    event: Event
    starts_work: bool
    stops_work: bool
    completes_work: bool


def decide_transition(status: Status, event: Event) -> TransitionDecision:
    """Validate one event and return every generic lifecycle consequence."""
    if status not in _ALLOWED_FROM[event]:
        raise InvalidTransition(f"cannot {event.value} a work order in {status.value}")
    target = _TARGET[event]
    # Sub preserves PAUSED when a paused technician reports en-route; the event
    # is evidence of movement, not an implicit resume.
    if status is Status.PAUSED and event is Event.EN_ROUTE:
        target = None
    return TransitionDecision(
        previous_status=status,
        new_status=target or status,
        event=event,
        starts_work=event in {Event.START, Event.RESUME},
        stops_work=event
        in {Event.PAUSE, Event.HOLD, Event.COMPLETE, Event.UNABLE_TO_COMPLETE},
        completes_work=event is Event.COMPLETE,
    )


__all__ = [
    "TERMINAL_STATUSES",
    "Event",
    "InvalidTransition",
    "Status",
    "TransitionDecision",
    "decide_transition",
]
