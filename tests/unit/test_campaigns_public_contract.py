"""Typed edge contracts must not leak campaign ORM or provider vocabulary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from dotmac_campaigns import (
    ContractError,
    DeliveryIntentView,
    ObservationKind,
    ResponseFact,
    UnsubscribeRequest,
    UnsubscribeResult,
    delivery_intent,
    response_facts,
)


def test_unsubscribe_request_validates_and_fingerprints_the_edge_fact() -> None:
    request = UnsubscribeRequest(
        channel=" EMAIL ",
        address="person@example.com",
        source_owner="public.unsubscribe",
        source_event_id="request-1",
        source_fingerprint="a" * 64,
        requested_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert request.channel == "email"
    assert request.fingerprint_payload()["address"] == "person@example.com"

    with pytest.raises(ContractError, match="source_fingerprint"):
        UnsubscribeRequest(
            channel="email",
            address="person@example.com",
            source_owner="public.unsubscribe",
            source_event_id="request-1",
            source_fingerprint="provider-id-is-not-a-digest",
            requested_at=datetime(2026, 8, 18, tzinfo=UTC),
        )


def test_public_result_and_read_types_are_provider_neutral() -> None:
    request_id = uuid.uuid4()
    result = UnsubscribeResult(request_id=request_id, replayed=True)
    response = ResponseFact(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        recipient_step_id=uuid.uuid4(),
        observation_id=uuid.uuid4(),
        kind=ObservationKind.REPLY,
        correlation_ref="opaque:conversation-1",
        fingerprint_sha256="b" * 64,
        occurred_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert result.request_id == request_id
    assert response.kind == ObservationKind.REPLY
    assert not hasattr(response, "lead_id")
    assert not hasattr(response, "provider_campaign_id")
    assert callable(response_facts)
    assert callable(delivery_intent)
    assert DeliveryIntentView.__module__ == "dotmac_campaigns.contracts"
