"""Payload retention: age out the content, keep the identity.

The tests that carry real weight, and why:

* **a redacted receipt still deduplicates a redelivery.** This is the whole
  point of the slice. Providers retry for days and a restored queue can
  resurface a months-old event; if ageing out a payload turned that redelivery
  into a NEW event, the product would answer a customer's conversation twice.
* **four refusals, four different ways to destroy in-flight work.** A legal
  hold, a worker's claim, an unresolved receipt and one awaiting reconciliation
  are each refused BY NAME and counted — never a row quietly missing from a
  batch.
* **nothing is configured by default.** `resolve_retention_policy` refuses
  until a period and a legal-policy owner are stated, because a default here
  becomes the deployment's data-retention posture without anyone deciding it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    REDACTION_MARKER,
    RETENTION_DAYS_VAR,
    RETENTION_LEGAL_POLICY_OWNER_VAR,
    CapabilityBinding,
    ConnectorInstallation,
    InboxReceipt,
    ProviderEventIdentityCollision,
    ReceiptLegalHold,
    RetentionNotConfigured,
    RetentionPolicy,
    RetentionRefused,
    active_hold_for,
    classify_receipt,
    is_redacted,
    place_legal_hold,
    purge_expired_payloads,
    receive_verified,
    redact_receipt,
    release_legal_hold,
    resolve_retention_policy,
    retention_backlog,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

#: A period for the TESTS, stated here rather than imported, because the module
#: deliberately ships none. If this constant could be imported from
#: `dotmac_integration`, the slice would have failed.
TEST_RETENTION_DAYS = 30
TEST_LEGAL_OWNER = "test-legal-owner"

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=400)
YESTERDAY = NOW - timedelta(days=1)

PAYLOAD = {"messages": [{"from": "+2348012345678", "text": "when is my router due"}]}
HEADERS = {"x-hub-signature-256": "sha256=deadbeef", "content-type": "application/json"}
CONSEQUENCE = {"ticket_id": "TCK-4471", "subscriber_ref": "SUB-99"}


@pytest.fixture()
def policy() -> RetentionPolicy:
    return RetentionPolicy(
        payload_retention_days=TEST_RETENTION_DAYS,
        legal_policy_owner=TEST_LEGAL_OWNER,
    )


class _AuditSpy:
    """Stands in for the kernel's platform audit ledger.

    Patched over `record_operation`'s deferred import, exactly as
    `test_integration_operations.py` does — the point being that retention
    WRITES to that one ledger rather than keeping a second evidence store of
    its own.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def __call__(self, db: object, **kwargs: object) -> dict[str, object]:
        self.events.append(kwargs)
        return kwargs


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    spy = _AuditSpy()
    import dotmac_integration.retention as retention_module

    monkeypatch.setattr(retention_module, "record_operation", spy)
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
        InboxReceipt,
        ReceiptLegalHold,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def binding(db: Session) -> CapabilityBinding:
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="conformance_fake",
        connector_version="1.0.0",
        spi_range=">=1.0,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(installation)
    db.flush()
    record = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id="message.observation.v1",
        state="enabled",
    )
    db.add(record)
    db.flush()
    return record


def _instant(value: object) -> object:
    """Datetimes compared as INSTANTS, everything else as itself.

    `redact_receipt` ends with `db.refresh`, and SQLite has no timezone type:
    an aware value written in Python comes back naive, so an untouched
    timestamp would read as "changed" and this guard would fail on the one
    thing it is meant to permit. Naive is treated as UTC, which is what the
    column stores. The guard survives: a redaction that genuinely moved a
    timestamp changes the instant and is still caught.
    """
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
    return value


def _receipt(
    db: Session,
    binding: CapabilityBinding,
    *,
    provider_event_id: str = "wamid.EVENT-1",
    payload: dict[str, object] | None = None,
    received_at: datetime = LONG_AGO,
    state: str = "processed",
    consequence: dict[str, object] | None = None,
) -> InboxReceipt:
    receipt, _ = receive_verified(
        db,
        installation_id=binding.installation_id,
        capability_binding_id=binding.id,
        provider_event_id=provider_event_id,
        event_type="message.received",
        payload=PAYLOAD if payload is None else payload,
        headers=dict(HEADERS),
    )
    receipt.received_at = received_at
    receipt.state = state
    if state == "processed":
        receipt.processed_at = received_at
        receipt.consequence_json = dict(
            CONSEQUENCE if consequence is None else consequence
        )
    db.flush()
    return receipt


