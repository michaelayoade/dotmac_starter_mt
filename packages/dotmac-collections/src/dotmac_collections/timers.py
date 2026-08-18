"""Narrow durable-timer port; scheduling mechanics remain separately owned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID, uuid4

from dotmac_kernel.cache import TenantScope

from dotmac_collections._validation import require_aware, require_text


@dataclass(frozen=True, slots=True)
class TimerIdentityV1:
    scope: TenantScope
    owner: str
    entity_kind: str
    entity_id: str
    purpose: str

    def __post_init__(self) -> None:
        for name in ("owner", "entity_kind", "entity_id", "purpose"):
            require_text(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class TimerRequestV1:
    identity: TimerIdentityV1
    due_at: datetime
    recorded_at: datetime
    output_event_type: str

    def __post_init__(self) -> None:
        require_aware("due_at", self.due_at)
        require_aware("recorded_at", self.recorded_at)
        require_text("output_event_type", self.output_event_type)


@dataclass(frozen=True, slots=True)
class TimerHandleV1:
    timer_id: UUID
    identity: TimerIdentityV1
    generation: int
    due_at: datetime
    output_event_type: str

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        require_aware("due_at", self.due_at)
        require_text("output_event_type", self.output_event_type)


@dataclass(frozen=True, slots=True)
class CancelTimerV1:
    identity: TimerIdentityV1
    observed_generation: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.observed_generation < 1:
            raise ValueError("observed_generation must be positive")
        require_aware("recorded_at", self.recorded_at)


@dataclass(frozen=True, slots=True)
class TimerTriggerV1:
    timer_id: UUID
    identity: TimerIdentityV1
    generation: int
    due_at: datetime
    output_event_type: str

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        require_aware("due_at", self.due_at)
        require_text("output_event_type", self.output_event_type)


@dataclass(frozen=True, slots=True)
class Canceled:
    observed_generation: int
    current_generation: int


@dataclass(frozen=True, slots=True)
class AlreadyFired:
    observed_generation: int
    current_generation: int


@dataclass(frozen=True, slots=True)
class NothingScheduled:
    observed_generation: int
    current_generation: int | None = None


@dataclass(frozen=True, slots=True)
class Stale:
    observed_generation: int
    current_generation: int


@dataclass(frozen=True, slots=True)
class Current:
    observed_generation: int
    current_generation: int
    replayed: bool = False


TimerDecision: TypeAlias = Canceled | AlreadyFired | NothingScheduled | Stale | Current


class CollectionsTimer(Protocol):
    def schedule(self, request: TimerRequestV1) -> TimerHandleV1: ...

    def cancel(self, request: CancelTimerV1) -> TimerDecision: ...

    def accept_trigger(
        self, trigger: TimerTriggerV1, *, accepted_at: datetime
    ) -> TimerDecision: ...

    def current(self, identity: TimerIdentityV1) -> TimerHandleV1 | None: ...


class FakeCollectionsTimer:
    """Deterministic state-machine fake with no scheduler, thread, or clock."""

    def __init__(self) -> None:
        self._latest: dict[TimerIdentityV1, TimerHandleV1] = {}
        self._states: dict[UUID, str] = {}

    def schedule(self, request: TimerRequestV1) -> TimerHandleV1:
        previous = self._latest.get(request.identity)
        generation = 1 if previous is None else previous.generation + 1
        if previous is not None:
            self._states[previous.timer_id] = "superseded"
        handle = TimerHandleV1(
            timer_id=uuid4(),
            identity=request.identity,
            generation=generation,
            due_at=request.due_at,
            output_event_type=request.output_event_type,
        )
        self._latest[request.identity] = handle
        self._states[handle.timer_id] = "scheduled"
        return handle

    def current(self, identity: TimerIdentityV1) -> TimerHandleV1 | None:
        handle = self._latest.get(identity)
        if handle is None or self._states[handle.timer_id] != "scheduled":
            return None
        return handle

    def cancel(self, request: CancelTimerV1) -> TimerDecision:
        current = self._latest.get(request.identity)
        if current is None:
            return NothingScheduled(request.observed_generation)
        if request.observed_generation != current.generation:
            return Stale(request.observed_generation, current.generation)
        state = self._states[current.timer_id]
        if state == "fired":
            return AlreadyFired(request.observed_generation, current.generation)
        if state == "superseded":
            return Stale(request.observed_generation, current.generation)
        self._states[current.timer_id] = "canceled"
        return Canceled(request.observed_generation, current.generation)

    def accept_trigger(
        self, trigger: TimerTriggerV1, *, accepted_at: datetime
    ) -> TimerDecision:
        require_aware("accepted_at", accepted_at)
        current = self._latest.get(trigger.identity)
        if current is None:
            return NothingScheduled(trigger.generation)
        if (
            trigger.generation != current.generation
            or trigger.timer_id != current.timer_id
            or trigger.due_at != current.due_at
            or trigger.output_event_type != current.output_event_type
        ):
            return Stale(trigger.generation, current.generation)
        state = self._states[current.timer_id]
        if state == "fired":
            return Current(trigger.generation, current.generation, replayed=True)
        if state == "canceled":
            return Canceled(trigger.generation, current.generation)
        self._states[current.timer_id] = "fired"
        return Current(trigger.generation, current.generation, replayed=False)


__all__ = [
    "AlreadyFired",
    "Canceled",
    "CancelTimerV1",
    "CollectionsTimer",
    "Current",
    "FakeCollectionsTimer",
    "NothingScheduled",
    "Stale",
    "TimerDecision",
    "TimerHandleV1",
    "TimerIdentityV1",
    "TimerRequestV1",
    "TimerTriggerV1",
]
