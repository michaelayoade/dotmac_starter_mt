"""Delivery receipts and the bounce→consent loop (ADR-0006 § 5c).

These are NOT ported tests. The loop they cover exists in neither product:
verified in `dotmac_sub` at `5d6f115b7`, `DeliveryStatus.bounced` is declared and
never assigned, `SuppressionReason.bounce`/`.complaint` have zero call sites, and
the campaign unsubscribe link is the only writer of a suppression anywhere. So
this behaviour is new code and owes its own proof rather than inheriting one.

The receipt SHAPE is ported (`dotmac_sub:NotificationDelivery`), minus the queue
around it — that is `dotmac_kernel.messaging`, built twice in Sub. See
`docs/inventories/delivery-outbox-sources.md`.

SQLite, no RLS: tenancy enforcement is proven in `tests/test_delivery_isolation.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import consent, delivery
from dotmac_kernel.consent_models import REASON_BOUNCE, REASON_COMPLAINT, SCOPE_ALL
from dotmac_kernel.delivery import DeliveryError
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_BOUNCED,
    DELIVERY_COMPLAINT,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_REJECTED,
)
from dotmac_kernel.models import Tenant


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


def _receipt(db, tenant, **kwargs):
    defaults = {
        "channel": "email",
        "address": "jane@example.com",
        "provider": "smtp",
        "status": DELIVERY_ACCEPTED,
    }
    return delivery.record_receipt(db, tenant.id, **{**defaults, **kwargs})


# ── The loop ────────────────────────────────────────────────────────────────


def test_a_bounce_suppresses_the_address_for_everything(db, tenant) -> None:
    """THE test. Without this the consent ledger has no automated writer and
    answers "yes, send" forever — which is Sub's actual situation today."""
    _receipt(db, tenant, status=DELIVERY_BOUNCED, response_code="5.1.1")

    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )
    assert (
        consent.suppression_reason(
            db,
            tenant.id,
            channel="email",
            address="jane@example.com",
            category="billing",
        )
        == REASON_BOUNCE
    )


def test_a_complaint_suppresses_with_its_own_reason(db, tenant) -> None:
    _receipt(db, tenant, status=DELIVERY_COMPLAINT)
    assert (
        consent.suppression_reason(
            db,
            tenant.id,
            channel="email",
            address="jane@example.com",
            category="billing",
        )
        == REASON_COMPLAINT
    )


def test_the_suppression_records_where_it_came_from(db, tenant) -> None:
    """An operator answering "why did this customer stop getting mail?" must be
    able to see it was the provider, not a person."""
    _receipt(db, tenant, status=DELIVERY_BOUNCED, provider="ses", response_code="5.1.1")
    row = consent.list_suppressions(db, tenant.id)[0]
    assert row.scope == SCOPE_ALL
    assert row.created_by == "delivery:ses"
    assert "ses" in (row.note or "") and "5.1.1" in (row.note or "")


@pytest.mark.parametrize(
    "status",
    [DELIVERY_ACCEPTED, DELIVERY_DELIVERED, DELIVERY_FAILED, DELIVERY_REJECTED],
)
def test_a_non_final_verdict_does_not_suppress(db, tenant, status) -> None:
    """The trap this guards: a SOFT bounce — mailbox full, greylisted — must be
    recorded as `failed`, never `bounced`, or a full inbox permanently stops that
    customer's invoices."""
    _receipt(db, tenant, status=status)
    assert consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_a_bounce_escalates_an_existing_unsubscribe(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    _receipt(db, tenant, status=DELIVERY_BOUNCED)
    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


# ── Idempotency ─────────────────────────────────────────────────────────────


def test_a_redelivered_webhook_does_not_record_twice(db, tenant) -> None:
    """Provider callbacks are at-least-once."""
    first = _receipt(db, tenant, status=DELIVERY_BOUNCED, provider_message_id="msg-1")
    second = _receipt(db, tenant, status=DELIVERY_BOUNCED, provider_message_id="msg-1")
    assert first.id == second.id
    assert (
        len(
            delivery.receipts_for_address(
                db, tenant.id, channel="email", address="jane@example.com"
            )
        )
        == 1
    )


def test_one_provider_message_keeps_each_status_transition(db, tenant) -> None:
    """A provider message id identifies the MESSAGE, not one receipt event.

    The normal lifecycle is accepted -> delivered/bounced. Deduplicating solely
    on the message id erases the late verdict and, for a bounce, leaves consent
    open forever.
    """
    accepted = _receipt(
        db, tenant, status=DELIVERY_ACCEPTED, provider_message_id="progress-1"
    )
    bounced = _receipt(
        db, tenant, status=DELIVERY_BOUNCED, provider_message_id="progress-1"
    )

    assert accepted.id != bounced.id
    assert accepted.dispatch_id == bounced.dispatch_id
    assert [
        row.status
        for row in delivery.receipts_for_address(
            db, tenant.id, channel="email", address="jane@example.com"
        )
    ] == [DELIVERY_BOUNCED, DELIVERY_ACCEPTED]
    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_receipts_without_a_provider_id_are_all_recorded(db, tenant) -> None:
    """A synchronous failure never gets an id, and there is nothing to dedupe on
    — the unique index is partial for exactly this reason."""
    _receipt(db, tenant, status=DELIVERY_FAILED)
    _receipt(db, tenant, status=DELIVERY_FAILED)
    assert (
        len(
            delivery.receipts_for_address(
                db, tenant.id, channel="email", address="jane@example.com"
            )
        )
        == 2
    )


def test_the_same_provider_id_is_distinct_across_providers(db, tenant) -> None:
    _receipt(db, tenant, provider="ses", provider_message_id="msg-1")
    _receipt(db, tenant, provider="twilio", provider_message_id="msg-1")
    assert (
        len(
            delivery.receipts_for_address(
                db, tenant.id, channel="email", address="jane@example.com"
            )
        )
        == 2
    )


# ── Normalisation and edges ─────────────────────────────────────────────────


def test_the_address_is_normalised_the_same_way_consent_normalises_it(
    db, tenant
) -> None:
    """Otherwise a bounce for `Jane@Example.com` would not suppress
    `jane@example.com`, and the loop would silently not close."""
    _receipt(db, tenant, address="Jane@Example.COM", status=DELIVERY_BOUNCED)
    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_an_unknown_status_is_refused(db, tenant) -> None:
    with pytest.raises(DeliveryError, match="unknown delivery status"):
        _receipt(db, tenant, status="probably-fine")


def test_an_empty_address_is_refused(db, tenant) -> None:
    with pytest.raises(DeliveryError, match="empty address"):
        _receipt(db, tenant, address="  ")


def test_receipts_for_an_address_come_back_newest_first(db, tenant) -> None:
    from datetime import UTC, datetime

    _receipt(
        db,
        tenant,
        status=DELIVERY_ACCEPTED,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    _receipt(
        db,
        tenant,
        status=DELIVERY_DELIVERED,
        occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    rows = delivery.receipts_for_address(
        db, tenant.id, channel="email", address="jane@example.com"
    )
    assert [r.status for r in rows] == [DELIVERY_DELIVERED, DELIVERY_ACCEPTED]


def test_one_tenants_bounce_does_not_suppress_another(db, tenant) -> None:
    other = Tenant(name="Other", slug=f"other-{uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    _receipt(db, tenant, status=DELIVERY_BOUNCED)
    assert consent.may_send(
        db, other.id, channel="email", address="jane@example.com", category="billing"
    )