# ── Nothing runs until someone decides the policy ───────────────────────────


def test_retention_refuses_to_run_until_it_is_configured() -> None:
    """No period, no owner, no defaults. This is the fail-closed guarantee."""
    with pytest.raises(RetentionNotConfigured) as excinfo:
        resolve_retention_policy({})
    message = str(excinfo.value)
    assert RETENTION_DAYS_VAR in message
    assert RETENTION_LEGAL_POLICY_OWNER_VAR in message


def test_a_period_with_no_legal_owner_is_not_a_policy() -> None:
    """Half a decision is refused. A purge with nobody able to forbid it is
    worse than no purge, because it looks configured."""
    with pytest.raises(RetentionNotConfigured) as excinfo:
        resolve_retention_policy({RETENTION_DAYS_VAR: "30"})
    assert RETENTION_LEGAL_POLICY_OWNER_VAR in str(excinfo.value)


def test_an_owner_with_no_period_is_an_owner_of_nothing() -> None:
    with pytest.raises(RetentionNotConfigured) as excinfo:
        resolve_retention_policy({RETENTION_LEGAL_POLICY_OWNER_VAR: "legal@example"})
    assert RETENTION_DAYS_VAR in str(excinfo.value)


def test_a_blank_value_is_not_a_decision() -> None:
    """`FOO=` in a `.env` is an unset knob wearing a set knob's clothes."""
    with pytest.raises(RetentionNotConfigured):
        resolve_retention_policy(
            {RETENTION_DAYS_VAR: "  ", RETENTION_LEGAL_POLICY_OWNER_VAR: ""}
        )


def test_a_period_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(RetentionNotConfigured):
        resolve_retention_policy(
            {
                RETENTION_DAYS_VAR: "ninety",
                RETENTION_LEGAL_POLICY_OWNER_VAR: "legal@example",
            }
        )


def test_the_policy_cannot_be_constructed_without_stating_both_decisions() -> None:
    """A `TypeError`, not a default. The dataclass itself is the guard."""
    with pytest.raises(TypeError):
        RetentionPolicy()  # type: ignore[call-arg]


def test_a_zero_day_period_is_refused() -> None:
    with pytest.raises(ValueError, match="not a retention period"):
        RetentionPolicy(payload_retention_days=0, legal_policy_owner="legal@example")


def test_a_configured_policy_is_used_verbatim() -> None:
    resolved = resolve_retention_policy(
        {
            RETENTION_DAYS_VAR: "45",
            RETENTION_LEGAL_POLICY_OWNER_VAR: " legal@example ",
        }
    )
    assert resolved.payload_retention_days == 45
    assert resolved.legal_policy_owner == "legal@example"
    assert resolved.cutoff(NOW) == NOW - timedelta(days=45)


# ── The identity survives its content ───────────────────────────────────────


