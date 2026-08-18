"""RED-first contract for Collections' narrow durable-timer port and fake."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from dotmac_collections.timers import (
    AlreadyFired,
    Canceled,
    CancelTimerV1,
    Current,
    FakeCollectionsTimer,
    NothingScheduled,
    Stale,
    TimerHandleV1,
    TimerIdentityV1,
    TimerRequestV1,
    TimerTriggerV1,
)
from dotmac_kernel.cache import TenantScope

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
AT = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
SCOPE = TenantScope(TENANT_ID)


def _identity(*, purpose: str = "next_action") -> TimerIdentityV1:
    return TimerIdentityV1(
        scope=SCOPE,
        owner="collections.case",
        entity_kind="collection_case",
        entity_id="case:40000000-0000-0000-0000-000000000001",
        purpose=purpose,
    )


def _request(
    *,
    identity: TimerIdentityV1 | None = None,
    due_at: datetime = AT + timedelta(days=1),
    recorded_at: datetime = AT,
) -> TimerRequestV1:
    return TimerRequestV1(
        identity=identity or _identity(),
        due_at=due_at,
        recorded_at=recorded_at,
        output_event_type="collections.case_action_due.v1",
    )


def _trigger(handle: TimerHandleV1) -> TimerTriggerV1:
    return TimerTriggerV1(
        timer_id=handle.timer_id,
        identity=handle.identity,
        generation=handle.generation,
        due_at=handle.due_at,
        output_event_type=handle.output_event_type,
    )


def test_timer_values_are_frozen_closed_and_carry_explicit_scope() -> None:
    assert tuple(field.name for field in fields(TimerIdentityV1)) == (
        "scope",
        "owner",
        "entity_kind",
        "entity_id",
        "purpose",
    )
    assert tuple(field.name for field in fields(TimerRequestV1)) == (
        "identity",
        "due_at",
        "recorded_at",
        "output_event_type",
    )
    request = _request()
    assert request.identity.scope == SCOPE
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.output_event_type = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        TimerRequestV1(
            identity=_identity(),
            due_at=AT,
            recorded_at=AT,
            output_event_type="collections.case_action_due.v1",
            expected_source_version=7,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("due_at", AT.replace(tzinfo=None)),
        ("recorded_at", AT.replace(tzinfo=None)),
    ],
)
def test_timer_request_rejects_naive_instants(
    field_name: str,
    value: datetime,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(**{field_name: value})  # type: ignore[arg-type]


def test_fake_schedule_supersedes_and_stale_trigger_names_both_generations() -> None:
    timer = FakeCollectionsTimer()
    first = timer.schedule(_request())
    replacement = timer.schedule(
        _request(
            due_at=AT + timedelta(days=2),
            recorded_at=AT + timedelta(hours=1),
        )
    )

    assert first.generation == 1
    assert replacement.generation == 2
    assert replacement.timer_id != first.timer_id
    assert timer.current(_identity()) == replacement
    assert timer.accept_trigger(
        _trigger(first), accepted_at=AT + timedelta(days=1)
    ) == Stale(
        observed_generation=1,
        current_generation=2,
    )

    accepted = timer.accept_trigger(
        _trigger(replacement),
        accepted_at=AT + timedelta(days=2),
    )
    assert accepted == Current(
        observed_generation=2,
        current_generation=2,
        replayed=False,
    )
    assert timer.accept_trigger(
        _trigger(replacement),
        accepted_at=AT + timedelta(days=2, minutes=1),
    ) == Current(
        observed_generation=2,
        current_generation=2,
        replayed=True,
    )


def test_cancel_is_exact_and_has_four_distinct_typed_outcomes() -> None:
    timer = FakeCollectionsTimer()
    primary = timer.schedule(_request())
    sibling_identity = _identity(purpose="grace_expiry")
    sibling = timer.schedule(_request(identity=sibling_identity))

    stale = timer.cancel(
        CancelTimerV1(
            identity=primary.identity,
            observed_generation=primary.generation + 1,
            recorded_at=AT + timedelta(minutes=1),
        )
    )
    assert stale == Stale(
        observed_generation=2,
        current_generation=1,
    )
    canceled = timer.cancel(
        CancelTimerV1(
            identity=primary.identity,
            observed_generation=primary.generation,
            recorded_at=AT + timedelta(minutes=2),
        )
    )
    assert canceled == Canceled(observed_generation=1, current_generation=1)
    assert timer.current(sibling_identity) == sibling

    missing = timer.cancel(
        CancelTimerV1(
            identity=_identity(purpose="arrangement_due"),
            observed_generation=4,
            recorded_at=AT + timedelta(minutes=3),
        )
    )
    assert missing == NothingScheduled(observed_generation=4)

    fired_timer = FakeCollectionsTimer()
    fired = fired_timer.schedule(_request())
    fired_timer.accept_trigger(_trigger(fired), accepted_at=fired.due_at)
    assert fired_timer.cancel(
        CancelTimerV1(
            identity=fired.identity,
            observed_generation=fired.generation,
            recorded_at=fired.due_at + timedelta(minutes=1),
        )
    ) == AlreadyFired(observed_generation=1, current_generation=1)

    assert len({type(stale), type(canceled), type(missing), AlreadyFired}) == 4


def test_fake_acceptance_requires_a_caller_supplied_aware_instant() -> None:
    timer = FakeCollectionsTimer()
    handle = timer.schedule(_request())
    with pytest.raises(ValueError, match="timezone-aware"):
        timer.accept_trigger(
            _trigger(handle),
            accepted_at=handle.due_at.replace(tzinfo=None),
        )
