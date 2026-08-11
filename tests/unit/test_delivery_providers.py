"""The one send path, and the consent check that is structural rather than polite.

`dotmac_kernel.delivery_providers.send` exists so "ask consent first" cannot be
forgotten by a caller. Sub's history is what a convention gets you: marketing
eligibility lived inside the campaign segment filter, so the answer depended on
who was asking and an unsubscribed customer stayed reachable by every other path.

The fake provider here is the whole point of the seam — the kernel ships a
`Protocol` and no client, so a test needs no SMTP and a product brings its own.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from dotmac_kernel import consent
from dotmac_kernel.consent_models import REASON_BOUNCE, SCOPE_ALL
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_BOUNCED,
    DELIVERY_FAILED,
)
from dotmac_kernel.delivery_providers import (
    DeliveryProvider,
    OutboundMessage,
    ProviderResult,
    Sent,
    Suppressed,
    send,
)
from dotmac_kernel.models import Tenant


class FakeProvider:
    """Records what it was asked to send, and answers however the test wants."""

    name = "fake"

    def __init__(self, result: ProviderResult | None = None) -> None:
        self.result = result or ProviderResult()
        self.calls: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> ProviderResult:
        self.calls.append(message)
        return self.result


@pytest.fixture(autouse=True)
def _registries():
    previous_marketing, previous_numeric = consent._reset_registries_for_tests(
        marketing=("marketing",)
    )
    yield
    consent._reset_registries_for_tests(
        marketing=previous_marketing, numeric=previous_numeric
    )


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _message(**kwargs) -> OutboundMessage:
    defaults = {
        "dispatch_id": uuid4(),
        "channel": "email",
        "address": "jane@example.com",
        "body": "hello",
        "category": "billing",
    }
    return OutboundMessage(**{**defaults, **kwargs})


def test_the_seam_is_satisfied_by_a_plain_object() -> None:
    """No base class to inherit, no kernel import in the adapter's signature."""
    assert isinstance(FakeProvider(), DeliveryProvider)


# ── Consent is asked FIRST ──────────────────────────────────────────────────


def test_a_suppressed_address_never_reaches_the_provider(db, tenant) -> None:
    """Not merely "not sent" — not CALLED. A suppressed send must cost no
    network request and leave no receipt to reconcile."""
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    provider = FakeProvider()

    outcome = send(db, tenant.id, provider=provider, message=_message())

    assert isinstance(outcome, Suppressed)
    assert outcome.reason == REASON_BOUNCE
    assert outcome.sent is False
    assert provider.calls == []


def test_an_unsubscribe_stops_marketing_but_not_the_invoice(db, tenant) -> None:
    """The § 5c rule, proven end to end through the send path rather than only
    against the consent service."""
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    provider = FakeProvider()

    marketing = send(
        db, tenant.id, provider=provider, message=_message(category="marketing")
    )
    invoice = send(
        db, tenant.id, provider=provider, message=_message(category="billing")
    )

    assert isinstance(marketing, Suppressed)
    assert isinstance(invoice, Sent)
    assert [m.category for m in provider.calls] == ["billing"]


# ── The receipt, and the loop ───────────────────────────────────────────────


def test_a_successful_send_records_a_receipt(db, tenant) -> None:
    provider = FakeProvider(
        ProviderResult(status=DELIVERY_ACCEPTED, provider_message_id="msg-1")
    )
    outcome = send(db, tenant.id, provider=provider, message=_message())

    assert isinstance(outcome, Sent)
    assert outcome.sent is True
    assert outcome.status == DELIVERY_ACCEPTED
    assert outcome.receipt.provider == "fake"
    assert outcome.receipt.provider_message_id == "msg-1"
    assert outcome.receipt.dispatch_id == provider.calls[0].dispatch_id


def test_a_bounce_reported_at_send_time_suppresses_immediately(db, tenant) -> None:
    """A synchronous hard bounce closes the loop without waiting for a webhook."""
    provider = FakeProvider(
        ProviderResult(status=DELIVERY_BOUNCED, response_code="5.1.1")
    )
    send(db, tenant.id, provider=provider, message=_message())

    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_a_transient_failure_does_not_suppress(db, tenant) -> None:
    provider = FakeProvider(
        ProviderResult(status=DELIVERY_FAILED, response_code="4.2.2")
    )
    outcome = send(db, tenant.id, provider=provider, message=_message())

    assert isinstance(outcome, Sent)
    assert consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_the_provider_receives_the_message_unchanged(db, tenant) -> None:
    provider = FakeProvider()
    message = _message(subject="Your invoice", body="Hi {name}")
    send(db, tenant.id, provider=provider, message=message)
    assert provider.calls == [message]


def test_a_persisted_receipt_prevents_an_outbox_retry_from_calling_provider_again(
    db, tenant
) -> None:
    """The relay is at-least-once. If tenant delivery committed but dispatcher
    settlement crashed, replaying the outbox event must reuse its receipt before
    touching the network again."""
    provider = FakeProvider(
        ProviderResult(status=DELIVERY_ACCEPTED, provider_message_id="stable-1")
    )
    message = _message()
    tenant_id = tenant.id

    first = send(db, tenant_id, provider=provider, message=message)
    db.expunge_all()
    second = send(db, tenant_id, provider=provider, message=message)

    assert isinstance(first, Sent)
    assert isinstance(second, Sent)
    assert first.receipt.id == second.receipt.id
    assert provider.calls == [message]


def test_message_exposes_the_dispatch_id_as_the_provider_idempotency_key() -> None:
    message = _message()
    assert message.idempotency_key == str(message.dispatch_id)


def test_dispatch_id_reuse_with_different_message_is_a_conflict(db, tenant) -> None:
    provider = FakeProvider(
        ProviderResult(status=DELIVERY_ACCEPTED, provider_message_id="stable-2")
    )
    original = _message(body="invoice one")
    send(db, tenant.id, provider=provider, message=original)

    with pytest.raises(ValueError, match="dispatch id"):
        send(
            db,
            tenant.id,
            provider=provider,
            message=_message(
                dispatch_id=original.dispatch_id, body="different invoice"
            ),
        )
    assert provider.calls == [original]


# ── The adapter's vocabulary is checked at construction ─────────────────────


def test_category_is_a_required_constructor_argument() -> None:
    parameter = inspect.signature(OutboundMessage).parameters["category"]
    assert parameter.default is inspect.Parameter.empty


def test_an_empty_category_is_refused() -> None:
    with pytest.raises(ValueError, match="category"):
        _message(category="  ")


def test_an_unknown_provider_status_is_refused_at_construction(db) -> None:
    """An adapter typo fails where it is written, not two layers down in the
    receipt writer."""
    with pytest.raises(ValueError, match="unknown delivery status"):
        ProviderResult(status="probably-sent")
