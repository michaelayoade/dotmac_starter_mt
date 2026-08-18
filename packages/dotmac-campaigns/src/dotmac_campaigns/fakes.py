"""Deterministic fakes and executable conformance checks for assembly ports."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_campaigns.contracts import (
    DueWorkTrigger,
    RenderedMessage,
    Renderer,
    RenderRequest,
    ScheduledTimer,
    SenderRequest,
    SenderResolver,
    SenderSnapshot,
    TimerAcceptance,
    TimerCancellation,
    TimerIdentity,
    TimerOutput,
    TimerPort,
    fingerprint,
)

_TEST_TENANT: Final[UUID] = UUID("00000000-0000-0000-0000-000000000001")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeRenderer(Renderer):
    def render(self, request: RenderRequest) -> RenderedMessage:
        body = f"Rendered {request.template_slug}"
        for key, value in sorted(request.context.items()):
            body += f" {key}={value}"
        subject = (
            f"Subject: {request.template_slug}" if request.channel == "email" else None
        )
        revision = f"{request.template_slug}:published:1"
        return RenderedMessage(
            template_revision=revision,
            subject=subject,
            body=body,
            fingerprint_sha256=fingerprint(
                {"revision": revision, "subject": subject, "body": body}
            ),
        )


class FakeSenderResolver(SenderResolver):
    def resolve(self, request: SenderRequest) -> SenderSnapshot:
        address = f"{request.sender_key}@example.test"
        display_name = request.sender_key.replace("_", " ").title()
        return SenderSnapshot(
            sender_key=request.sender_key,
            address=address,
            display_name=display_name,
            fingerprint_sha256=fingerprint(
                {
                    "channel": request.channel,
                    "sender_key": request.sender_key,
                    "address": address,
                    "display_name": display_name,
                }
            ),
        )


class FakeTimerPort(TimerPort):
    """In-memory generation semantics matching Durable Timers' public contract."""

    def __init__(self) -> None:
        self._generations: defaultdict[tuple[UUID, TimerIdentity], int] = defaultdict(
            int
        )
        self._current: dict[tuple[UUID, TimerIdentity], ScheduledTimer] = {}
        self._cancelled: set[tuple[UUID, UUID, int]] = set()
        self._accepted: set[tuple[UUID, UUID, int]] = set()

    def schedule(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        identity: TimerIdentity,
        due_at: datetime,
        output: TimerOutput,
        recorded_at: datetime,
        expires_at: datetime | None,
    ) -> ScheduledTimer:
        del db, recorded_at, expires_at
        key = (tenant_id, identity)
        self._generations[key] += 1
        timer = ScheduledTimer(
            timer_id=uuid.uuid4(),
            identity=identity,
            generation=self._generations[key],
            due_at=due_at,
            output=output,
        )
        self._current[key] = timer
        return timer

    def cancel(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        identity: TimerIdentity,
        recorded_at: datetime,
    ) -> TimerCancellation:
        del db, recorded_at
        key = (tenant_id, identity)
        current = self._current.pop(key, None)
        if current is None:
            return TimerCancellation(False, "nothing_scheduled")
        self._cancelled.add((tenant_id, current.timer_id, current.generation))
        return TimerCancellation(True, "cancelled")

    def accept(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        trigger: DueWorkTrigger,
        accepted_at: datetime | None = None,
    ) -> TimerAcceptance:
        del db, accepted_at
        key = (tenant_id, trigger.identity)
        current = self._current.get(key)
        acceptance = (tenant_id, trigger.timer_id, trigger.generation)
        if acceptance in self._accepted:
            return TimerAcceptance(True, replayed=True)
        if acceptance in self._cancelled:
            return TimerAcceptance(False, reason="cancelled")
        if (
            current is None
            or current.timer_id != trigger.timer_id
            or current.generation != trigger.generation
        ):
            return TimerAcceptance(False, reason="stale")
        self._accepted.add(acceptance)
        self._current.pop(key, None)
        return TimerAcceptance(True)

    def current_triggers(self) -> list[DueWorkTrigger]:
        return [timer.trigger() for timer in self._current.values()]

    def only_current_trigger(self) -> DueWorkTrigger:
        triggers = self.current_triggers()
        if len(triggers) != 1:
            raise AssertionError(f"expected one current trigger, found {len(triggers)}")
        return triggers[0]


def assert_renderer_conformance(renderer: Renderer) -> None:
    request = RenderRequest(
        tenant_id=_TEST_TENANT,
        template_slug="conformance",
        channel="email",
        context={"name": "Ada"},
    )
    first = renderer.render(request)
    second = renderer.render(request)
    _check(first == second, "the same exact revision/context must render identically")
    _check(bool(first.template_revision), "renderer did not return a revision")
    _check(len(first.fingerprint_sha256) == 64, "renderer digest is not SHA-256")
    _check(bool(first.body), "renderer returned an empty body")


def assert_sender_resolver_conformance(resolver: SenderResolver) -> None:
    request = SenderRequest(
        tenant_id=_TEST_TENANT, channel="email", sender_key="conformance"
    )
    first = resolver.resolve(request)
    second = resolver.resolve(request)
    _check(first == second, "sender selection must be deterministic for a snapshot")
    _check(first.sender_key == request.sender_key, "sender key changed")
    _check(bool(first.address), "sender address is empty")
    _check(len(first.fingerprint_sha256) == 64, "sender digest is not SHA-256")
    _check(not hasattr(first, "password"), "sender snapshot leaked a password")
    _check(not hasattr(first, "token"), "sender snapshot leaked a token")


def assert_timer_port_conformance(timers: TimerPort) -> None:
    identity = TimerIdentity(
        owner="campaigns",
        entity_kind="recipient_step",
        entity_id="conformance",
        purpose="delivery_due:0",
    )
    due_at = datetime(2026, 8, 18, tzinfo=UTC)
    first = timers.schedule(
        None,
        tenant_id=_TEST_TENANT,
        identity=identity,
        due_at=due_at,
        output=TimerOutput("campaigns.recipient_step_due.v1"),
        recorded_at=due_at,
        expires_at=None,
    )
    second = timers.schedule(
        None,
        tenant_id=_TEST_TENANT,
        identity=identity,
        due_at=due_at,
        output=TimerOutput("campaigns.recipient_step_due.v1"),
        recorded_at=due_at,
        expires_at=None,
    )
    _check(
        second.generation == first.generation + 1,
        "timer generation did not advance",
    )
    stale = timers.accept(
        None, tenant_id=_TEST_TENANT, trigger=first.trigger(), accepted_at=due_at
    )
    _check(not stale.current, "superseded timer was accepted")
    current = timers.accept(
        None, tenant_id=_TEST_TENANT, trigger=second.trigger(), accepted_at=due_at
    )
    _check(current.current, "current timer was refused")
    replay = timers.accept(
        None, tenant_id=_TEST_TENANT, trigger=second.trigger(), accepted_at=due_at
    )
    _check(replay.replayed, "timer replay was not identified")


__all__ = [
    "FakeRenderer",
    "FakeSenderResolver",
    "FakeTimerPort",
    "assert_renderer_conformance",
    "assert_sender_resolver_conformance",
    "assert_timer_port_conformance",
]
