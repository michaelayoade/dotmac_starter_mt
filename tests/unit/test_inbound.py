"""The inbound seam: connected accounts, and at-most-once admission.

The half of the communications stack that did not exist before 2026-08-12 —
`docs/inventories/inbox-sources.md` § "Prerequisites" measured the gap. The tests
that matter most are the delegation ones: `admit` must not become a fourth
idempotency implementation, and a replay must not write a second fact.

Tenancy note: these run on SQLite with no RLS. `tenant_id` is passed explicitly
and the queries filter on it, which is what is under test — not the database's
enforcement of it, which is proven against real Postgres in `tests/`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel import channels, inbound
from dotmac_kernel.channels import (
    AddressForm,
    ChannelSpec,
    MessageIdScope,
    ThreadIdentity,
    Transport,
    UnknownChannelError,
)
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.inbound import InboundError, Observation
from dotmac_kernel.inbound_models import (
    STATUS_PROCESSED,
    STATUS_RECORDED,
    STATUS_REJECTED,
    InboundObservation,
)
from dotmac_kernel.models import Tenant

OBSERVED = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _channels():
    channels.reset_registry_for_tests()
    channels.register_channels(
        [
            ChannelSpec(
                code="email",
                owner="test_product",
                address_form=AddressForm.EMAIL,
                transport=Transport.EXTERNAL,
                thread_identity=ThreadIdentity.PROVIDER,
                message_id_scope=MessageIdScope.GLOBAL,
            )
        ]
    )
    yield
    channels.reset_registry_for_tests()


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _observation(**overrides: object) -> Observation:
    base: dict = {
        "provider": "smtp",
        "account_scope": "support@example.net",
        "provider_event_id": "<msg-1@mail>",
        "channel": "email",
        "payload": {"subject": "Hello", "body": "Is anyone there?"},
        "observed_at": OBSERVED,
    }
    base.update(overrides)
    return Observation(**base)  # type: ignore[arg-type]


# ── Connected accounts ──────────────────────────────────────────────────────


def test_registering_an_account_makes_its_scope_resolvable(db, tenant) -> None:
    """`account_scope` is where every thread key downstream comes from, so this
    registry is what makes a conversation module adoptable at all."""
    inbound.register_connected_account(
        db,
        tenant.id,
        channel="email",
        provider="smtp",
        account_scope="support@example.net",
        display_name="Support",
    )
    found = inbound.connected_account(
        db, tenant.id, provider="smtp", account_scope="support@example.net"
    )
    assert found is not None
    assert found.display_name == "Support"
    assert found.is_active is True


def test_an_undeclared_channel_is_refused_on_an_account(db, tenant) -> None:
    """The opposite call to an observation's: a misspelled channel here is a
    misconfiguration that would silently receive nothing, caught while an
    operator can still fix it."""
    with pytest.raises(UnknownChannelError):
        inbound.register_connected_account(
            db, tenant.id, channel="watsapp", provider="meta", account_scope="page-1"
        )


def test_re_registering_reactivates_rather_than_failing(db, tenant) -> None:
    """ "Connect this mailbox again" is an operator action with an obvious
    meaning, and it must not be a unique-constraint error."""
    inbound.register_connected_account(
        db, tenant.id, channel="email", provider="smtp", account_scope="a@x.example"
    )
    assert inbound.deactivate_account(
        db, tenant.id, provider="smtp", account_scope="a@x.example"
    )
    again = inbound.register_connected_account(
        db,
        tenant.id,
        channel="email",
        provider="smtp",
        account_scope="a@x.example",
        display_name="Renamed",
    )
    assert again.is_active is True
    assert again.display_name == "Renamed"
    assert len(inbound.list_connected_accounts(db, tenant.id)) == 1


def test_deactivating_keeps_the_row(db, tenant) -> None:
    """Deleting would strand every conversation that arrived through it."""
    inbound.register_connected_account(
        db, tenant.id, channel="email", provider="smtp", account_scope="a@x.example"
    )
    inbound.deactivate_account(
        db, tenant.id, provider="smtp", account_scope="a@x.example"
    )
    assert inbound.list_connected_accounts(db, tenant.id) == ()
    assert (
        inbound.list_connected_accounts(db, tenant.id, active_only=False)[0].is_active
        is False
    )


def test_two_tenants_may_connect_the_same_address(db) -> None:
    """The identity is per tenant. A shared uniqueness would let one tenant's
    mailbox block another's."""
    first = Tenant(name="A", slug=f"a-{uuid4().hex[:8]}")
    second = Tenant(name="B", slug=f"b-{uuid4().hex[:8]}")
    db.add_all([first, second])
    db.flush()
    for tenant in (first, second):
        inbound.register_connected_account(
            db,
            tenant.id,
            channel="email",
            provider="smtp",
            account_scope="support@shared.example",
        )
    assert len(inbound.list_connected_accounts(db, first.id)) == 1
    assert len(inbound.list_connected_accounts(db, second.id)) == 1


def test_an_account_never_stores_a_secret_value(db, tenant) -> None:
    """ADR-0009: the column holds a NAME resolved through `secret_sources`. A
    token here would make a database backup a credential leak."""
    account = inbound.register_connected_account(
        db,
        tenant.id,
        channel="email",
        provider="smtp",
        account_scope="a@x.example",
        credential_name="smtp/support",
    )
    columns = set(account.__table__.c.keys())
    assert "credential_name" in columns
    assert not {"password", "secret", "token", "api_key"} & columns


# ── Admission ───────────────────────────────────────────────────────────────


