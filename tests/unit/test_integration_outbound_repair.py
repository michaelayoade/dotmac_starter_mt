"""Replay by key, dead-letter repair, and ambiguous-outcome reconciliation.

The tests that carry real weight, and what each would do on the parent commit
(where `dotmac_integration.outbound_repair` does not exist at all — so all of
them fail to import, which is the trivial half; the interesting half is what
each would do against a plausible WRONG implementation):

* `test_replaying_a_landed_command_returns_the_recorded_outcome` — the
  no-double-send proof. An implementation that simply delegated to
  `operations.replay_delivery` raises `NotRepairable` here; one that requeued a
  `delivered` row instead re-sends an effect the provider already holds, and
  this test is what says so.
* `test_replay_takes_no_content_parameter` — the signature IS the property, the
  same shape `receipt_delivery`'s
  `test_delivery_is_addressed_only_from_trusted_state` pins. Adding a
  `payload=` escape hatch to either entry point turns it red immediately.
* `test_repair_refuses_a_dead_letter_whose_request_cannot_be_verified` — the
  negative case the whole module exists for. Remove the `classify_repair` call
  from `_requeue_verified` and it goes green-to-red: a row with no verifiable
  request would be re-armed and eventually sent.
* `test_a_landed_verdict_never_returns_the_command_to_the_queue` and
  `test_only_a_provider_proven_absence_returns_the_command_to_the_queue` — the
  ambiguous-outcome pair. An implementation that treated `reconciliation_required`
  as "retry it" fails the first; one that treated it as "dead-letter it" fails
  the second.
* `test_no_second_outcome_vocabulary_is_declared` — a ratchet, not a behaviour
  test. It fires the moment someone re-declares `succeeded`/`retryable`/
  `terminal`/`reconciliation_required` in this module instead of using
  `retry.OutcomeStatus`, which is how a queue acquires two opinions about
  whether an attempt may be retried.
* `test_the_reconciliation_subject_carries_no_session` — phase 2's contract,
  checked structurally rather than by asking a probe nicely.

Everything here is SQLite-fast and holds no tenancy claim: `mod_intg` is a
platform-plane schema with no `tenant_id` anywhere, so there is no RLS canary
to write for this slice.
"""

from __future__ import annotations

import dataclasses
import inspect
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from enum import Enum
from uuid import UUID

