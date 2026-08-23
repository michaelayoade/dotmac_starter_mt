"""Narrow durable-timer port; scheduling mechanics remain separately owned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope
from sqlalchemy.orm import Session

from dotmac_collections._validation import require_aware, require_text

STEP_DUE_EVENT_TYPE = "collections.case.step_due.v1"


@dataclass(frozen=True, slots=True)
class TimerIdentityV1:
    scope: Scope
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
    expected_source_version: int | None = None

    def __post_init__(self) -> None:
        require_aware("due_at", self.due_at)
        require_aware("recorded_at", self.recorded_at)
        require_text("output_event_type", self.output_event_type)
        if (
            self.expected_source_version is not None
            and self.expected_source_version < 1
        ):
            raise ValueError("expected_source_version must be positive")


@dataclass(frozen=True, slots=True)
class TimerHandleV1:
    timer_id: UUID
    identity: TimerIdentityV1
    generation: int
    due_at: datetime
    output_event_type: str
    expected_source_version: int | None = None

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        require_aware("due_at", self.due_at)
        require_text("output_event_type", self.output_event_type)
        if (
            self.expected_source_version is not None
            and self.expected_source_version < 1
        ):
            raise ValueError("expected_source_version must be positive")


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
    expected_source_version: int | None = None

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be positive")
        require_aware("due_at", self.due_at)
        require_text("output_event_type", self.output_event_type)
        if (
            self.expected_source_version is not None
            and self.expected_source_version < 1
        ):
            raise ValueError("expected_source_version must be positive")


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


TimerDecision = Canceled | AlreadyFired | NothingScheduled | Stale | Current


class CollectionsTimer(Protocol):
    def schedule(self, db: Session, request: TimerRequestV1) -> TimerHandleV1: ...

    def cancel(self, db: Session, request: CancelTimerV1) -> TimerDecision: ...

    def accept_trigger(
        self, db: Session, trigger: TimerTriggerV1, *, accepted_at: datetime
    ) -> TimerDecision: ...

    def current(
        self, db: Session, identity: TimerIdentityV1
    ) -> TimerHandleV1 | None: ...


class FakeCollectionsTimer:
    """Deterministic state-machine fake with no scheduler, thread, or clock."""

    def __init__(self) -> None:
        self._latest: dict[TimerIdentityV1, TimerHandleV1] = {}
        self._states: dict[UUID, str] = {}

    @property
    def identities(self) -> tuple[TimerIdentityV1, ...]:
        return tuple(self._latest)

    def schedule(self, db: Session, request: TimerRequestV1) -> TimerHandleV1:
        del db
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
            expected_source_version=request.expected_source_version,
        )
        self._latest[request.identity] = handle
        self._states[handle.timer_id] = "scheduled"
        return handle

    def current(self, db: Session, identity: TimerIdentityV1) -> TimerHandleV1 | None:
        del db
        handle = self._latest.get(identity)
        if handle is None or self._states[handle.timer_id] != "scheduled":
            return None
        return handle

    def cancel(self, db: Session, request: CancelTimerV1) -> TimerDecision:
        del db
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
        self, db: Session, trigger: TimerTriggerV1, *, accepted_at: datetime
    ) -> TimerDecision:
        del db
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


def assert_collections_timer_conformance(
    timer: CollectionsTimer,
    db: Session,
    *,
    scope: Scope,
) -> None:
    """Executable contract every assembly timer adapter must pass."""

    identity = TimerIdentityV1(
        scope=scope,
        owner="collections.case",
        entity_kind="collection_case",
        entity_id=f"conformance:{uuid4()}",
        purpose="next_step",
    )
    recorded_at = datetime(2026, 8, 23, tzinfo=UTC)
    due_at = recorded_at
    first = timer.schedule(
        db,
        TimerRequestV1(
            identity=identity,
            due_at=due_at,
            recorded_at=recorded_at,
            output_event_type=STEP_DUE_EVENT_TYPE,
            expected_source_version=1,
        ),
    )
    second = timer.schedule(
        db,
        TimerRequestV1(
            identity=identity,
            due_at=due_at,
            recorded_at=recorded_at,
            output_event_type=STEP_DUE_EVENT_TYPE,
            expected_source_version=2,
        ),
    )
    if second.generation != first.generation + 1:
        raise AssertionError("timer replacement did not advance generation")
    if second.expected_source_version != 2:
        raise AssertionError("timer dropped expected source-version evidence")
    first_trigger = TimerTriggerV1(
        first.timer_id,
        first.identity,
        first.generation,
        first.due_at,
        first.output_event_type,
        first.expected_source_version,
    )
    if not isinstance(
        timer.accept_trigger(db, first_trigger, accepted_at=due_at), Stale
    ):
        raise AssertionError("superseded timer generation was accepted")
    second_trigger = TimerTriggerV1(
        second.timer_id,
        second.identity,
        second.generation,
        second.due_at,
        second.output_event_type,
        second.expected_source_version,
    )
    accepted = timer.accept_trigger(db, second_trigger, accepted_at=due_at)
    if not isinstance(accepted, Current) or accepted.replayed:
        raise AssertionError("current timer generation was not accepted once")
    replay = timer.accept_trigger(db, second_trigger, accepted_at=due_at)
    if not isinstance(replay, Current) or not replay.replayed:
        raise AssertionError("accepted timer replay was not identified")
    cancel_identity = TimerIdentityV1(
        scope=scope,
        owner=identity.owner,
        entity_kind=identity.entity_kind,
        entity_id=f"conformance-cancel:{uuid4()}",
        purpose=identity.purpose,
    )
    sibling_identity = TimerIdentityV1(
        scope=scope,
        owner=cancel_identity.owner,
        entity_kind=cancel_identity.entity_kind,
        entity_id=cancel_identity.entity_id,
        purpose="sibling",
    )
    cancel_handle = timer.schedule(
        db,
        TimerRequestV1(
            cancel_identity,
            due_at,
            recorded_at,
            STEP_DUE_EVENT_TYPE,
            1,
        ),
    )
    sibling = timer.schedule(
        db,
        TimerRequestV1(
            sibling_identity,
            due_at,
            recorded_at,
            STEP_DUE_EVENT_TYPE,
            1,
        ),
    )
    canceled = timer.cancel(
        db,
        CancelTimerV1(cancel_identity, cancel_handle.generation, recorded_at),
    )
    if not isinstance(canceled, Canceled):
        raise AssertionError("exact current timer cancellation was refused")
    if timer.current(db, cancel_identity) is not None:
        raise AssertionError("canceled timer remained current")
    if timer.current(db, sibling_identity) != sibling:
        raise AssertionError("exact cancellation changed a sibling timer")


__all__ = [
    "AlreadyFired",
    "Canceled",
    "CancelTimerV1",
    "CollectionsTimer",
    "Current",
    "FakeCollectionsTimer",
    "NothingScheduled",
    "Stale",
    "STEP_DUE_EVENT_TYPE",
    "TimerDecision",
    "TimerHandleV1",
    "TimerIdentityV1",
    "TimerRequestV1",
    "TimerTriggerV1",
    "assert_collections_timer_conformance",
]
