"""Outbound content ages out without erasing dedupe or provider evidence."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    REDACTION_MARKER,
    CapabilityBinding,
    ConnectorInstallation,
    DeliveryAttempt,
    DeliveryLegalHold,
    RetentionPolicy,
    RetentionRefused,
    classify_delivery,
    enqueue_delivery,
    is_delivery_redacted,
    place_delivery_legal_hold,
    purge_expired_delivery_payloads,
    redact_delivery,
    release_delivery_legal_hold,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=60)
POLICY = RetentionPolicy(
    payload_retention_days=30,
    replay_evidence_retention_days=180,
    legal_policy_owner="test-legal-owner",
)
PAYLOAD = {
    "action": "send_text",
    "params": {"recipient": "+2348012345678", "body": "service restored"},
}


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
def delivery(db: Session) -> DeliveryAttempt:
    installation = ConnectorInstallation(
        id=uuid.uuid4(),
        connector_key="meta_whatsapp",
        connector_version="0.1.0a3",
        spi_range=">=1.3,<2.0",
        manifest_digest="d" * 64,
        name="primary",
        state="enabled",
    )
    db.add(installation)
    db.flush()
    binding = CapabilityBinding(
        id=uuid.uuid4(),
        installation_id=installation.id,
        capability_id="messaging.send.v1",
        state="enabled",
    )
    db.add(binding)
    db.flush()
    queued, _ = enqueue_delivery(
        db,
        installation_id=installation.id,
        capability_binding_id=binding.id,
        event_type="messaging.send.requested.v1",
        idempotency_key="sub:message:1",
        payload=PAYLOAD,
    )
    queued.state = "delivered"
    queued.created_at = OLD
    queued.delivered_at = OLD
    queued.provider_reference = "wamid.outbound-1"
    queued.provider_status_code = 200
    queued.result_json = {"accepted_reference": "product-result-1"}
    db.flush()
    return queued


def test_redaction_keeps_dedupe_and_provider_evidence(
    db: Session, delivery: DeliveryAttempt
) -> None:
    before = {
        "id": delivery.id,
        "idempotency_key": delivery.idempotency_key,
        "payload_digest": delivery.payload_digest,
        "provider_reference": delivery.provider_reference,
        "provider_status_code": delivery.provider_status_code,
        "state": delivery.state,
    }

    redact_delivery(db, delivery, policy=POLICY, now=NOW)

    assert is_delivery_redacted(delivery)
    assert delivery.payload_json[REDACTION_MARKER]["key_count"] == len(PAYLOAD)
    assert delivery.result_json is None, "normalized result content must age out too"
    assert {
        "id": delivery.id,
        "idempotency_key": delivery.idempotency_key,
        "payload_digest": delivery.payload_digest,
        "provider_reference": delivery.provider_reference,
        "provider_status_code": delivery.provider_status_code,
        "state": delivery.state,
    } == before


def test_redacted_delivery_still_deduplicates_product_replay(
    db: Session, delivery: DeliveryAttempt
) -> None:
    redact_delivery(db, delivery, policy=POLICY, now=NOW)

    replay, created = enqueue_delivery(
        db,
        installation_id=delivery.installation_id,
        capability_binding_id=delivery.capability_binding_id,
        event_type=delivery.event_type,
        idempotency_key=delivery.idempotency_key,
        payload=PAYLOAD,
    )

    assert created is False
    assert replay.id == delivery.id


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("pending", "unresolved"),
        ("in_flight", "leased"),
        ("retryable", "unresolved"),
        ("dead_letter", "unresolved"),
        ("reconciliation_required", "reconciliation_required"),
    ],
)
def test_only_delivered_content_can_be_redacted(
    delivery: DeliveryAttempt, state: str, reason: str
) -> None:
    delivery.state = state
    assert classify_delivery(delivery, policy=POLICY, now=NOW, held=False) == reason


def test_legal_hold_wins_over_an_otherwise_eligible_delivery(
    db: Session, delivery: DeliveryAttempt, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_integration.retention as retention_module

    monkeypatch.setattr(retention_module, "record_operation", lambda *a, **k: None)
    hold = place_delivery_legal_hold(
        db,
        delivery,
        policy=POLICY,
        reason="customer dispute",
        placed_by="legal-user",
    )

    with pytest.raises(RetentionRefused, match="legal hold"):
        redact_delivery(db, delivery, policy=POLICY, now=NOW)

    release_delivery_legal_hold(
        db, hold, released_by="legal-user", reason="matter closed"
    )
    redact_delivery(db, delivery, policy=POLICY, now=NOW)
    assert is_delivery_redacted(delivery)


def test_sweep_is_bounded_and_idempotent(
    db: Session, delivery: DeliveryAttempt, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dotmac_integration.retention as retention_module

    monkeypatch.setattr(retention_module, "record_operation", lambda *a, **k: None)
    first = purge_expired_delivery_payloads(db, policy=POLICY, now=NOW)
    second = purge_expired_delivery_payloads(db, policy=POLICY, now=NOW)

    assert first.redacted == 1
    assert first.redacted_ids == (delivery.id,)
    assert second.redacted == 0