import pytest
from dotmac_integration import (
    DEAD_LETTER_PAGE_SIZE_VAR,
    DEFAULT_DEAD_LETTER_PAGE_SIZE,
    INCONCLUSIVE_CODE,
    REDACTION_MARKER,
    REPAIR_REFUSAL_REASONS,
    CapabilityBinding,
    ConnectorInstallation,
    DeliveryAttempt,
    DeliveryLegalHold,
    DeliveryNotFound,
    EvidenceStatus,
    OutcomeStatus,
    ProviderEvidence,
    ProviderEvidenceProbe,
    ProviderVerdict,
    ReconciliationSubject,
    RepairRefused,
    ambiguous_report,
    classify_request_evidence,
    dead_letter_report,
    enqueue_delivery,
    inspect_delivery,
    prepare_reconciliation,
    reconcile_with_evidence,
    repair_dead_letter,
    replay_by_idempotency_key,
    resolve_dead_letter_page_size,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
PAYLOAD = {
    "action": "send_text",
    "params": {"recipient": "+2348012345678", "body": "service restored"},
}


class _AuditSpy:
    """Stands in for the kernel's platform audit ledger.

    Patched over BOTH writers' module-level names — this module's own and
    `operations`', which `replay_delivery` resolves through — so a test can see
    every event without a database for the kernel's own tables. The point being
    that this module WRITES to that ledger rather than keeping one.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, db: object, **kwargs: object) -> dict:
        self.events.append(kwargs)
        return kwargs

    def actions(self) -> list[object]:
        return [event["action"] for event in self.events]


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    import dotmac_integration.operations as operations
    import dotmac_integration.outbound_repair as outbound_repair

    spy = _AuditSpy()
    monkeypatch.setattr(operations, "record_operation", spy)
    monkeypatch.setattr(outbound_repair, "record_operation", spy)
    return spy


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (
        ConnectorInstallation,
        CapabilityBinding,
        DeliveryAttempt,
        DeliveryLegalHold,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def installation(db: Session) -> ConnectorInstallation:
    record = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="meta_whatsapp",
        connector_version="0.1.0a3",
        spi_range=">=1.3,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


@pytest.fixture()
def binding(db: Session, installation: ConnectorInstallation) -> CapabilityBinding:
    record = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id="messaging.send.v1",
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


def _queue(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding | None,
    *,
    key: str,
    state: str = "pending",
    payload: object = None,
    attempt_count: int = 0,
    created_at: datetime | None = None,
    **columns: object,
) -> DeliveryAttempt:
    """One outbound command in a chosen state, through the REAL enqueue path.

    Going through `enqueue_delivery` rather than constructing the row matters:
    the digest these tests verify against is the one the shipped enqueue path
    records, not one the test computed itself. A test that digests its own
    payload proves its own arithmetic.
    """
    delivery, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id if binding is not None else None,
        event_type="messaging.send.requested.v1",
        idempotency_key=key,
        payload=PAYLOAD if payload is None else payload,
    )
    delivery.state = state
    delivery.attempt_count = attempt_count
    delivery.created_at = created_at or NOW
    if state in ("delivered", "dead_letter", "reconciliation_required"):
        # What `record_delivery_outcome` does for these states: nothing is due,
        # and leaving a schedule would make a dispatcher pick the row up
        # forever. Mirrored here so the fixture produces rows the engine could
        # actually have produced.
        delivery.next_attempt_at = None
    for column, value in columns.items():
        setattr(delivery, column, value)
    db.flush()
    return delivery


# ── (a) Replay by idempotency key ───────────────────────────────────────────


def test_replay_takes_no_content_parameter() -> None:
    """A replay re-sends the STORED request; there is nowhere to put a new one.

    The security property is what the signature cannot express, exactly as in
    `receipt_delivery.build_product_request`. A caller that wants to change what
    is delivered has to enqueue a different command, which is a different
    idempotency key and therefore a different effect.
    """
    forbidden = ("payload", "body", "content", "observation", "document")
    for entry_point in (replay_by_idempotency_key, repair_dead_letter):
        names = set(inspect.signature(entry_point).parameters)
        offenders = sorted(n for n in names if any(f in n for f in forbidden))
        assert not offenders, (
            f"{entry_point.__name__} accepts {offenders}: a replay that can be "
            "handed content is not a replay"
        )


def test_replay_reuses_the_stored_request_evidence(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    delivery = _queue(
        db,
        installation,
        binding,
        key="sub:message:1",
        state="dead_letter",
        attempt_count=5,
        error_code="provider_rejected",
    )
    digest_before = delivery.payload_digest

    decision = replay_by_idempotency_key(
        db,
        installation_id=installation.id,
        idempotency_key="sub:message:1",
        reason="provider outage resolved",
    )

    assert decision.requeued is True
    assert decision.previous_state == "dead_letter"
    assert decision.state == "pending"
    # The budget is reset by `operations.replay_delivery`, which stays the one
    # owner of that. The count that WAS spent survives in the decision.
    assert decision.previous_attempt_count == 5
    assert delivery.attempt_count == 0
    # The stored request is untouched: same digest, same content, no rebuild.
    assert delivery.payload_digest == digest_before
    assert delivery.payload_json == PAYLOAD
    assert decision.evidence is not None
    assert decision.evidence.is_verified
    assert dict(decision.evidence.payload) == PAYLOAD
    # `operations.replay_delivery` is the one writer of the requeue fact; this
    # module adds no second audit code for it. The prefix is applied inside
    # `record_operation`, which the spy stands in for.
    assert audit.actions() == ["delivery.replayed"]


def test_replaying_a_landed_command_returns_the_recorded_outcome(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """The no-double-send proof.

    `operations.replay_delivery` raises on a delivered row — right for the
    mechanism, wrong for the operator, who asked a question whose true answer
    is "it already landed, here is what happened". Nothing is written, nothing
    is queued, and the provider is not asked twice.
    """
    delivered_at = NOW - timedelta(minutes=5)
    delivery = _queue(
        db,
        installation,
        binding,
        key="sub:message:2",
        state="delivered",
        attempt_count=2,
        delivered_at=delivered_at,
        provider_reference="wamid.outbound-2",
        provider_status_code=200,
        result_json={"accepted_reference": "message-2"},
    )

    decision = replay_by_idempotency_key(
        db,
        installation_id=installation.id,
        idempotency_key="sub:message:2",
        reason="operator asked to resend",
    )

    assert decision.requeued is False
    assert decision.reason == "already_delivered"
    assert decision.outcome is not None
    assert decision.outcome.delivered_at == delivered_at
    assert decision.outcome.provider_reference == "wamid.outbound-2"
    assert decision.outcome.provider_status_code == 200
    assert decision.outcome.result == {"accepted_reference": "message-2"}
    assert decision.outcome.attempt_count == 2
    # NOTHING moved, and nothing was audited: no decision was taken.
    assert delivery.state == "delivered"
    assert delivery.attempt_count == 2
    assert delivery.next_attempt_at is None
    assert audit.events == []


def test_replay_refuses_an_ambiguous_command_and_names_reconciliation(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """`operations.replay_delivery` would accept this row. This owner does not.

    An INDETERMINATE outcome may have half-landed at the provider. Replaying
    risks doing the effect twice; the only safe next step is asking the
    provider what it holds.
    """
    _queue(
        db,
        installation,
        binding,
        key="sub:message:3",
        state="reconciliation_required",
        attempt_count=1,
    )

    with pytest.raises(RepairRefused) as raised:
        replay_by_idempotency_key(
            db,
            installation_id=installation.id,
            idempotency_key="sub:message:3",
            reason="just retry it",
        )

    assert raised.value.reason == "ambiguous_outcome"
    assert "prepare_reconciliation" in str(raised.value)
    assert audit.events == []


def test_replay_of_a_queued_command_changes_nothing(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A command that is already going to be attempted is not "repaired".

    Resetting its budget would be an action with no effect anyone asked for,
    and would read in the audit trail as though something had been fixed.
    """
    delivery = _queue(db, installation, binding, key="sub:message:4", attempt_count=1)

    decision = replay_by_idempotency_key(
        db,
        installation_id=installation.id,
        idempotency_key="sub:message:4",
        reason="impatient operator",
    )

    assert decision.requeued is False
    assert decision.reason == "already_queued"
    assert delivery.attempt_count == 1
    assert audit.events == []


def test_replay_refuses_a_command_a_worker_still_holds(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    _queue(
        db,
        installation,
        binding,
        key="sub:message:5",
        state="in_flight",
        attempt_count=1,
        leased_until=NOW + timedelta(minutes=5),
    )

    with pytest.raises(RepairRefused) as raised:
        replay_by_idempotency_key(
            db,
            installation_id=installation.id,
            idempotency_key="sub:message:5",
            reason="looks stuck",
        )

    assert raised.value.reason == "in_flight"
    # It names the owner of lease recovery instead of doing lease recovery.
    assert "release_expired_leases" in str(raised.value)


def test_an_unknown_key_is_not_found_rather_than_refused(
    db: Session, installation: ConnectorInstallation
) -> None:
    with pytest.raises(DeliveryNotFound):
        replay_by_idempotency_key(
            db,
            installation_id=installation.id,
            idempotency_key="sub:message:never",
            reason="typo in the ticket",
        )


def test_a_key_is_scoped_to_its_installation(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """`(installation_id, idempotency_key)` is the outbox's unique constraint.

    Resolving by key alone would let one installation's operator replay
    another's command whenever two products happened to mint the same key.
    """
    _queue(db, installation, binding, key="shared:key", state="dead_letter")
    other = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="other_provider",
        connector_version="1.0.0",
        spi_range=">=1.3,<2.0",
        manifest_digest="e" * 64,
        name="secondary",
        state="enabled",
    )
    db.add(other)
    db.flush()

    with pytest.raises(DeliveryNotFound):
        replay_by_idempotency_key(
            db,
            installation_id=other.id,
            idempotency_key="shared:key",
            reason="wrong installation",
        )


# ── Request evidence, the thing "verified" actually means ───────────────────


def test_an_intact_request_digests_to_what_enqueue_recorded(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    delivery = _queue(db, installation, binding, key="ev:1", state="dead_letter")
    assert classify_request_evidence(delivery) is EvidenceStatus.INTACT


def test_a_non_mapping_payload_is_still_intact(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """`enqueue_delivery` digests the payload it was GIVEN and stores an
    envelope when that payload was not a mapping.

    So the stored digest is over the unwrapped value while the stored JSON is
    `{"value": ...}`. An evidence check that only recomputed over the stored
    JSON would report every scalar and list payload ever enqueued as tampered,
    and this module would refuse to repair rows that are perfectly intact.
    """
    delivery = _queue(
        db,
        installation,
        binding,
        key="ev:2",
        state="dead_letter",
        payload=["first", "second"],
    )
    assert delivery.payload_json == {"value": ["first", "second"]}
    assert classify_request_evidence(delivery) is EvidenceStatus.INTACT


def test_a_rewritten_payload_no_longer_verifies(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    delivery = _queue(db, installation, binding, key="ev:3", state="dead_letter")
    delivery.payload_json = {"action": "send_text", "params": {"body": "different"}}
    db.flush()
    assert classify_request_evidence(delivery) is EvidenceStatus.DIGEST_MISMATCH


def test_a_missing_payload_no_longer_verifies(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    delivery = _queue(db, installation, binding, key="ev:4", state="dead_letter")
    delivery.payload_json = None
    db.flush()
    assert classify_request_evidence(delivery) is EvidenceStatus.MISSING


def test_a_retention_tombstone_is_not_a_payload(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """The marker `retention` writes is recognised, not mistaken for content.

    Note honestly what this is: DEFENCE IN DEPTH. `retention.classify_delivery`
    refuses to redact anything that is not `delivered`, so today's sweep cannot
    produce a redacted dead letter. This gate is what keeps that one refusal
    from being the only thing between a retention-policy change and a replay of
    a tombstone.
    """
    delivery = _queue(db, installation, binding, key="ev:5", state="dead_letter")
    delivery.payload_json = {
        REDACTION_MARKER: {
            "redacted_at": NOW.isoformat(),
            "retention_days": 30,
            "legal_policy_owner": "test-legal-owner",
            "payload_digest": delivery.payload_digest,
            "key_count": 2,
        }
    }
    db.flush()
    assert classify_request_evidence(delivery) is EvidenceStatus.REDACTED


# ── (b) Dead-letter inspection and operator repair ──────────────────────────


def test_a_dead_letter_is_inspectable_without_a_database_client(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """What was attempted, against which binding, with what provider evidence
    and what classification — the four questions the task names."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="dl:1",
        state="dead_letter",
        attempt_count=6,
        error_code="provider_rejected",
        error_detail="TypeError",
        provider_reference="wamid.failed-1",
        provider_status_code=422,
    )

    entry = inspect_delivery(db, delivery_id=delivery.id)

    # what was attempted
    assert entry.event_type == "messaging.send.requested.v1"
    assert entry.idempotency_key == "dl:1"
    assert entry.payload_digest == delivery.payload_digest
    assert entry.attempt_count == 6
    # against which binding
    assert entry.capability_binding_id == binding.id
    assert entry.capability_id == "messaging.send.v1"
    assert entry.binding_state == "enabled"
    assert entry.connector_key == "meta_whatsapp"
    assert entry.installation_state == "enabled"
    # with what provider evidence
    assert entry.provider_reference == "wamid.failed-1"
    assert entry.provider_status_code == 422
    # and what classification
    assert entry.state == "dead_letter"
    assert entry.error_code == "provider_rejected"
    assert entry.evidence is EvidenceStatus.INTACT
    assert entry.legal_hold is False
    assert entry.repairable is True
    assert entry.refusal is None
    # And it projects to a wire document without leaking the payload itself.
    rendered = entry.as_dict()
    assert rendered["payload_digest"] == delivery.payload_digest
    assert "payload" not in rendered
    assert "payload_json" not in rendered


def test_the_report_and_the_repair_command_cannot_disagree(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """One decision function, so an operator is never shown a repairable row
    the command then refuses — nor, worse, an unrepairable one it accepts."""
    good = _queue(db, installation, binding, key="dl:ok", state="dead_letter")
    broken = _queue(db, installation, binding, key="dl:broken", state="dead_letter")
    broken.payload_json = None
    db.flush()

    entries = {e.delivery_id: e for e in dead_letter_report(db)}
    assert entries[good.id].repairable is True
    assert entries[broken.id].repairable is False
    assert entries[broken.id].refusal == "evidence_missing"

    for entry in entries.values():
        if entry.repairable:
            repair_dead_letter(
                db, delivery_id=entry.delivery_id, reason="verified by hand"
            )
        else:
            with pytest.raises(RepairRefused) as raised:
                repair_dead_letter(
                    db, delivery_id=entry.delivery_id, reason="verified by hand"
                )
            assert raised.value.reason == entry.refusal


def test_repair_refuses_a_dead_letter_whose_request_cannot_be_verified(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """THE negative case. A dead letter is never silently retried into a live
    effect without the verification evidence the original had.

    Two ways the evidence can be gone, both refused by name, and in both cases
    the row is left exactly as it was — a refusal that half-moved the row would
    be worse than no refusal at all.
    """
    for key, mutate, expected in (
        ("dl:gone", lambda d: setattr(d, "payload_json", None), "evidence_missing"),
        (
            "dl:tombstone",
            lambda d: setattr(
                d, "payload_json", {REDACTION_MARKER: {"redacted_at": NOW.isoformat()}}
            ),
            "evidence_redacted",
        ),
        (
            "dl:rewritten",
            lambda d: setattr(d, "payload_json", {"action": "something_else"}),
            "evidence_digest_mismatch",
        ),
    ):
        delivery = _queue(
            db, installation, binding, key=key, state="dead_letter", attempt_count=6
        )
        mutate(delivery)
        db.flush()

        with pytest.raises(RepairRefused) as raised:
            repair_dead_letter(
                db, delivery_id=delivery.id, reason="operator says send it"
            )

        assert raised.value.reason == expected
        assert raised.value.delivery_id == delivery.id
        assert delivery.state == "dead_letter"
        assert delivery.attempt_count == 6
        assert delivery.next_attempt_at is None
    assert audit.events == []


def test_repair_refuses_a_command_whose_route_no_longer_exists(
    db: Session,
    installation: ConnectorInstallation,
) -> None:
    """Requeueing an unroutable command parks it silently, which is the exact
    outcome this module exists to make impossible."""
    delivery = _queue(db, installation, None, key="dl:unrouted", state="dead_letter")

    with pytest.raises(RepairRefused) as raised:
        repair_dead_letter(db, delivery_id=delivery.id, reason="try again")

    assert raised.value.reason == "binding_missing"
    assert delivery.state == "dead_letter"


def test_repair_is_for_dead_letters_only(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """So that "repair" names one situation rather than becoming a
    general-purpose state mover."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="dl:retryable",
        state="retryable",
        attempt_count=2,
    )

    with pytest.raises(RepairRefused) as raised:
        repair_dead_letter(db, delivery_id=delivery.id, reason="pull it forward")

    assert raised.value.reason == "not_dead_letter"
    assert delivery.state == "retryable"


def test_a_held_dead_letter_is_reported_as_held_and_still_repairable(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A legal hold forbids DESTRUCTION, not delivery.

    Conflating the two would make a hold quietly cancel the work it was placed
    to preserve — the opposite of what an operator asked for.
    """
    delivery = _queue(db, installation, binding, key="dl:held", state="dead_letter")
    db.add(
        DeliveryLegalHold(
            id=uuid.uuid4(),
            delivery_id=delivery.id,
            reason="regulator request",
            policy_owner="test-legal-owner",
            placed_by="compliance@dotmac",
        )
    )
    db.flush()

    entry = inspect_delivery(db, delivery_id=delivery.id)
    assert entry.legal_hold is True
    assert entry.repairable is True


def test_the_report_is_oldest_first_and_bounded(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A backlog is worked from its far end; newest-first paging buries the
    rows that have been stuck longest."""
    for index in range(5):
        _queue(
            db,
            installation,
            binding,
            key=f"page:{index}",
            state="dead_letter",
            created_at=NOW - timedelta(days=5 - index),
        )

    page = dead_letter_report(db, page_size=3)
    assert [entry.idempotency_key for entry in page] == ["page:0", "page:1", "page:2"]


def test_the_two_backlogs_are_reported_separately(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A dead letter needs a decision about the work; an ambiguous one needs
    evidence from the provider before any decision is even safe."""
    _queue(db, installation, binding, key="split:dead", state="dead_letter")
    _queue(
        db,
        installation,
        binding,
        key="split:ambiguous",
        state="reconciliation_required",
    )

    assert [e.idempotency_key for e in dead_letter_report(db)] == ["split:dead"]
    ambiguous = ambiguous_report(db)
    assert [e.idempotency_key for e in ambiguous] == ["split:ambiguous"]
    assert ambiguous[0].refusal == "ambiguous_outcome"
    assert ambiguous[0].repairable is False


def test_the_page_size_is_a_configured_knob_with_a_documented_default() -> None:
    assert resolve_dead_letter_page_size({}) == DEFAULT_DEAD_LETTER_PAGE_SIZE
    assert resolve_dead_letter_page_size({DEAD_LETTER_PAGE_SIZE_VAR: "25"}) == 25
    # Whitespace-only is absent, not a parse error — an unset compose variable
    # renders as an empty string far more often than as a missing key.
    assert (
        resolve_dead_letter_page_size({DEAD_LETTER_PAGE_SIZE_VAR: "  "})
        == DEFAULT_DEAD_LETTER_PAGE_SIZE
    )
    for bad in ("0", "-1", "100000", "many"):
        with pytest.raises(ValueError):
            resolve_dead_letter_page_size({DEAD_LETTER_PAGE_SIZE_VAR: bad})


# ── (c) Ambiguous-outcome reconciliation ────────────────────────────────────


def test_no_second_outcome_vocabulary_is_declared() -> None:
    """`retry.OutcomeStatus` stays the ONE classification of an attempt.

    A ratchet rather than a behaviour test. It fires the moment a local enum
    re-declares `succeeded`/`retryable`/`terminal`/`reconciliation_required`,
    which is how a queue acquires two opinions about whether an attempt may be
    retried — and the one consulted last silently wins.
    """
    import dotmac_integration.outbound_repair as outbound_repair

    reserved = {member.value for member in OutcomeStatus}
    assert reserved & {"reconciliation_required"}, "the ambiguous state moved"
    for name, obj in vars(outbound_repair).items():
        if isinstance(obj, type) and issubclass(obj, Enum) and obj is not OutcomeStatus:
            clash = {member.value for member in obj} & reserved
            assert (
                not clash
            ), f"{name} re-declares {sorted(clash)} — use retry.OutcomeStatus"


def test_the_reconciliation_subject_carries_no_session(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """Phase 2's contract, checked structurally rather than by asking nicely.

    A probe that were handed an ORM instance would be handed a session with it,
    and a transaction open across a provider round-trip holds row locks for the
    duration of someone else's outage.
    """
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:1",
        state="reconciliation_required",
        attempt_count=1,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    assert isinstance(subject, ReconciliationSubject)
    for field in dataclasses.fields(subject):
        value = getattr(subject, field.name)
        assert isinstance(value, UUID | str | int | type(None)), (
            f"{field.name} carries a {type(value).__name__}, which is not a "
            "plain value a probe may hold across the network"
        )
    # And the port itself takes no session.
    assert list(inspect.signature(ProviderEvidenceProbe.probe).parameters) == [
        "self",
        "subject",
    ]


def test_reconciliation_refuses_a_command_with_a_known_outcome(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """Otherwise provider evidence could overwrite a settled fact."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:settled",
        state="delivered",
        delivered_at=NOW,
    )

    with pytest.raises(RepairRefused) as raised:
        prepare_reconciliation(db, delivery_id=delivery.id)
    assert raised.value.reason == "not_ambiguous"


def test_a_landed_verdict_never_returns_the_command_to_the_queue(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """The effect is already at the provider. Re-arming the queue here is
    precisely the duplicate the whole ambiguous state exists to prevent."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:landed",
        state="reconciliation_required",
        attempt_count=3,
        error_code="connector_raised",
        error_detail="TimeoutError",
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    decision = reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(
            verdict=ProviderVerdict.LANDED,
            provider_reference="wamid.confirmed-1",
            provider_status_code=200,
            detail="provider says accepted at 11:59",
        ),
        reason="probed the provider's message log",
        now=NOW,
    )

    assert decision.requeued is False
    assert decision.state == "delivered"
    assert delivery.state == "delivered"
    # `is not None` rather than `== NOW`: the row is re-read after the
    # conditional UPDATE, and SQLite's DATETIME has no timezone to give back —
    # an equality here would be testing the driver, not the module.
    assert delivery.delivered_at is not None
    assert delivery.next_attempt_at is None
    assert delivery.attempt_count == 3
    assert delivery.provider_reference == "wamid.confirmed-1"
    assert delivery.provider_status_code == 200
    assert delivery.error_code is None
    assert audit.actions() == ["delivery.reconciled"]


def test_a_landed_verdict_keeps_the_reference_the_attempt_observed(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A probe that confirms without quoting a reference must not erase the one
    handle a later provider callback could be matched by."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:keepref",
        state="reconciliation_required",
        attempt_count=1,
        provider_reference="wamid.from-attempt",
        provider_status_code=504,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(verdict=ProviderVerdict.LANDED),
        reason="confirmed out of band",
        now=NOW,
    )

    assert delivery.provider_reference == "wamid.from-attempt"
    assert delivery.provider_status_code == 504


def test_only_a_provider_proven_absence_returns_the_command_to_the_queue(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:absent",
        state="reconciliation_required",
        attempt_count=4,
        error_code="connector_raised",
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    decision = reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(
            verdict=ProviderVerdict.NOT_LANDED, detail="no such message id"
        ),
        reason="provider log shows nothing was received",
        now=NOW,
    )

    assert decision.requeued is True
    assert decision.state == "pending"
    assert delivery.state == "pending"
    # Reset, for `replay_delivery`'s reason: leaving the count at the cap makes
    # the requeue a no-op that dead-letters again on its very next outcome.
    assert delivery.attempt_count == 0
    assert delivery.error_code is None
    assert audit.actions() == ["delivery.reconciled"]


def test_a_proven_absence_still_cannot_requeue_an_unverifiable_request(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """Provider-proven absence says re-dispatching is SAFE. It does not say the
    bytes still exist — so the same evidence gate applies here as to a repair.
    """
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:absent-noevidence",
        state="reconciliation_required",
        attempt_count=2,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)
    delivery.payload_json = None
    db.flush()

    with pytest.raises(RepairRefused) as raised:
        reconcile_with_evidence(
            db,
            subject,
            ProviderEvidence(verdict=ProviderVerdict.NOT_LANDED),
            reason="provider proved absence",
            now=NOW,
        )

    assert raised.value.reason == "evidence_missing"
    assert delivery.state == "reconciliation_required"
    assert audit.events == []


def test_an_unknown_verdict_is_neither_retried_nor_failed(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """The answer people leave out. Forcing a connector that cannot tell into
    one of the other two verdicts is how a reconciler turns "I do not know"
    into either a duplicate send or a lost effect."""
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:unknown",
        state="reconciliation_required",
        attempt_count=2,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    decision = reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(
            verdict=ProviderVerdict.UNKNOWN, detail="provider search timed out"
        ),
        reason="second probe attempt",
        now=NOW,
    )

    assert decision.requeued is False
    assert decision.state == "reconciliation_required"
    assert delivery.state == "reconciliation_required"
    assert delivery.attempt_count == 2
    assert delivery.next_attempt_at is None
    # A marker this module owns, so an operator can filter the inconclusive
    # ones without the module persisting a string a plugin authored.
    assert delivery.error_code == INCONCLUSIVE_CODE
    assert delivery.error_detail is None
    assert audit.actions() == ["delivery.reconciled"]


def test_a_stale_reconciliation_is_refused_rather_than_written(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """The probe runs with no session held, so the row can move underneath it.

    The guard IS the write: this delivery, still ambiguous, still at the
    attempt the subject was read at. A loser that recorded its answer anyway
    would overwrite whatever the winner decided.
    """
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:raced",
        state="reconciliation_required",
        attempt_count=2,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    # Something else moved it while the probe was in flight.
    delivery.attempt_count = 3
    db.flush()

    with pytest.raises(RepairRefused) as raised:
        reconcile_with_evidence(
            db,
            subject,
            ProviderEvidence(verdict=ProviderVerdict.LANDED),
            reason="stale answer",
            now=NOW,
        )

    assert raised.value.reason == "raced"
    assert delivery.state == "reconciliation_required"
    assert audit.events == []


def test_the_audit_event_carries_the_verdict_and_no_connector_text(
    db: Session,
    audit: _AuditSpy,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """`dispatch.invoke` persists only an exception's TYPE name because a
    connector holding materialized secrets authored the message. A probe's
    `detail` is the same category of text, and this ledger outlives the
    process, the request and the credential.
    """
    secret_ish = "Bearer EAAG-super-secret-token"
    delivery = _queue(
        db,
        installation,
        binding,
        key="rc:audit",
        state="reconciliation_required",
        attempt_count=1,
    )
    subject = prepare_reconciliation(db, delivery_id=delivery.id)

    decision = reconcile_with_evidence(
        db,
        subject,
        ProviderEvidence(
            verdict=ProviderVerdict.LANDED,
            provider_reference="wamid.audited",
            detail=secret_ish,
        ),
        reason="probed",
        actor_admin_id=uuid.uuid4(),
        now=NOW,
    )

    assert decision.detail == secret_ish, "the caller still gets it, for its log"
    event = audit.events[-1]
    assert event["action"] == "delivery.reconciled"
    assert event["entity_type"] == "delivery_attempt"
    assert event["entity_id"] == str(delivery.id)
    details = event["details"]
    assert details["verdict"] == "landed"
    assert details["previous_state"] == "reconciliation_required"
    assert details["resulting_state"] == "delivered"
    assert details["idempotency_key"] == "rc:audit"
    assert details["payload_digest"] == delivery.payload_digest
    assert details["provider_reference"] == "wamid.audited"
    assert secret_ish not in str(details)


def test_provider_evidence_reuses_the_engine_s_own_bounds() -> None:
    """Two copies of "1..500 characters, HTTP 100..599" that are supposed to
    agree eventually do not, so the bounds stay in `retry.Outcome`."""
    assert (
        ProviderEvidence(
            verdict=ProviderVerdict.LANDED, provider_reference="  wamid.trimmed  "
        ).provider_reference
        == "wamid.trimmed"
    )
    with pytest.raises(ValueError):
        ProviderEvidence(verdict=ProviderVerdict.LANDED, provider_reference="x" * 501)
    with pytest.raises(ValueError):
        ProviderEvidence(verdict=ProviderVerdict.LANDED, provider_status_code=99)


def test_every_refusal_this_module_raises_is_a_declared_reason(
    db: Session,
    installation: ConnectorInstallation,
    binding: CapabilityBinding,
) -> None:
    """A CLOSED set: inventing a word for an awkward row is how a refusal
    vocabulary stops meaning anything."""
    import dotmac_integration.outbound_repair as outbound_repair

    assert set(outbound_repair._REFUSAL_DETAIL) == set(REPAIR_REFUSAL_REASONS)
    assert len(REPAIR_REFUSAL_REASONS) == len(set(REPAIR_REFUSAL_REASONS))
    # And every reason `classify_repair` can return is one of them.
    for state in ("delivered", "pending", "in_flight", "reconciliation_required"):
        delivery = _queue(db, installation, binding, key=f"reason:{state}", state=state)
        reason = outbound_repair.classify_repair(db, delivery)
        assert reason in REPAIR_REFUSAL_REASONS
