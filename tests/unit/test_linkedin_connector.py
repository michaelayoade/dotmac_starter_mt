from __future__ import annotations

import hashlib
import hmac
import json

from dotmac_connector_linkedin import MANIFEST, __version__
from dotmac_connector_linkedin.plugin import (
    CLIENT_SECRET,
    LEAD_CAPABILITY,
    SIGNATURE_HEADER,
    SOCIAL_CAPABILITY,
    LinkedInPlugin,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import ConnectorMode, IngressRequest


def _signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), b"hmacsha256=" + body, hashlib.sha256).hexdigest()


def test_manifest_declares_two_ingress_capabilities_and_deny_all_egress() -> None:
    plugin = LinkedInPlugin()
    assert MANIFEST.connector_key == "linkedin"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.capability_ids == {SOCIAL_CAPABILITY, LEAD_CAPABILITY}
    assert plugin.modes == frozenset({ConnectorMode.INGRESS})
    assert tuple(item.name for item in MANIFEST.secret_bindings or ()) == (
        CLIENT_SECRET,
    )
    assert MANIFEST.egress is not None and MANIFEST.egress.hosts == ()
    assert_plugin_conforms(plugin)


def test_challenge_echoes_the_code_and_documented_hmac_as_json() -> None:
    handler = LinkedInPlugin().ingress_handler_for(SOCIAL_CAPABILITY)
    request = IngressRequest(params={"challengeCode": "abc-123"})
    acknowledgement = handler.challenge(
        request,
        config={},
        secrets={CLIENT_SECRET: "held-client-secret"},
    )
    assert acknowledgement is not None
    assert acknowledgement.media_type == "application/json"
    assert json.loads(acknowledgement.body) == {
        "challengeCode": "abc-123",
        "challengeResponse": hmac.new(
            b"held-client-secret", b"abc-123", hashlib.sha256
        ).hexdigest(),
    }


def test_post_verification_uses_exact_raw_bytes_and_x_li_signature() -> None:
    raw = b'{"notificationId":"n-1","action":"LIKE"}'
    handler = LinkedInPlugin().ingress_handler_for(SOCIAL_CAPABILITY)
    accepted = handler.verify(
        IngressRequest(
            raw_body=raw,
            headers={SIGNATURE_HEADER: _signature(raw, "held-client-secret")},
        ),
        config={},
        secrets={CLIENT_SECRET: "held-client-secret"},
    )
    changed = handler.verify(
        IngressRequest(
            raw_body=raw + b" ",
            headers={SIGNATURE_HEADER: _signature(raw, "held-client-secret")},
        ),
        config={},
        secrets={CLIENT_SECRET: "held-client-secret"},
    )
    prefixed_header = handler.verify(
        IngressRequest(
            raw_body=raw,
            headers={
                SIGNATURE_HEADER: f"hmacsha256={_signature(raw, 'held-client-secret')}"
            },
        ),
        config={},
        secrets={CLIENT_SECRET: "held-client-secret"},
    )
    assert accepted is True
    assert changed is False
    assert prefixed_header is False


def test_social_action_is_a_provider_fact_not_a_product_decision() -> None:
    raw = json.dumps(
        {
            "type": "ORGANIZATION_SOCIAL_ACTION_NOTIFICATIONS",
            "notifications": [
                {
                    "notificationId": "n-1",
                    "action": "COMMENT",
                    "organizationalEntity": "urn:li:organization:123",
                    "sourcePost": "urn:li:share:456",
                    "generatedActivity": "urn:li:comment:789",
                    "lastModifiedAt": 1787212200000,
                }
            ],
        }
    ).encode()
    handler = LinkedInPlugin().ingress_handler_for(SOCIAL_CAPABILITY)
    events, acknowledgement = handler.normalize(IngressRequest(raw_body=raw), config={})
    assert acknowledgement is not None and acknowledgement.body == b""
    assert len(events) == 1
    assert events[0].provider_event_id == "n-1"
    assert events[0].event_type == SOCIAL_CAPABILITY
    assert events[0].payload["provider_action"] == "COMMENT"
    assert events[0].payload["provider_organization"] == "urn:li:organization:123"
    assert "ticket" not in events[0].payload
    assert "lead_status" not in events[0].payload


def test_lead_sync_uses_response_urn_plus_occurrence_for_identity() -> None:
    handler = LinkedInPlugin().ingress_handler_for(LEAD_CAPABILITY)
    first = {
        "type": "LEAD_ACTION",
        "leadAction": "CREATED",
        "leadGenFormResponse": "urn:li:leadGenFormResponse:1",
        "occurredAt": 1787212200000,
    }
    second = {**first, "leadAction": "DELETED", "occurredAt": 1787212300000}
    first_events, _ = handler.normalize(
        IngressRequest(raw_body=json.dumps(first).encode()), config={}
    )
    second_events, _ = handler.normalize(
        IngressRequest(raw_body=json.dumps(second).encode()), config={}
    )
    assert first_events[0].provider_event_id == (
        "urn:li:leadGenFormResponse:1:1787212200000"
    )
    assert second_events[0].provider_event_id == (
        "urn:li:leadGenFormResponse:1:1787212300000"
    )
    assert first_events[0].payload["provider_action"] == "CREATED"
    assert second_events[0].payload["provider_action"] == "DELETED"
