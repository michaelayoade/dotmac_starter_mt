from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from dotmac_connector_meta_social import MANIFEST, PLUGIN, __version__
from dotmac_connector_meta_social.plugin import (
    CAPABILITY_ID,
    WEBHOOK_SIGNING_PREVIOUS_SECRET,
    WEBHOOK_SIGNING_SECRET,
    WEBHOOK_VERIFY_TOKEN,
    PayloadInvalid,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import (
    ConnectorMode,
    InboundDisposition,
    IngressRequest,
    VerificationResult,
)

PRIMARY = "primary-signing-material"
PREVIOUS = "previous-signing-material"
VERIFY = "subscription-verify-material"


def _handler():
    return PLUGIN.ingress_handler_for(CAPABILITY_ID)


def _request(
    payload: object,
    *,
    secret: str = PRIMARY,
    header_name: str = "X-Hub-Signature-256",
) -> IngressRequest:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return IngressRequest(raw_body=raw, headers={header_name: signature})


def _secrets() -> dict[str, str]:
    return {
        WEBHOOK_SIGNING_SECRET: PRIMARY,
        WEBHOOK_SIGNING_PREVIOUS_SECRET: PREVIOUS,
        WEBHOOK_VERIFY_TOKEN: VERIFY,
    }


def test_manifest_is_the_versioned_ingress_runtime_contract() -> None:
    assert MANIFEST.connector_key == "meta_social"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.capability_ids == {CAPABILITY_ID}
    assert MANIFEST.spi_range.minimum.minor == 3
    assert PLUGIN.modes == frozenset({ConnectorMode.INGRESS})
    assert tuple(binding.name for binding in MANIFEST.secret_bindings or ()) == (
        WEBHOOK_SIGNING_SECRET,
        WEBHOOK_SIGNING_PREVIOUS_SECRET,
        WEBHOOK_VERIFY_TOKEN,
    )
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == ()
    assert_plugin_conforms(PLUGIN)


def test_connection_validation_requires_only_declared_material() -> None:
    assert PLUGIN.validate_connection(config={}, secrets=_secrets()) == ()
    assert (
        PLUGIN.validate_connection(
            config={}, secrets={WEBHOOK_SIGNING_SECRET: PRIMARY}
        )[0].code
        == "required_material_unavailable"
    )


def test_challenge_echoes_only_a_matching_subscription_request() -> None:
    handler = _handler()
    request = IngressRequest(
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY,
            "hub.challenge": "challenge-123",
        }
    )

    answer = handler.challenge(request, config={}, secrets=_secrets())

    assert answer is not None
    assert answer.body == b"challenge-123"
    assert answer.media_type == "text/plain"
    refused = IngressRequest(
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-123",
        }
    )
    assert handler.challenge(refused, config={}, secrets=_secrets()) is None


def test_verification_covers_exact_bytes_and_reports_rotation_position() -> None:
    handler = _handler()
    request = _request({"object": "page", "entry": []}, secret=PREVIOUS)

    result = handler.verify(request, config={}, secrets=_secrets())

    assert result == VerificationResult(
        accepted=True,
        matched_secret_positions=(1,),
    )
    changed = IngressRequest(
        raw_body=request.raw_body + b" ",
        headers=request.headers,
    )
    assert handler.verify(changed, config={}, secrets=_secrets()) == (
        VerificationResult(accepted=False)
    )


def test_facebook_and_instagram_messages_are_independent_provider_events() -> None:
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page-1",
                "messaging": [
                    {
                        "sender": {"id": "fb-user-1"},
                        "timestamp": 1783670400000,
                        "message": {"mid": "m_fb_1", "text": "Hello support"},
                    },
                    {
                        "sender": {"id": "fb-user-2"},
                        "timestamp": 1783670401000,
                        "message": {
                            "mid": "m_fb_2",
                            "attachments": [
                                {
                                    "type": "image",
                                    "payload": {
                                        "url": "https://lookaside.fbsbx.com/image-1"
                                    },
                                }
                            ],
                        },
                    },
                ],
            }
        ],
    }

    events, acknowledgement = _handler().normalize(_request(payload), config={})

    assert [event.provider_event_id for event in events] == ["m_fb_1", "m_fb_2"]
    assert all(event.event_type == "meta.message.received.v1" for event in events)
    first = events[0].payload
    assert first["provider"] == "meta_cloud_api"
    assert first["provider_account_scope"] == "page-1"
    assert first["channel"] == "facebook_messenger"
    assert first["message"] == {
        "contact_address": "fb-user-1",
        "body": "Hello support",
        "external_message_id": "m_fb_1",
        "provider_account_id": "page-1",
        "external_account_id": "page-1",
        "page_id": "page-1",
        "attachments": [],
    }
    second_attachment = events[1].payload["message"]["attachments"][0]
    assert second_attachment == {
        "asset_type": "image",
        "source_url": "https://lookaside.fbsbx.com/image-1",
        "download_status": "remote_available",
    }
    assert acknowledgement is not None
    assert acknowledgement.body == b'{"status":"ok"}'

    instagram = {
        "object": "instagram",
        "entry": [
            {
                "id": "ig-1",
                "messaging": [
                    {
                        "sender": {"id": "ig-user-1"},
                        "timestamp": 1783670500000,
                        "message": {"mid": "m_ig_1", "text": "Account help"},
                    }
                ],
            }
        ],
    }
    instagram_events, _ = _handler().normalize(_request(instagram), config={})
    message = instagram_events[0].payload["message"]
    assert instagram_events[0].payload["channel"] == "instagram_dm"
    assert message["instagram_account_id"] == "ig-1"
    assert "page_id" not in message


