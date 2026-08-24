from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from dotmac_connector_meta_social import MANIFEST, PLUGIN, __version__
from dotmac_connector_meta_social.plugin import (
    CAPABILITY_ID,
    FACEBOOK_PAGE_ACCESS_TOKEN,
    INSTAGRAM_LOGIN_ACCESS_TOKEN,
    META_OAUTH_ACCESS_TOKEN,
    SEND_CAPABILITY_ID,
    WEBHOOK_SIGNING_PREVIOUS_SECRET,
    WEBHOOK_SIGNING_SECRET,
    WEBHOOK_VERIFY_TOKEN,
    MetaSocialConnector,
    PayloadInvalid,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.retry import OutcomeStatus, next_state
from dotmac_integration.spi import (
    ConnectorMode,
    DispatchRequest,
    InboundDisposition,
    IngressRequest,
    VerificationResult,
)

PRIMARY = "primary-signing-material"
PREVIOUS = "previous-signing-material"
VERIFY = "subscription-verify-material"
PAGE_ID = "page-account-1"
INSTAGRAM_ID = "instagram-account-1"
PAGE_MATERIAL = "held-page-material"
INSTAGRAM_MATERIAL = "held-instagram-material"
OAUTH_MATERIAL = "held-oauth-material"
DELIVERY_CONFIG: dict[str, object] = {
    "graph_api_version": "v23.0",
    "auth_mode": "individual",
    "facebook_page_id": PAGE_ID,
    "instagram_account_id": INSTAGRAM_ID,
    "timeout_seconds": 10,
}
OAUTH_CONFIG: dict[str, object] = {**DELIVERY_CONFIG, "auth_mode": "oauth"}
DELIVERY_SECRETS: dict[str, str] = {
    FACEBOOK_PAGE_ACCESS_TOKEN: PAGE_MATERIAL,
    INSTAGRAM_LOGIN_ACCESS_TOKEN: INSTAGRAM_MATERIAL,
    META_OAUTH_ACCESS_TOKEN: OAUTH_MATERIAL,
}


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


def test_manifest_is_the_versioned_two_mode_runtime_contract() -> None:
    assert MANIFEST.connector_key == "meta_social"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.capability_ids == {CAPABILITY_ID, SEND_CAPABILITY_ID}
    # SPI 1.4 is the floor `CapabilityDeclaration.modes` needs. Declaring the
    # mapping under an older range would let an engine call the ingress factory
    # for the send capability.
    assert MANIFEST.spi_range.minimum.minor == 4
    assert PLUGIN.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.DELIVERY})
    by_id = {
        declaration.capability_id: declaration for declaration in MANIFEST.capabilities
    }
    assert by_id[CAPABILITY_ID].modes == frozenset({ConnectorMode.INGRESS})
    assert by_id[SEND_CAPABILITY_ID].modes == frozenset({ConnectorMode.DELIVERY})
    assert tuple(binding.name for binding in MANIFEST.secret_bindings or ()) == (
        WEBHOOK_SIGNING_SECRET,
        WEBHOOK_SIGNING_PREVIOUS_SECRET,
        WEBHOOK_VERIFY_TOKEN,
        FACEBOOK_PAGE_ACCESS_TOKEN,
        INSTAGRAM_LOGIN_ACCESS_TOKEN,
        META_OAUTH_ACCESS_TOKEN,
    )
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == ("graph.facebook.com", "graph.instagram.com")
    assert_plugin_conforms(PLUGIN)


def test_the_outbound_capability_reuses_the_declared_send_name() -> None:
    """Not a new name for an existing meaning.

    `messaging.send.v1` is the id Sub already binds for these Meta accounts and
    the id the sibling WhatsApp connector already implements. A connector that
    minted `meta.social.send.v1` here would be undiscoverable to the product
    port that asks for the send capability, while looking perfectly correct in
    isolation.
    """
    assert SEND_CAPABILITY_ID == "messaging.send.v1"
    assert SEND_CAPABILITY_ID != CAPABILITY_ID


def test_connection_validation_requires_only_declared_material() -> None:
    assert PLUGIN.validate_connection(config={}, secrets=_secrets()) == ()
    assert (
        PLUGIN.validate_connection(
            config={}, secrets={WEBHOOK_SIGNING_SECRET: PRIMARY}
        )[0].code
        == "required_material_unavailable"
    )