def test_admitting_records_the_provider_fact(db, tenant) -> None:
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    assert outcome.replayed is False
    row = db.get(InboundObservation, outcome.observation_id)
    assert row.processing_status == STATUS_RECORDED
    assert row.payload == {"subject": "Hello", "body": "Is anyone there?"}
    assert row.observed_at == OBSERVED


def test_a_redelivered_event_is_replayed_not_duplicated(db, tenant) -> None:
    """THE test. Provider webhooks are at-least-once by design; a retry must not
    produce a second fact."""
    first = inbound.admit(db, tenant.id, observation=_observation())
    second = inbound.admit(db, tenant.id, observation=_observation())
    assert second.observation_id == first.observation_id
    assert second.replayed is True
    assert db.query(InboundObservation).count() == 1


def test_admission_delegates_at_most_once_to_the_kernel_owner(db, tenant) -> None:
    """Hard rule 21: `dotmac_kernel.idempotency` owns "has this been done".
    `admit` is a CALLER of it, not a second implementation — so an idempotency
    record must exist for every admission."""
    from dotmac_kernel.idempotency_models import IdempotencyRecord

    observation = _observation()
    inbound.admit(db, tenant.id, observation=observation)
    record = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == inbound.IDEMPOTENCY_SCOPE)
        .one()
    )
    assert record.key == observation.idempotency_key
    assert record.fingerprint == observation.payload_fingerprint


def test_the_same_event_id_carrying_different_content_is_a_conflict(db, tenant) -> None:
    """A real provider bug or a spoofed replay. Keeping the first version
    silently would hide both."""
    inbound.admit(db, tenant.id, observation=_observation())
    with pytest.raises(IdempotencyConflict):
        inbound.admit(
            db, tenant.id, observation=_observation(payload={"subject": "Different"})
        )


def test_the_same_event_id_at_a_different_account_is_a_different_event(
    db, tenant
) -> None:
    """The key is scoped to the account. A Message-ID delivered to two of our
    mailboxes is two arrivals, and collapsing them drops one."""
    inbound.admit(db, tenant.id, observation=_observation())
    other = inbound.admit(
        db, tenant.id, observation=_observation(account_scope="sales@example.net")
    )
    assert other.replayed is False
    assert db.query(InboundObservation).count() == 2


def test_two_tenants_receiving_the_same_event_id_do_not_collide(db) -> None:
    first = Tenant(name="A", slug=f"a-{uuid4().hex[:8]}")
    second = Tenant(name="B", slug=f"b-{uuid4().hex[:8]}")
    db.add_all([first, second])
    db.flush()
    for tenant in (first, second):
        assert (
            inbound.admit(db, tenant.id, observation=_observation()).replayed is False
        )
    assert db.query(InboundObservation).count() == 2


def test_an_undeclared_channel_is_still_admitted(db, tenant) -> None:
    """An observation that cannot be recorded is a message silently lost. "What
    arrived that we could not handle" is exactly what this ledger answers, so an
    unknown channel is admitted and rejected downstream with a reason."""
    outcome = inbound.admit(db, tenant.id, observation=_observation(channel="telegram"))
    assert outcome.replayed is False


@pytest.mark.parametrize(
    "field", ["provider", "account_scope", "provider_event_id", "channel"]
)
def test_an_observation_missing_its_identity_is_refused(field: str) -> None:
    with pytest.raises(InboundError, match=field):
        _observation(**{field: "  "})


# ── Processing ──────────────────────────────────────────────────────────────


def test_marking_processed_records_the_consequence_was_derived(db, tenant) -> None:
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    inbound.mark_processed(db, tenant.id, observation_id=outcome.observation_id)
    assert (
        db.get(InboundObservation, outcome.observation_id).processing_status
        == STATUS_PROCESSED
    )


def test_a_rejected_observation_keeps_its_payload(db, tenant) -> None:
    """The row that explains a message the customer swears they sent, and what a
    reprocess runs against once the reason is fixed."""
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    inbound.mark_rejected(
        db, tenant.id, observation_id=outcome.observation_id, error_code="no_account"
    )
    row = db.get(InboundObservation, outcome.observation_id)
    assert row.processing_status == STATUS_REJECTED
    assert row.error_code == "no_account"
    assert row.payload == {"subject": "Hello", "body": "Is anyone there?"}


def test_a_rejection_must_say_why(db, tenant) -> None:
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    with pytest.raises(InboundError, match="error_code"):
        inbound.mark_rejected(
            db, tenant.id, observation_id=outcome.observation_id, error_code=""
        )


def test_pending_observations_are_oldest_first(db, tenant) -> None:
    """A conversation's messages must be applied in the order they were
    observed; newest-first would reorder a customer's thread in front of them."""
    for index in range(3):
        inbound.admit(
            db,
            tenant.id,
            observation=_observation(
                provider_event_id=f"<msg-{index}@mail>",
                observed_at=OBSERVED + timedelta(minutes=index),
            ),
        )
    pending = inbound.pending_observations(db, tenant.id)
    assert [row.observed_at for row in pending] == sorted(
        row.observed_at for row in pending
    )


def test_a_processed_observation_leaves_the_pending_queue(db, tenant) -> None:
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    assert len(inbound.pending_observations(db, tenant.id)) == 1
    inbound.mark_processed(db, tenant.id, observation_id=outcome.observation_id)
    assert inbound.pending_observations(db, tenant.id) == ()


def test_another_tenants_observation_cannot_be_transitioned(db, tenant) -> None:
    """Explicit tenant filtering, not RLS — SQLite has none. This is the query
    under test, and Postgres isolation is proven separately."""
    other = Tenant(name="B", slug=f"b-{uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    outcome = inbound.admit(db, tenant.id, observation=_observation())
    with pytest.raises(InboundError, match="not found"):
        inbound.mark_processed(db, other.id, observation_id=outcome.observation_id)