@pytest.mark.parametrize(
    ("object_name", "field", "value", "channel", "thread"),
    [
        (
            "page",
            "feed",
            {
                "item": "comment",
                "post_id": "page-1_987",
                "comment_id": "comment-1",
                "parent_id": "page-1_987",
                "message": "Please check this area",
                "created_time": 1783670600,
                "from": {"id": "fb-user-1", "name": "Public Customer"},
            },
            "facebook_comment",
            "facebook_comment:page-1_987",
        ),
        (
            "instagram",
            "comments",
            {
                "id": "ig-comment-2",
                "text": "Replying under the post",
                "parent_id": "ig-comment-1",
                "media": {"id": "ig-media-1"},
                "created_time": 1783670700,
                "from": {"id": "ig-user-1", "username": "igcustomer"},
            },
            "instagram_comment",
            "instagram_comment:ig-media-1",
        ),
    ],
)
def test_comments_keep_provider_identity_and_post_threading(
    object_name: str,
    field: str,
    value: dict[str, object],
    channel: str,
    thread: str,
) -> None:
    payload = {
        "object": object_name,
        "entry": [
            {
                "id": "page-1" if object_name == "page" else "ig-1",
                "changes": [{"field": field, "value": value}],
            }
        ],
    }

    events, _ = _handler().normalize(_request(payload), config={})

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "meta.comment.received.v1"
    assert event.payload["channel"] == channel
    assert event.payload["message"]["external_thread_id"] == thread
    assert event.provider_event_id == event.payload["message"]["external_message_id"]


def test_event_identity_is_not_the_request_digest() -> None:
    item = {
        "sender": {"id": "fb-user-1"},
        "timestamp": 1783670400000,
        "message": {"mid": "m_stable", "text": "Hello"},
    }
    first = {"object": "page", "entry": [{"id": "page-1", "messaging": [item]}]}
    second = {
        "object": "page",
        "entry": [
            {"id": "page-1", "messaging": [item]},
            {"id": "page-2", "messaging": []},
        ],
    }

    first_events, _ = _handler().normalize(_request(first), config={})
    second_events, _ = _handler().normalize(_request(second), config={})

    assert first_events[0].provider_event_id == second_events[0].provider_event_id
    assert first_events[0].provider_event_id == "m_stable"


def test_echoes_are_durable_transport_evidence_but_not_product_observations() -> None:
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page-1",
                "messaging": [
                    {
                        "sender": {"id": "page-1"},
                        "timestamp": 1783670400000,
                        "message": {
                            "mid": "m_echo",
                            "text": "sent by us",
                            "is_echo": True,
                        },
                    }
                ],
            }
        ],
    }

    events, _ = _handler().normalize(_request(payload), config={})

    assert len(events) == 1
    assert events[0].provider_event_id == "m_echo"
    assert events[0].disposition is InboundDisposition.RECORD_ONLY
    assert events[0].payload["transport_evidence"]["reason_code"] == "message_echo"


def test_duplicate_identity_in_one_batch_is_refused_before_recording() -> None:
    event = {
        "sender": {"id": "fb-user-1"},
        "timestamp": 1783670400000,
        "message": {"mid": "m_duplicate", "text": "Hello"},
    }
    payload = {
        "object": "page",
        "entry": [{"id": "page-1", "messaging": [event, event]}],
    }

    with pytest.raises(PayloadInvalid, match="duplicate event identities"):
        _handler().normalize(_request(payload), config={})


def test_invalid_json_and_unknown_object_fail_closed() -> None:
    with pytest.raises(PayloadInvalid, match="JSON object"):
        _handler().normalize(IngressRequest(raw_body=b"not-json"), config={})
    with pytest.raises(PayloadInvalid, match="object unsupported"):
        _handler().normalize(_request({"object": "unknown", "entry": []}), config={})