def test_a_redacted_receipt_still_answers_a_redelivery_as_a_duplicate(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """The reason this slice exists.

    Meta retries. If ageing out a payload resurrected a months-old message as
    fresh, the product would process one customer conversation twice.
    """
    receipt = _receipt(db, binding)
    redact_receipt(db, receipt, policy=policy, now=NOW)

    again, is_new = receive_verified(
        db,
        installation_id=binding.installation_id,
        capability_binding_id=binding.id,
        provider_event_id="wamid.EVENT-1",
        event_type="message.received",
        payload=PAYLOAD,
    )
    assert is_new is False, "a redacted receipt must not read as a new event"
    assert again.id == receipt.id
    assert again.state == "processed", "and it must still say it was handled"


def test_a_redacted_receipt_still_detects_a_provider_identity_collision(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """`payload_digest` outlives the payload, so the stronger statement still
    holds: one event id with DIFFERENT content is a provider defect, not a
    redelivery, and deduping it would discard real data."""
    receipt = _receipt(db, binding)
    redact_receipt(db, receipt, policy=policy, now=NOW)

    with pytest.raises(ProviderEventIdentityCollision):
        receive_verified(
            db,
            installation_id=binding.installation_id,
            capability_binding_id=binding.id,
            provider_event_id="wamid.EVENT-1",
            event_type="message.received",
            payload={"messages": [{"text": "something else entirely"}]},
        )


def test_redaction_touches_only_the_redactable_columns(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Diff the WHOLE row, not a list of columns someone remembered to check.

    Written this way on purpose: a future column added to `inbox_receipts` is
    covered by this test the day it lands, without anyone updating it.
    """
    receipt = _receipt(db, binding)
    redactable = {"payload_json", "headers_json", "consequence_json"}
    columns = [c.name for c in InboxReceipt.__table__.columns]
    before = {name: _instant(getattr(receipt, name)) for name in columns}

    redact_receipt(db, receipt, policy=policy, now=NOW)
    after = {name: _instant(getattr(receipt, name)) for name in columns}

    changed = {name for name in columns if before[name] != after[name]}
    assert changed <= redactable, (
        f"redaction wrote to {sorted(changed - redactable)}. Everything outside "
        "payload/headers/consequence is deduplication identity, ordering or "
        "outcome evidence, and a redelivery depends on all of it"
    )
    # Named explicitly as well, because "nothing changed" would also pass the
    # assertion above and would mean redaction did nothing at all.
    assert after["payload_digest"] == before["payload_digest"]
    assert after["provider_event_id"] == before["provider_event_id"]
    assert after["capability_binding_id"] == before["capability_binding_id"]
    assert after["state"] == "processed"
    assert is_redacted(receipt)


def test_the_tombstone_carries_evidence_about_the_payload_never_the_payload(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Including the payload's own KEY NAMES.

    A provider is free to key a map by a phone number, so payload keys are
    provider data wearing schema's clothes. Only the digest and a count survive.
    """
    receipt = _receipt(
        db,
        binding,
        payload={"+2348012345678": {"text": "hello"}, "meta": {"ts": 1}},
    )
    redact_receipt(db, receipt, policy=policy, now=NOW)

    payload = receipt.payload_json
    assert isinstance(payload, dict)
    assert set(payload) == {REDACTION_MARKER}
    marker = payload[REDACTION_MARKER]
    assert isinstance(marker, dict)
    assert marker["payload_digest"] == receipt.payload_digest
    assert marker["retention_days"] == TEST_RETENTION_DAYS
    assert marker["legal_policy_owner"] == TEST_LEGAL_OWNER
    assert marker["key_count"] == 2

    rendered = repr(receipt.payload_json)
    assert "+2348012345678" not in rendered
    assert "hello" not in rendered


def test_header_names_survive_but_header_values_do_not(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """That the request was signed is evidence; the signature is a secret."""
    receipt = _receipt(db, binding)
    redact_receipt(db, receipt, policy=policy, now=NOW)

    headers = receipt.headers_json
    assert isinstance(headers, dict)
    marker = headers[REDACTION_MARKER]
    assert isinstance(marker, dict)
    assert marker["header_names"] == sorted(HEADERS)
    assert "sha256=deadbeef" not in repr(headers)


def test_outcome_evidence_survives_as_a_digest_so_a_replay_is_comparable(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """ "Did replaying this produce the same consequence?" must stay answerable
    after the consequence values are gone — that is the difference between a
    safe replay and a guess."""
    receipt = _receipt(db, binding)
    redact_receipt(db, receipt, policy=policy, now=NOW)

    consequence = receipt.consequence_json
    assert isinstance(consequence, dict)
    marker = consequence[REDACTION_MARKER]
    assert isinstance(marker, dict)
    assert marker["consequence_digest"], "the outcome digest is the comparison"
    assert marker["consequence_keys"] == sorted(CONSEQUENCE)
    assert "TCK-4471" not in repr(consequence)


def test_the_redaction_marker_is_a_wire_contract() -> None:
    """Pinned deliberately. `dotmac_integrator`'s retention-backlog gauge counts
    receipts still holding real content by matching on this literal, so a rename
    would silently make that metric read zero forever."""
    assert REDACTION_MARKER == "__dotmac_redacted__"


# ── Four refusals, four ways to destroy in-flight work ──────────────────────


def test_a_legally_held_receipt_is_refused_by_name(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Refusal 1 of 4. Checked FIRST, so it beats a receipt that is otherwise
    perfectly eligible — long processed, long expired, still carrying its body."""
    receipt = _receipt(db, binding)
    place_legal_hold(
        db,
        receipt,
        policy=policy,
        reason="regulator request DM-2026-118",
        placed_by="ops@example",
    )

    with pytest.raises(RetentionRefused) as excinfo:
        redact_receipt(db, receipt, policy=policy, now=NOW)
    assert excinfo.value.reason == "legal_hold"
    assert not is_redacted(receipt)
    assert receipt.payload_json == PAYLOAD


def test_a_leased_receipt_is_refused_by_name(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Refusal 2 of 4. `processing` IS the inbox claim, and redacting a claimed
    receipt destroys the payload the claiming worker is at that moment reading."""
    receipt = _receipt(db, binding, state="processing")

    with pytest.raises(RetentionRefused) as excinfo:
        redact_receipt(db, receipt, policy=policy, now=NOW)
    assert excinfo.value.reason == "leased"
    assert receipt.payload_json == PAYLOAD


@pytest.mark.parametrize("state", ["verified", "retryable", "dead_letter"])
def test_an_unresolved_receipt_is_refused_by_name(
    state: str,
    db: Session,
    binding: CapabilityBinding,
    policy: RetentionPolicy,
    audit: _AuditSpy,
) -> None:
    """Refusal 3 of 4, and `dead_letter` belongs in it.

    `operations.replay_receipt` moves `dead_letter` and `retryable` back to
    `verified` for reprocessing. A replay of a redacted receipt is a replay with
    nothing to replay — the repair command would look available and do nothing.
    """
    receipt = _receipt(db, binding, state=state)

    with pytest.raises(RetentionRefused) as excinfo:
        redact_receipt(db, receipt, policy=policy, now=NOW)
    assert excinfo.value.reason == "unresolved"
    assert receipt.payload_json == PAYLOAD


def test_a_receipt_awaiting_reconciliation_is_refused_by_name(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Refusal 4 of 4. It may have half-landed, so a human still has to compare
    what happened against what the payload said — with the payload."""
    receipt = _receipt(db, binding, state="reconciliation_required")

    with pytest.raises(RetentionRefused) as excinfo:
        redact_receipt(db, receipt, policy=policy, now=NOW)
    assert excinfo.value.reason == "reconciliation_required"
    assert receipt.payload_json == PAYLOAD


def test_a_receipt_inside_its_period_is_refused(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    receipt = _receipt(db, binding, received_at=YESTERDAY)
    with pytest.raises(RetentionRefused) as excinfo:
        redact_receipt(db, receipt, policy=policy, now=NOW)
    assert excinfo.value.reason == "not_expired"


def test_an_unknown_future_state_is_refused_rather_than_assumed_safe(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy
) -> None:
    """A terminal state added upstream must opt IN to ageing, not inherit it.

    The state is set WITHOUT flushing, because `ck_inbox_receipts_state` now
    refuses an unrecognised value outright — a stronger guarantee than this
    test asks for, and one that arrived after it was written. That constraint
    protects the table; this protects the classifier, which is what would
    decide the question on the day a new state IS added to the constraint. If
    `classify_receipt` treated anything it did not recognise as resolved, that
    future state would silently start ageing out message bodies.
    """
    receipt = _receipt(db, binding)
    with db.no_autoflush:
        receipt.state = "archived_by_some_later_slice"
        verdict = classify_receipt(receipt, policy=policy, now=NOW, held=False)
    db.expire(receipt)
    assert verdict == "unresolved"


def test_the_sweep_leaves_every_refused_receipt_untouched_and_counts_it(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """The four refusals again, through the batch entry point — because a sweep
    that refuses one at a time and a sweep that skips a whole batch are
    different bugs."""
    held = _receipt(db, binding, provider_event_id="wamid.HELD")
    place_legal_hold(
        db, held, policy=policy, reason="litigation hold", placed_by="ops@example"
    )
    leased = _receipt(db, binding, provider_event_id="wamid.LEASED", state="processing")
    unresolved = _receipt(
        db, binding, provider_event_id="wamid.STUCK", state="retryable"
    )
    reconciling = _receipt(
        db, binding, provider_event_id="wamid.RECON", state="reconciliation_required"
    )
    eligible = _receipt(db, binding, provider_event_id="wamid.OK")

    sweep = purge_expired_payloads(db, policy=policy, now=NOW)

    assert sweep.redacted == 1
    assert sweep.redacted_ids == (eligible.id,)
    assert sweep.refused_by_reason == {
        "legal_hold": 1,
        "leased": 1,
        "unresolved": 1,
        "reconciliation_required": 1,
    }
    assert sweep.refused_total == 4
    for survivor in (held, leased, unresolved, reconciling):
        assert survivor.payload_json == PAYLOAD, "an in-flight payload was destroyed"
        assert not is_redacted(survivor)
    assert is_redacted(eligible)


# ── Batched, idempotent, audited ────────────────────────────────────────────


def test_running_the_sweep_twice_changes_nothing_the_first_run_did_not(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Idempotence, stated as a byte comparison rather than as a count.

    The marker is what removes a row from the candidate set, so the second run
    must not so much as rewrite `redacted_at`.
    """
    receipt = _receipt(db, binding)
    first = purge_expired_payloads(db, policy=policy, now=NOW)
    snapshot = dict(receipt.payload_json or {})

    later = NOW + timedelta(hours=6)
    second = purge_expired_payloads(db, policy=policy, now=later)

    assert first.redacted == 1
    assert second.redacted == 0
    assert receipt.payload_json == snapshot


def test_a_sweep_that_did_nothing_writes_no_audit_event(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """A cleanup that audited its own no-ops would bury the runs that did
    something under a timer's worth of noise."""
    purge_expired_payloads(db, policy=policy, now=NOW)
    assert audit.events == []


def test_the_audit_record_names_what_was_destroyed_and_under_whose_authority(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    receipt = _receipt(db, binding)
    purge_expired_payloads(db, policy=policy, now=NOW)

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event["action"] == "retention.payloads.redacted"
    details = event["details"]
    assert isinstance(details, dict)
    assert details["receipt_ids"] == [str(receipt.id)]
    assert details["redacted"] == 1
    assert details["legal_policy_owner"] == TEST_LEGAL_OWNER
    assert details["retention_days"] == TEST_RETENTION_DAYS


def test_no_audit_detail_carries_a_provider_identifier_or_a_payload(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """An audit detail is read by more people, and shipped to more places, than
    the row it describes. Internal UUIDs identify a receipt without carrying a
    provider's subscriber-linked identifier into a log pipeline."""
    receipt = _receipt(db, binding)
    place_legal_hold(
        db, receipt, policy=policy, reason="regulator", placed_by="ops@example"
    )
    purge_expired_payloads(db, policy=policy, now=NOW)

    assert audit.events, "the sweep must have recorded its refusal"
    rendered = repr(audit.events)
    for forbidden in (
        "wamid.EVENT-1",
        "+2348012345678",
        "when is my router due",
        "sha256=deadbeef",
        "TCK-4471",
    ):
        assert forbidden not in rendered, (
            f"{forbidden!r} reached an audit detail. Provider event ids, message "
            "content and signatures are exactly what must not leave the row"
        )


def test_the_sweep_is_batched_and_takes_the_oldest_first(
    db: Session, binding: CapabilityBinding, audit: _AuditSpy
) -> None:
    """A first run against years of history is a series of short transactions,
    not one lock held for an hour — and if it cannot finish, it should have
    retired the OLDEST content."""
    batched = RetentionPolicy(
        payload_retention_days=TEST_RETENTION_DAYS,
        legal_policy_owner=TEST_LEGAL_OWNER,
        batch_size=2,
    )
    receipts = [
        _receipt(
            db,
            binding,
            provider_event_id=f"wamid.{index}",
            received_at=LONG_AGO + timedelta(days=index),
        )
        for index in range(5)
    ]

    sweep = purge_expired_payloads(db, policy=batched, now=NOW)

    assert sweep.redacted == 2
    assert sweep.redacted_ids == (receipts[0].id, receipts[1].id)
    assert [is_redacted(r) for r in receipts] == [True, True, False, False, False]


# ── Legal hold lifecycle ────────────────────────────────────────────────────


def test_placing_a_hold_twice_returns_the_same_hold(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """A duplicate instruction during an incident is not an error. Refusing it
    invites someone to release the first hold to "fix" the second."""
    receipt = _receipt(db, binding)
    first = place_legal_hold(
        db, receipt, policy=policy, reason="regulator", placed_by="ops@example"
    )
    second = place_legal_hold(
        db, receipt, policy=policy, reason="regulator again", placed_by="other@example"
    )
    assert first.id == second.id
    assert first.reason == "regulator"


def test_a_hold_requires_a_stated_reason(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    receipt = _receipt(db, binding)
    with pytest.raises(ValueError, match="stated reason"):
        place_legal_hold(db, receipt, policy=policy, reason="   ", placed_by="ops")


def test_a_hold_records_the_accountable_owner_at_placement(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """Copied, not looked up. The question is who owned this decision WHEN it
    was made, which today's configuration cannot answer."""
    receipt = _receipt(db, binding)
    hold = place_legal_hold(
        db, receipt, policy=policy, reason="regulator", placed_by="ops@example"
    )
    assert hold.policy_owner == TEST_LEGAL_OWNER
    assert hold.released_at is None


def test_releasing_a_hold_keeps_the_record_and_re_enables_ageing(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    """The row survives its release: "was this ever held, and by whom?" is asked
    after the hold is lifted, not during."""
    receipt = _receipt(db, binding)
    hold = place_legal_hold(
        db, receipt, policy=policy, reason="regulator", placed_by="ops@example"
    )
    release_legal_hold(
        db, hold, released_by="legal@example", reason="matter DM-2026-118 closed"
    )
    db.flush()

    assert active_hold_for(db, receipt.id) is None
    assert hold.released_by == "legal@example"
    assert hold.release_reason == "matter DM-2026-118 closed"

    redact_receipt(db, receipt, policy=policy, now=NOW)
    assert is_redacted(receipt)


def test_releasing_a_released_hold_is_refused(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    receipt = _receipt(db, binding)
    hold = place_legal_hold(
        db, receipt, policy=policy, reason="regulator", placed_by="ops@example"
    )
    release_legal_hold(db, hold, released_by="legal@example", reason="closed")
    with pytest.raises(RetentionRefused):
        release_legal_hold(db, hold, released_by="legal@example", reason="closed again")


# ── Backlog is derived, never stored ────────────────────────────────────────


def test_a_quiet_platform_has_no_backlog(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy
) -> None:
    backlog = retention_backlog(db, policy=policy, now=NOW)
    assert backlog.expired_with_payload == 0
    assert backlog.oldest_expired_age_seconds is None
    assert backlog.needs_attention is False


def test_the_backlog_reports_an_age_because_a_count_hides_one_stuck_row(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    _receipt(db, binding, provider_event_id="wamid.OLD", received_at=LONG_AGO)
    _receipt(
        db,
        binding,
        provider_event_id="wamid.STUCK",
        received_at=LONG_AGO - timedelta(days=300),
        state="retryable",
    )

    backlog = retention_backlog(db, policy=policy, now=NOW)

    assert backlog.expired_with_payload == 2
    assert backlog.eligible_now == 1
    assert backlog.refused_by_reason == {"unresolved": 1}
    assert backlog.oldest_expired_age_seconds == int(
        (NOW - (LONG_AGO - timedelta(days=300))).total_seconds()
    )
    assert backlog.needs_attention is True


def test_the_backlog_falls_to_zero_once_the_sweep_has_run(
    db: Session, binding: CapabilityBinding, policy: RetentionPolicy, audit: _AuditSpy
) -> None:
    _receipt(db, binding)
    purge_expired_payloads(db, policy=policy, now=NOW)
    backlog = retention_backlog(db, policy=policy, now=NOW)
    assert backlog.expired_with_payload == 0
    assert backlog.active_legal_holds == 0
