from __future__ import annotations

from dataclasses import replace

import pytest
from dotmac_app_sync import (
    AuthenticatedPeer,
    DuplicateContract,
    EnvelopeInvalid,
    PeerMismatch,
    SyncAcceptance,
    SyncContract,
    SyncContractRegistry,
    SyncEnvelope,
    SyncReceipt,
    UnknownContract,
    deliver_authenticated,
    encode_envelope,
    fingerprint_for,
    idempotency_key_for,
)


def _contract(
    capability_id: str, owner: str, required: tuple[str, ...]
) -> SyncContract:
    return SyncContract(
        capability_id=capability_id,
        owner_application=owner,
        summary="A destination-owned observation contract.",
        payload_schema={
            "type": "object",
            "additionalProperties": False,
            "required": list(required),
            "properties": {name: {"type": "string"} for name in required},
        },
    )


CONTRACTS = (
    _contract(
        "subscriber.billing.observation.v1",
        "dotmac_erp",
        ("subscriber_ref", "service_ref", "billing_state"),
    ),
    _contract(
        "payment.ledger.observation.v1",
        "dotmac_sub",
        ("payment_ref", "ledger_state"),
    ),
    _contract(
        "learning.eligibility.observation.v1",
        "dotmac_academy_app",
        ("person_ref", "eligibility_basis"),
    ),
    _contract(
        "learning.completion.observation.v1",
        "dotmac_erp",
        ("person_ref", "learning_ref", "completion_state"),
    ),
)


def _envelope(
    capability_id: str,
    source: str,
    payload: dict[str, object],
    *,
    event_id: str = "event-1",
) -> SyncEnvelope:
    return SyncEnvelope(
        capability_id=capability_id,
        source_application=source,
        source_event_id=event_id,
        source_scope_kind="tenant",
        source_scope_ref="tenant-opaque-1",
        subject_ref="subject-opaque-1",
        occurred_at="2026-08-21T10:00:00Z",
        correlation_id="correlation-1",
        payload=payload,
    )


class _AtomicReceiver:
    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.calls = 0

    def receive(
        self,
        *,
        envelope: SyncEnvelope,
        idempotency_key: str,
        fingerprint: str,
    ) -> SyncReceipt:
        self.calls += 1
        existing = self.seen.get(idempotency_key)
        if existing is not None:
            assert existing == fingerprint
            return SyncReceipt(
                acceptance=SyncAcceptance.ALREADY_APPLIED,
                destination_ref="local-1",
            )
        self.seen[idempotency_key] = fingerprint
        return SyncReceipt(
            acceptance=SyncAcceptance.ACCEPTED,
            destination_ref="local-1",
        )


@pytest.mark.parametrize(
    ("envelope", "owner"),
    [
        (
            _envelope(
                "subscriber.billing.observation.v1",
                "dotmac_sub",
                {
                    "subscriber_ref": "sub-1",
                    "service_ref": "svc-1",
                    "billing_state": "observed",
                },
            ),
            "dotmac_erp",
        ),
        (
            _envelope(
                "payment.ledger.observation.v1",
                "dotmac_erp",
                {"payment_ref": "pay-1", "ledger_state": "posted"},
            ),
            "dotmac_sub",
        ),
        (
            _envelope(
                "learning.eligibility.observation.v1",
                "dotmac_erp",
                {"person_ref": "person-1", "eligibility_basis": "employment"},
            ),
            "dotmac_academy_app",
        ),
        (
            _envelope(
                "learning.completion.observation.v1",
                "dotmac_academy_app",
                {
                    "person_ref": "person-1",
                    "learning_ref": "course-1",
                    "completion_state": "completed",
                },
            ),
            "dotmac_erp",
        ),
    ],
)
def test_four_first_flows_use_one_authenticated_idempotent_contract(
    envelope: SyncEnvelope, owner: str
) -> None:
    registry = SyncContractRegistry(CONTRACTS)
    receiver = _AtomicReceiver()
    raw = encode_envelope(envelope)
    first = deliver_authenticated(
        raw,
        peer=AuthenticatedPeer(application=envelope.source_application),
        expected_owner=owner,
        registry=registry,
        receiver=receiver,
    )
    duplicate = deliver_authenticated(
        raw,
        peer=AuthenticatedPeer(application=envelope.source_application),
        expected_owner=owner,
        registry=registry,
        receiver=receiver,
    )
    assert first.acceptance is SyncAcceptance.ACCEPTED
    assert duplicate.acceptance is SyncAcceptance.ALREADY_APPLIED
    assert receiver.calls == 2


def test_authenticated_peer_must_equal_the_claimed_source() -> None:
    envelope = _envelope(
        "payment.ledger.observation.v1",
        "dotmac_erp",
        {"payment_ref": "pay-1", "ledger_state": "posted"},
    )
    with pytest.raises(PeerMismatch):
        deliver_authenticated(
            encode_envelope(envelope),
            peer=AuthenticatedPeer(application="dotmac_sub"),
            expected_owner="dotmac_sub",
            registry=SyncContractRegistry(CONTRACTS),
            receiver=_AtomicReceiver(),
        )


def test_wrong_owner_fails_closed_before_the_receiver() -> None:
    receiver = _AtomicReceiver()
    envelope = _envelope(
        "learning.eligibility.observation.v1",
        "dotmac_erp",
        {"person_ref": "person-1"},
    )
    with pytest.raises(UnknownContract):
        deliver_authenticated(
            encode_envelope(envelope),
            peer=AuthenticatedPeer(application="dotmac_erp"),
            expected_owner="dotmac_erp",
            registry=SyncContractRegistry(CONTRACTS),
            receiver=receiver,
        )
    assert receiver.calls == 0


def test_payload_schema_fails_closed_before_the_receiver() -> None:
    receiver = _AtomicReceiver()
    envelope = _envelope(
        "learning.eligibility.observation.v1",
        "dotmac_erp",
        {"person_ref": "person-1"},
    )
    with pytest.raises(EnvelopeInvalid):
        deliver_authenticated(
            encode_envelope(envelope),
            peer=AuthenticatedPeer(application="dotmac_erp"),
            expected_owner="dotmac_academy_app",
            registry=SyncContractRegistry(CONTRACTS),
            receiver=receiver,
        )
    assert receiver.calls == 0


def test_duplicate_contracts_are_refused_and_idempotency_is_payload_independent() -> (
    None
):
    with pytest.raises(DuplicateContract):
        SyncContractRegistry((CONTRACTS[0], replace(CONTRACTS[0], summary="other")))
    first = _envelope(
        CONTRACTS[0].capability_id,
        "dotmac_sub",
        {"subscriber_ref": "s", "service_ref": "x", "billing_state": "one"},
    )
    changed = replace(
        first,
        payload={"subscriber_ref": "s", "service_ref": "x", "billing_state": "two"},
    )
    assert idempotency_key_for(first) == idempotency_key_for(changed)
    assert fingerprint_for(first) != fingerprint_for(changed)


def test_malformed_wire_types_are_closed_contract_errors() -> None:
    raw = encode_envelope(
        _envelope(
            "payment.ledger.observation.v1",
            "dotmac_erp",
            {"payment_ref": "pay-1", "ledger_state": "posted"},
        )
    ).replace(b'"capability_id":"payment.ledger.observation.v1"', b'"capability_id":1')
    with pytest.raises(EnvelopeInvalid):
        deliver_authenticated(
            raw,
            peer=AuthenticatedPeer(application="dotmac_erp"),
            expected_owner="dotmac_sub",
            registry=SyncContractRegistry(CONTRACTS),
            receiver=_AtomicReceiver(),
        )
