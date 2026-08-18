"""Port conformance and provider/product independence canaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_campaigns.contracts import (
    RenderRequest,
    SenderRequest,
    TimerIdentity,
    TimerOutput,
)
from dotmac_campaigns.fakes import (
    FakeRenderer,
    FakeSenderResolver,
    FakeTimerPort,
    assert_renderer_conformance,
    assert_sender_resolver_conformance,
    assert_timer_port_conformance,
)


def test_shipped_fakes_exercise_the_published_port_contracts() -> None:
    assert_renderer_conformance(FakeRenderer())
    assert_sender_resolver_conformance(FakeSenderResolver())
    assert_timer_port_conformance(FakeTimerPort())


def test_rendering_returns_an_exact_revision_and_fingerprint() -> None:
    rendered = FakeRenderer().render(
        RenderRequest(
            tenant_id=uuid.uuid4(),
            template_slug="welcome",
            channel="email",
            context={"first_name": "Ada"},
        )
    )
    assert rendered.template_revision
    assert len(rendered.fingerprint_sha256) == 64
    assert "Ada" in rendered.body


def test_sender_resolution_returns_no_provider_credentials() -> None:
    sender = FakeSenderResolver().resolve(
        SenderRequest(
            tenant_id=uuid.uuid4(), channel="email", sender_key="growth"
        )
    )
    assert sender.sender_key == "growth"
    assert sender.address
    assert not hasattr(sender, "password")
    assert not hasattr(sender, "token")


def test_timer_generations_supersede_and_cancel_stale_work() -> None:
    timers = FakeTimerPort()
    tenant = uuid.uuid4()
    identity = TimerIdentity(
        owner="campaigns",
        entity_kind="recipient_step",
        entity_id=str(uuid.uuid4()),
        purpose="delivery_due",
    )
    first = timers.schedule(
        None,
        tenant_id=tenant,
        identity=identity,
        due_at=datetime(2026, 8, 18, tzinfo=UTC),
        output=TimerOutput("campaigns.recipient_step_due.v1"),
        recorded_at=datetime(2026, 8, 17, tzinfo=UTC),
        expires_at=datetime(2027, 8, 18, tzinfo=UTC),
    )
    second = timers.schedule(
        None,
        tenant_id=tenant,
        identity=identity,
        due_at=datetime(2026, 8, 19, tzinfo=UTC),
        output=TimerOutput("campaigns.recipient_step_due.v1"),
        recorded_at=datetime(2026, 8, 17, tzinfo=UTC) + timedelta(seconds=1),
        expires_at=datetime(2027, 8, 18, tzinfo=UTC),
    )
    assert first.generation == 1
    assert second.generation == 2
    assert timers.accept(None, tenant_id=tenant, trigger=first.trigger()).current is False
    assert timers.cancel(
        None,
        tenant_id=tenant,
        identity=identity,
        recorded_at=datetime(2026, 8, 17, tzinfo=UTC) + timedelta(seconds=2),
    ).cancelled
    assert timers.accept(None, tenant_id=tenant, trigger=second.trigger()).current is False


def test_conformance_refuses_a_renderer_without_stable_fingerprint() -> None:
    class BrokenRenderer:
        def render(self, request: RenderRequest):  # type: ignore[no-untyped-def]
            result = FakeRenderer().render(request)
            return result.__class__(
                template_revision=result.template_revision,
                subject=result.subject,
                body=result.body,
                fingerprint_sha256="",
            )

    with pytest.raises(AssertionError):
        assert_renderer_conformance(BrokenRenderer())  # type: ignore[arg-type]