def test_connection_validation_checks_delivery_material_once_bound() -> None:
    """An ingress-only installation stays valid; a send binding raises the bar.

    Delivery configuration is optional, so its absence must not fail a
    connection that never sends. Its PRESENCE is what pulls the credential and
    account checks in — otherwise a half-configured send binding validates
    clean and fails on the first real reply.
    """
    assert PLUGIN.validate_connection(config={}, secrets=_secrets()) == ()
    complete = PLUGIN.validate_connection(
        config=DELIVERY_CONFIG,
        secrets={**_secrets(), **DELIVERY_SECRETS},
    )
    assert complete == ()
    missing_material = PLUGIN.validate_connection(
        config=DELIVERY_CONFIG, secrets=_secrets()
    )
    assert missing_material[0].code == "delivery_configuration_invalid"
    missing_account = PLUGIN.validate_connection(
        config={**DELIVERY_CONFIG, "instagram_account_id": None},
        secrets={**_secrets(), **DELIVERY_SECRETS},
    )
    assert missing_account[0].code == "delivery_configuration_invalid"


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


# ── DELIVERY: the outbound slice ────────────────────────────────────────────


def _send(
    action: str,
    params: dict[str, object],
    *,
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    return DispatchRequest(
        capability_id=SEND_CAPABILITY_ID,
        event_type="messaging.send.requested.v1",
        payload={"action": action, "params": params},
        config=dict(DELIVERY_CONFIG if config is None else config),
        secrets=dict(DELIVERY_SECRETS),
        idempotency_key="product:reply:1",
    )


def _delivery(handler) -> MetaSocialConnector:
    return MetaSocialConnector(transport=httpx.MockTransport(handler))


def _accepts(body: dict[str, object], status: int = 200):
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    respond.seen = seen  # type: ignore[attr-defined]
    return respond


def _rejects(status: int, code: int | None = None, subcode: int | None = None):
    error: dict[str, object] = {
        # Prose the connector must never read: it quotes the held material and
        # the outbound content straight back at us.
        "message": f"{PAGE_MATERIAL} rejected: private customer content",
        "type": "OAuthException",
    }
    if code is not None:
        error["code"] = code
    if subcode is not None:
        error["error_subcode"] = subcode
    body = {"error": error}

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return respond


def _no_call(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError(f"the connector contacted {request.url} anyway")


def _direct_params(channel: str = "facebook_messenger") -> dict[str, object]:
    return {
        "channel": channel,
        "provider_account_id": (
            PAGE_ID if channel == "facebook_messenger" else INSTAGRAM_ID
        ),
        "recipient_id": "recipient-1",
        "body": "Hello",
    }


def test_facebook_messenger_send_matches_the_qualifying_sub_wire_shape() -> None:
    respond = _accepts({"message_id": "mid.fb-1", "recipient_id": "fb-user-1"})

    outcome = _delivery(respond).handler_for(SEND_CAPABILITY_ID)(
        _send(
            "send_direct_message",
            {
                "channel": "facebook_messenger",
                "provider_account_id": PAGE_ID,
                "recipient_id": "fb-user-1",
                "body": "Your line is back up.",
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "mid.fb-1"
    assert outcome.provider_status_code == 200
    request = respond.seen[0]
    assert str(request.url) == f"https://graph.facebook.com/v23.0/{PAGE_ID}/messages"
    assert json.loads(request.content) == {
        "recipient": {"id": "fb-user-1"},
        "message": {"text": "Your line is back up."},
        "messaging_type": "RESPONSE",
    }
    assert request.headers["authorization"] == f"Bearer {PAGE_MATERIAL}"


def test_instagram_direct_send_keeps_its_own_host_and_string_encoding() -> None:
    """Sub's two channels are not one endpoint with a different id.

    Instagram Login posts to `graph.instagram.com` as `me` and takes both
    objects as compact JSON STRINGS. Reusing the Messenger shape here produces
    a call that authenticates and then delivers nothing.
    """
    respond = _accepts({"message_id": "mid.ig-1", "recipient_id": "ig-user-1"})

    outcome = _delivery(respond).handler_for(SEND_CAPABILITY_ID)(
        _send(
            "send_direct_message",
            {
                "channel": "instagram_dm",
                "provider_account_id": INSTAGRAM_ID,
                "recipient_id": "ig-user-1",
                "body": "Thanks for reaching out.",
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    request = respond.seen[0]
    assert str(request.url) == "https://graph.instagram.com/v23.0/me/messages"
    assert json.loads(request.content) == {
        "recipient": '{"id":"ig-user-1"}',
        "message": '{"text":"Thanks for reaching out."}',
    }
    assert request.headers["authorization"] == f"Bearer {INSTAGRAM_MATERIAL}"


def test_shared_oauth_mode_moves_both_channels_onto_one_host_and_material() -> None:
    respond = _accepts({"message_id": "mid.oauth-1"})
    plugin = _delivery(respond)

    for channel, account in (
        ("facebook_messenger", PAGE_ID),
        ("instagram_dm", INSTAGRAM_ID),
    ):
        outcome = plugin.handler_for(SEND_CAPABILITY_ID)(
            _send(
                "send_direct_message",
                {
                    "channel": channel,
                    "provider_account_id": account,
                    "recipient_id": "user-1",
                    "body": "Hello",
                },
                config=OAUTH_CONFIG,
            )
        )
        assert outcome.status is OutcomeStatus.SUCCEEDED

    assert [str(request.url) for request in respond.seen] == [
        f"https://graph.facebook.com/v23.0/{PAGE_ID}/messages",
        f"https://graph.facebook.com/v23.0/{INSTAGRAM_ID}/messages",
    ]
    assert {request.headers["authorization"] for request in respond.seen} == {
        f"Bearer {OAUTH_MATERIAL}"
    }


@pytest.mark.parametrize(
    ("channel", "edge"),
    [("facebook_comment", "comments"), ("instagram_comment", "replies")],
)
def test_comment_replies_use_the_channels_own_graph_edge(
    channel: str, edge: str
) -> None:
    """`/comments` and `/replies` are not aliases.

    Facebook nests a reply on the parent comment's `comments` edge and
    Instagram on its `replies` edge. Sending either shape to the other endpoint
    fails in a way that reads like a provider problem, so the edge is asserted
    per channel rather than once.
    """
    respond = _accepts({"id": "comment-reply-1"})
    account = PAGE_ID if channel == "facebook_comment" else INSTAGRAM_ID

    outcome = _delivery(respond).handler_for(SEND_CAPABILITY_ID)(
        _send(
            "reply_to_comment",
            {
                "channel": channel,
                "provider_account_id": account,
                "parent_comment_id": "parent-comment-1",
                "body": "We have sent you a DM.",
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "comment-reply-1"
    request = respond.seen[0]
    assert str(request.url) == (
        f"https://graph.facebook.com/v23.0/parent-comment-1/{edge}"
    )
    assert json.loads(request.content) == {"message": "We have sent you a DM."}


@pytest.mark.parametrize("parent", [None, "", "   ", "not a comment id", 12345])
def test_a_missing_or_invalid_parent_comment_id_is_terminal_without_a_call(
    parent: object,
) -> None:
    """A reply with no addressable parent cannot become a retry.

    The transport asserts it was never reached: a command that cannot be
    translated must fail before I/O, or every malformed reply spends the
    engine's attempt budget rediscovering the same thing.
    """
    outcome = _delivery(_no_call).handler_for(SEND_CAPABILITY_ID)(
        _send(
            "reply_to_comment",
            {
                "channel": "facebook_comment",
                "provider_account_id": PAGE_ID,
                "parent_comment_id": parent,
                "body": "We have sent you a DM.",
            },
        )
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "parent_comment_id_required"
    assert next_state(outcome, attempt_count=1) == "dead_letter"


def test_a_parent_the_provider_cannot_load_is_terminal_too() -> None:
    """The same fault, discovered one layer later, gets the same answer."""
    outcome = _delivery(_rejects(400, code=100, subcode=33)).handler_for(
        SEND_CAPABILITY_ID
    )(
        _send(
            "reply_to_comment",
            {
                "channel": "instagram_comment",
                "provider_account_id": INSTAGRAM_ID,
                "parent_comment_id": "deleted-comment-1",
                "body": "Hello",
            },
        )
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "provider_object_not_found"
    assert next_state(outcome, attempt_count=1) == "dead_letter"


@pytest.mark.parametrize(
    ("status", "code", "subcode", "expected", "error_code"),
    [
        # Throttles: 4xx on the wire, "come back later" in meaning.
        (400, 4, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (403, 17, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (403, 32, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (400, 341, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (403, 613, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (429, None, None, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        # Graph's own transient failures.
        (500, 1, None, OutcomeStatus.RETRYABLE, "provider_transient_error"),
        (503, 2, None, OutcomeStatus.RETRYABLE, "provider_transient_error"),
        (502, None, None, OutcomeStatus.RETRYABLE, "provider_retryable_response"),
        # Policy refusals, the closed messaging window among them.
        (
            400,
            10,
            2018278,
            OutcomeStatus.TERMINAL,
            "provider_refused_by_messaging_policy",
        ),
        (
            400,
            10,
            2018108,
            OutcomeStatus.TERMINAL,
            "provider_refused_by_messaging_policy",
        ),
        (
            400,
            551,
            None,
            OutcomeStatus.TERMINAL,
            "provider_refused_by_messaging_policy",
        ),
        # Material no repeat of this call can fix.
        (401, 190, None, OutcomeStatus.TERMINAL, "provider_authorization_rejected"),
        (403, 102, None, OutcomeStatus.TERMINAL, "provider_authorization_rejected"),
        (403, 200, None, OutcomeStatus.TERMINAL, "provider_authorization_rejected"),
        (403, 299, None, OutcomeStatus.TERMINAL, "provider_authorization_rejected"),
        # Addressing faults.
        (400, 803, None, OutcomeStatus.TERMINAL, "provider_object_not_found"),
        (400, None, 33, OutcomeStatus.TERMINAL, "provider_object_not_found"),
        (400, 100, None, OutcomeStatus.TERMINAL, "provider_request_invalid"),
        # Anything else 4xx the table does not name.
        (400, None, None, OutcomeStatus.TERMINAL, "provider_rejected_message"),
        (404, 9999, None, OutcomeStatus.TERMINAL, "provider_rejected_message"),
    ],
)
def test_every_declared_error_class_maps_to_its_outcome(
    status: int,
    code: int | None,
    subcode: int | None,
    expected: OutcomeStatus,
    error_code: str,
) -> None:
    outcome = _delivery(_rejects(status, code, subcode)).handler_for(
        SEND_CAPABILITY_ID
    )(_send("send_direct_message", _direct_params()))

    assert outcome.status is expected
    assert outcome.error_code == error_code
    assert outcome.provider_status_code == status
    # Provider prose is diagnostic material, not evidence: nothing that could
    # carry held material or customer content is retained.
    assert PAGE_MATERIAL not in repr(outcome)
    assert outcome.error_detail is None


def test_a_throttle_under_a_4xx_is_retryable_rather_than_dead_lettered() -> None:
    """The sensitivity proof for reading the Graph code BEFORE the HTTP status.

    Delete the code table and this exact response — HTTP 400 carrying code 613
    — classifies as `provider_rejected_message` and is dead-lettered, which is
    how a throttled account quietly loses its replies. The second half shows
    the status fallback still bites for a 400 with no code, so the guard is not
    passing by turning every 400 retryable.
    """
    throttled = _delivery(_rejects(400, code=613)).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )
    assert throttled.status is OutcomeStatus.RETRYABLE
    assert next_state(throttled, attempt_count=1) == "retryable"

    refused = _delivery(_rejects(400)).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )
    assert refused.status is OutcomeStatus.TERMINAL
    assert next_state(refused, attempt_count=1) == "dead_letter"


def test_a_provider_retry_instruction_is_carried_rather_than_invented() -> None:
    def throttled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "97"},
            json={"error": {"code": 613, "message": "slow down"}},
        )

    outcome = _delivery(throttled).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.retry_after_seconds == 97


def test_a_closed_window_refusal_is_terminal_and_never_retried() -> None:
    """The provider's refusal is classified, not second-guessed.

    Terminal because the identical call is refused identically. The connector
    still does not own the window rule: it computes no clock, it reports what
    came back, and whether to answer in another form is decided elsewhere.
    """
    outcome = _delivery(_rejects(400, code=10, subcode=2018278)).handler_for(
        SEND_CAPABILITY_ID
    )(_send("send_direct_message", _direct_params("instagram_dm")))

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "provider_refused_by_messaging_policy"
    for attempt in (1, 2, 9):
        assert next_state(outcome, attempt_count=attempt) == "dead_letter"


def test_a_timeout_after_the_request_started_is_ambiguous_never_retried() -> None:
    """The one outcome that must not be collapsed into either neighbour.

    The bytes may have been accepted with the answer lost on the way back, so
    retrying duplicates a customer-visible message and dead-lettering hides one
    that was sent. `next_state` is asserted across attempt counts because the
    engine turns an exhausted RETRYABLE into `dead_letter` — a proof that only
    checked the status would also pass for a classification that gets retried.
    """

    def ambiguous(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider prose", request=request)

    outcome = _delivery(ambiguous).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )

    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_outcome_ambiguous"
    for attempt in (1, 2, 9):
        assert next_state(outcome, attempt_count=attempt) == "reconciliation_required"
    assert "provider prose" not in repr(outcome)


def test_a_connection_that_never_opened_is_retryable() -> None:
    """The other half of that distinction, so ambiguity is not the default.

    Nothing left the process, so repeating it cannot duplicate anything.
    """

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    outcome = _delivery(refused).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.error_code == "provider_connect_failed"
    assert next_state(outcome, attempt_count=1) == "retryable"


def test_a_success_without_a_provider_reference_is_not_recorded_as_delivered() -> None:
    outcome = _delivery(_accepts({})).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )

    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_receipt_missing"


def test_a_graph_error_carried_under_a_2xx_is_still_an_error() -> None:
    """Graph does not always spend an HTTP status on a refusal."""
    outcome = _delivery(_rejects(200, code=613)).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params())
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.error_code == "provider_rate_limited"


@pytest.mark.parametrize(
    ("action", "params", "code"),
    [
        ("send_smoke_signal", {}, "action_unsupported"),
        ("send_direct_message", {"channel": "sms"}, "channel_unsupported"),
        (
            "send_direct_message",
            {"channel": "facebook_comment"},
            "channel_unsupported",
        ),
        ("reply_to_comment", {"channel": "instagram_dm"}, "channel_unsupported"),
        (
            "send_direct_message",
            {
                "channel": "facebook_messenger",
                "provider_account_id": "another-page",
                "recipient_id": "fb-user-1",
                "body": "Hello",
            },
            "provider_account_not_bound",
        ),
        (
            "send_direct_message",
            {
                "channel": "facebook_messenger",
                "provider_account_id": PAGE_ID,
                "recipient_id": "fb-user-1",
                "body": "   ",
            },
            "body_required",
        ),
        (
            "send_direct_message",
            {
                "channel": "facebook_messenger",
                "provider_account_id": PAGE_ID,
                "body": "Hello",
            },
            "recipient_required",
        ),
    ],
)
def test_an_untranslatable_command_is_terminal_before_any_provider_call(
    action: str, params: dict[str, object], code: str
) -> None:
    outcome = _delivery(_no_call).handler_for(SEND_CAPABILITY_ID)(_send(action, params))

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == code


@pytest.mark.parametrize(
    ("config", "code"),
    [
        ({**DELIVERY_CONFIG, "graph_api_version": "21.0"}, "graph_api_version_invalid"),
        ({**DELIVERY_CONFIG, "auth_mode": "page_access_token"}, "auth_mode_invalid"),
        ({**DELIVERY_CONFIG, "timeout_seconds": 0}, "timeout_seconds_invalid"),
        ({**DELIVERY_CONFIG, "timeout_seconds": 600}, "timeout_seconds_invalid"),
    ],
)
def test_an_unusable_delivery_configuration_never_reaches_the_provider(
    config: dict[str, object], code: str
) -> None:
    """No ageing default stands in for a missing compatibility decision.

    Sub fell back to `v21.0` and silently normalized an unknown auth mode to
    `individual`; either fallback sends a real message under a contract nobody
    chose, through material nobody selected.
    """
    outcome = _delivery(_no_call).handler_for(SEND_CAPABILITY_ID)(
        _send("send_direct_message", _direct_params(), config=config)
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == code


def test_material_that_was_not_materialized_fails_before_the_call() -> None:
    request = DispatchRequest(
        capability_id=SEND_CAPABILITY_ID,
        event_type="messaging.send.requested.v1",
        payload={
            "action": "send_direct_message",
            "params": _direct_params("instagram_dm"),
        },
        config=dict(DELIVERY_CONFIG),
        secrets={FACEBOOK_PAGE_ACCESS_TOKEN: PAGE_MATERIAL},
        idempotency_key="product:reply:2",
    )

    outcome = _delivery(_no_call).handler_for(SEND_CAPABILITY_ID)(request)

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "access_material_unavailable"


def test_each_capability_reaches_only_its_own_factory() -> None:
    assert PLUGIN.handler_for(SEND_CAPABILITY_ID) is not None
    with pytest.raises(ValueError, match="not a delivery capability"):
        PLUGIN.handler_for(CAPABILITY_ID)
    with pytest.raises(ValueError, match="not an ingress capability"):
        PLUGIN.ingress_handler_for(SEND_CAPABILITY_ID)


def test_a_delivery_handler_called_for_another_capability_refuses() -> None:
    handler = PLUGIN.handler_for(SEND_CAPABILITY_ID)
    outcome = handler(
        DispatchRequest(
            capability_id=CAPABILITY_ID,
            event_type="messaging.receive.v1",
            payload={},
            config=dict(DELIVERY_CONFIG),
            secrets=dict(DELIVERY_SECRETS),
            idempotency_key="product:reply:3",
        )
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "capability_unsupported"
