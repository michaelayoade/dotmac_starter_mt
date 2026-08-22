"""Ported Sub work-order transition parity, expressed without persistence."""

from __future__ import annotations

import pytest
from dotmac_work_orders import (
    Event,
    InvalidTransition,
    Status,
    TransitionDecision,
    decide_transition,
)


@pytest.mark.parametrize(
    ("status", "event", "expected"),
    (
        (Status.SCHEDULED, Event.ACCEPT, Status.SCHEDULED),
        (Status.SCHEDULED, Event.EN_ROUTE, Status.DISPATCHED),
        (Status.DISPATCHED, Event.ARRIVED, Status.DISPATCHED),
        (Status.DISPATCHED, Event.START, Status.IN_PROGRESS),
        (Status.IN_PROGRESS, Event.PAUSE, Status.PAUSED),
        (Status.IN_PROGRESS, Event.HOLD, Status.PAUSED),
        (Status.PAUSED, Event.RESUME, Status.IN_PROGRESS),
        (Status.IN_PROGRESS, Event.COMPLETE, Status.COMPLETED),
        (Status.PAUSED, Event.UNABLE_TO_COMPLETE, Status.CANCELED),
    ),
)
def test_subs_execution_transitions_are_preserved(
    status: Status, event: Event, expected: Status
) -> None:
    decision = decide_transition(status, event)
    assert isinstance(decision, TransitionDecision)
    assert decision.previous_status is status
    assert decision.new_status is expected


def test_en_route_from_a_paused_job_does_not_rewind_the_pause() -> None:
    assert decide_transition(Status.PAUSED, Event.EN_ROUTE).new_status is Status.PAUSED


@pytest.mark.parametrize(
    ("status", "event"),
    (
        (Status.DRAFT, Event.START),
        (Status.SCHEDULED, Event.COMPLETE),
        (Status.IN_PROGRESS, Event.RESUME),
        (Status.COMPLETED, Event.START),
        (Status.CANCELED, Event.ACCEPT),
    ),
)
def test_invalid_or_terminal_transitions_fail_closed(
    status: Status, event: Event
) -> None:
    with pytest.raises(InvalidTransition):
        decide_transition(status, event)


def test_timestamp_effects_are_part_of_the_decision() -> None:
    assert decide_transition(Status.DISPATCHED, Event.START).starts_work
    assert decide_transition(Status.IN_PROGRESS, Event.PAUSE).stops_work
    assert decide_transition(Status.PAUSED, Event.RESUME).starts_work
    completed = decide_transition(Status.IN_PROGRESS, Event.COMPLETE)
    assert completed.stops_work
    assert completed.completes_work
