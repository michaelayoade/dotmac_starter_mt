"""Acceptance tests for the first provider connector, over the prebuilt corpus."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from dotmac_connector_whatsapp import MANIFEST, PLUGIN, __version__
from dotmac_connector_whatsapp.catalogue import (
    MAX_CATALOGUE_PAGES,
    CatalogueReadError,
)
from dotmac_connector_whatsapp.media import (
    DEFAULT_MAX_CAPTION_CHARACTERS,
    DEFAULT_MAX_FILENAME_CHARACTERS,
    DEFAULT_MEDIA_BYTE_LIMITS,
)
from dotmac_connector_whatsapp.plugin import WhatsAppConnector
from dotmac_connector_whatsapp.wire import DeliveryContractError
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.retry import OutcomeStatus
from dotmac_integration.runtime_policy import derive_runtime_policy
from dotmac_integration.spi import (
    ConnectorMode,
    DispatchRequest,
    EgressDeclaration,
    IngressRequest,
    InvalidManifestError,
    SecretBindingDeclaration,
    VerificationResult,
)
from jsonschema import Draft202012Validator

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "meta_whatsapp"
CORPUS = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
SIGNATURES = json.loads((FIXTURE_ROOT / "signatures.json").read_text(encoding="utf-8"))
KEYS = CORPUS["signing_keys"]
CONFIG: dict[str, object] = {}
MATERIAL = {
    "webhook_signing_secret": KEYS["primary"],
    "webhook_signing_previous_secret": KEYS["previous"],
    "webhook_verify_token": CORPUS["handshake"]["query"]["hub.verify_token"],
}


def _body(relative: str) -> bytes:
    return (FIXTURE_ROOT / relative).read_bytes()


def _handler():
    return PLUGIN.ingress_handler_for("messaging.receive.v1")


def _delivery_request(relative: str, key: str = "primary") -> IngressRequest:
    return IngressRequest(
        raw_body=_body(relative),
        headers={
            "X-Hub-Signature-256": SIGNATURES["bodies"][relative]["signatures"][key]
        },
    )


def test_the_distribution_and_plugin_satisfy_the_released_spi() -> None:
    assert __version__ == "0.1.0a3"
    assert MANIFEST.version == __version__
    assert str(MANIFEST.spi_range) == ">=1.4,<2.0"
    assert_plugin_conforms(PLUGIN)


def test_the_connector_adds_delivery_without_losing_ingress() -> None:
    assert MANIFEST.capability_ids == {
        "messaging.receive.v1",
        "messaging.send.v1",
        "messaging.templates.read.v1",
    }
    assert PLUGIN.modes == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )
    assert MANIFEST.egress == EgressDeclaration(hosts=("graph.facebook.com",))
    assert tuple(item.name for item in MANIFEST.secret_bindings or ()) == (
        "webhook_signing_secret",
        "webhook_signing_previous_secret",
        "webhook_verify_token",
        "access_token",
    )


def test_the_current_manifest_is_the_complete_runtime_policy() -> None:
    assert MANIFEST.secret_bindings == (
        SecretBindingDeclaration(
            name="webhook_signing_secret",
            description="Primary exact-byte webhook signature key.",
        ),
        SecretBindingDeclaration(
            name="webhook_signing_previous_secret",
            required=False,
            description="Previous webhook signature key during a bounded rotation.",
        ),
        SecretBindingDeclaration(
            name="webhook_verify_token",
            description="Subscription challenge comparison token.",
        ),
        SecretBindingDeclaration(
            name="access_token",
            required=False,
            description=(
                "Graph API access token; required when messaging.send.v1 or "
                "messaging.templates.read.v1 is bound."
            ),
        ),
    )
    assert MANIFEST.egress == EgressDeclaration(hosts=("graph.facebook.com",))
    assert MANIFEST.capabilities[0].config_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "phone_number_id": {
                "type": "string",
                "pattern": "^[0-9]{1,40}$",
            },
            "graph_api_version": {
                "type": "string",
                "pattern": "^v[0-9]{1,2}\\.[0-9]+$",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 60,
            },
        },
    }
    schema = Draft202012Validator(MANIFEST.capabilities[0].config_schema)
    assert not tuple(schema.iter_errors(CONFIG))
    assert tuple(
        schema.iter_errors(
            {
                "signing_slots": ["operator_alias"],
                "handshake_slot": "another_alias",
            }
        )
    )

    policy = derive_runtime_policy(ConnectorRegistry((PLUGIN,)))

    assert policy.egress_hosts == ("graph.facebook.com",)
    assert policy.secret_bindings == (
        ("meta_whatsapp", "access_token", False),
        ("meta_whatsapp", "webhook_signing_previous_secret", False),
        ("meta_whatsapp", "webhook_signing_secret", True),
        ("meta_whatsapp", "webhook_verify_token", True),
    )


def test_the_published_ingress_contracts_remain_historical_manifests() -> None:
    assert len(PLUGIN.historical_manifests) == 2
    historical = PLUGIN.historical_manifests[0]
    assert historical.connector_key == "meta_whatsapp"
    assert historical.version == "0.1.0a1"
    assert str(historical.spi_range) == ">=1.2,<2.0"
    assert historical.declares_runtime_boundaries is False
    assert historical.capabilities[0].config_schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["signing_slots", "handshake_slot"],
        "properties": {
            "signing_slots": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
            },
            "handshake_slot": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
            },
        },
    }
    assert (
        historical.digest
        == "235fdb90fdc4ea0cfd6327c3c9a68c6c1df8387535620fea23d6a632b9c36978"
    )

    legacy_config: dict[str, object] = {
        "signing_slots": ["current", "previous"],
        "handshake_slot": "handshake",
    }
    legacy_material = {
        "current": KEYS["primary"],
        "previous": KEYS["previous"],
        "handshake": CORPUS["handshake"]["query"]["hub.verify_token"],
    }
    assert (
        PLUGIN.validate_connection(config=legacy_config, secrets=legacy_material) == ()
    )
    assert (
        _handler()
        .verify(
            _delivery_request("bodies/01_text_message.json"),
            config=legacy_config,
            secrets=legacy_material,
        )
        .accepted
    )

    ingress_only = PLUGIN.historical_manifests[1]
    assert ingress_only.version == "0.1.0a2"
    assert ingress_only.capability_ids == {"messaging.receive.v1"}
    assert ingress_only.egress == EgressDeclaration()


SEND_CONFIG: dict[str, object] = {
    "phone_number_id": "123456789",
    "graph_api_version": "v23.0",
    "timeout_seconds": 10,
    "waba_id": "443723705501042",
}


def _send_request(
    action: str,
    params: dict[str, object],
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    return DispatchRequest(
        capability_id="messaging.send.v1",
        event_type="messaging.send.requested.v1",
        payload={"action": action, "params": params},
        config=dict(config or SEND_CONFIG),
        secrets={"access_token": "held-access-token"},
        idempotency_key="sub:message:1",
    )


class _Clock:
    """A hand-driven monotonic clock.

    Cache staleness is a property of elapsed time, and a test that proved it by
    sleeping for the TTL would prove it once and then be deleted for being slow.
    """

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _delivery_plugin(handler, clock=None) -> WhatsAppConnector:
    connector = WhatsAppConnector(transport=httpx.MockTransport(handler))
    if clock is None:
        return connector
    return WhatsAppConnector(
        transport=httpx.MockTransport(handler),
        catalogue_cache=connector.catalogue_cache,
        clock=clock,
    )


def _recording(seen: list[httpx.Request]):
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.unexpected"}]})

    return respond


def _row(
    *,
    name: str = "service_notice",
    language: str = "en",
    status: str = "APPROVED",
    body: str = "Service restored.",
    header: dict[str, object] | None = None,
    buttons: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    components: list[dict[str, object]] = []
    if header is not None:
        components.append({"type": "HEADER", **header})
    components.append({"type": "BODY", "text": body})
    if buttons is not None:
        components.append({"type": "BUTTONS", "buttons": buttons})
    return {
        "name": name,
        "language": language,
        "status": status,
        "category": "UTILITY",
        "components": components,
    }


def _catalogue_response(
    rows: list[dict[str, object]], *, after: str | None = None
) -> httpx.Response:
    payload: dict[str, object] = {"data": rows}
    if after is not None:
        payload["paging"] = {
            "next": "https://graph.facebook.com/next",
            "cursors": {"after": after},
        }
    return httpx.Response(200, json=payload)


def _is_catalogue(request: httpx.Request) -> bool:
    return request.url.path.endswith("/message_templates")


def _template_request(
    params: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    body: dict[str, object] = {
        "recipient": "+2348000000001",
        "template_name": "service_notice",
        "language": "en",
        "variables": {},
    }
    body.update(params or {})
    return _send_request("send_template", body, config)


def test_text_delivery_matches_the_qualifying_sub_wire_shape() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.sent-1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _send_request(
            "send_text",
            {"recipient": "+2348000000001", "body": "Service restored"},
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "wamid.sent-1"
    assert outcome.provider_status_code == 200
    request = seen[0]
    assert str(request.url) == ("https://graph.facebook.com/v23.0/123456789/messages")
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "to": "+2348000000001",
        "type": "text",
        "text": {"body": "Service restored"},
    }
    assert request.headers["authorization"] == "Bearer held-access-token"


def test_template_parameters_keep_the_source_ordering_contract() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row(body="{{1}} {{2}} {{3}}")])
        return httpx.Response(200, json={"messages": [{"id": "wamid.template-1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _send_request(
            "send_template",
            {
                "recipient": "+2348000000001",
                "template_name": "service_notice",
                "language": "en",
                "variables": {"2": "restored", "1": "Internet", "name": "Ada"},
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    body = json.loads(seen[-1].content)
    assert body["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Internet"},
        {"type": "text", "text": "restored"},
        {"type": "text", "text": "Ada"},
    ]


def test_template_language_keeps_the_qualifying_sub_default() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row(body="Service restored.")])
        return httpx.Response(200, json={"messages": [{"id": "wamid.template-1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _send_request(
            "send_template",
            {
                "recipient": "+2348000000001",
                "template_name": "service_notice",
                "variables": {},
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert json.loads(seen[-1].content)["template"]["language"] == {"code": "en"}


def test_media_content_is_uploaded_before_the_message_is_sent() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-1"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.media-1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _send_request(
            "send_media",
            {
                "recipient": "+2348000000001",
                "media_type": "document",
                "content_base64": "aGVsbG8=",
                "content_type": "text/plain",
                "filename": "notice.txt",
                "caption": "Service notice",
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert [request.url.path.rsplit("/", 1)[-1] for request in seen] == [
        "media",
        "messages",
    ]
    sent = json.loads(seen[1].content)
    assert sent["document"] == {
        "id": "media-1",
        "caption": "Service notice",
        "filename": "notice.txt",
    }


def test_the_sub_octet_stream_upload_default_is_refused_before_the_wire_call() -> None:
    """Sub defaulted an upload's declared type to `application/octet-stream`.

    Meta accepts that type for no media kind, so every upload it produced was
    going to be rejected AFTER the body had been streamed. The default is
    deliberately retired: an upload with no declared type is now a typed local
    refusal, and nothing reaches the provider.
    """
    seen: list[httpx.Request] = []

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _send_request(
            "send_media",
            {
                "recipient": "+2348000000001",
                "media_type": "document",
                "content_base64": "aGVsbG8=",
            },
        )
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "media_content_type_required"
    assert seen == []


def test_the_sub_upload_filename_default_still_applies() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-1"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.media-1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _send_request(
            "send_media",
            {
                "recipient": "+2348000000001",
                "media_type": "document",
                "content_base64": "aGVsbG8=",
                "content_type": "text/plain",
            },
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    upload = seen[0].content
    assert b'name="type"\r\n\r\ntext/plain' in upload
    assert b'filename="attachment"' in upload


def test_timeout_after_request_start_requires_reconciliation() -> None:
    def ambiguous(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private provider response", request=request)

    outcome = _delivery_plugin(ambiguous).handler_for("messaging.send.v1")(
        _send_request("send_text", {"recipient": "+2348000000001", "body": "hello"})
    )
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_outcome_ambiguous"
    assert "private" not in (outcome.error_detail or "")


@pytest.mark.parametrize(
    ("status", "expected", "code"),
    [
        (429, OutcomeStatus.RETRYABLE, "provider_rate_limited"),
        (500, OutcomeStatus.RETRYABLE, "provider_retryable_response"),
        (400, OutcomeStatus.TERMINAL, "provider_rejected_message"),
    ],
)
def test_provider_response_classification_matches_sub(
    status: int, expected: OutcomeStatus, code: str
) -> None:
    plugin = _delivery_plugin(
        lambda request: httpx.Response(status, text="held-access-token private")
    )
    outcome = plugin.handler_for("messaging.send.v1")(
        _send_request("send_text", {"recipient": "+2348000000001", "body": "hello"})
    )

    assert outcome.status is expected
    assert outcome.error_code == code
    assert outcome.provider_status_code == status
    assert "held-access-token" not in repr(outcome)
    assert "private" not in repr(outcome)


def test_success_without_a_provider_reference_is_not_reported_delivered() -> None:
    plugin = _delivery_plugin(lambda request: httpx.Response(200, json={}))
    outcome = plugin.handler_for("messaging.send.v1")(
        _send_request("send_text", {"recipient": "+2348000000001", "body": "hello"})
    )
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_receipt_missing"


def test_connection_validation_requires_resolved_material_without_naming_it() -> None:
    refused = PLUGIN.validate_connection(config=CONFIG, secrets={})
    assert refused
    assert all(not item.ok for item in refused)
    assert all("current" not in item.detail for item in refused)
    assert all("handshake" not in item.detail for item in refused)

    assert PLUGIN.validate_connection(config=CONFIG, secrets=MATERIAL) == ()

    without_previous = {
        name: value
        for name, value in MATERIAL.items()
        if name != "webhook_signing_previous_secret"
    }
    assert PLUGIN.validate_connection(config=CONFIG, secrets=without_previous) == ()


def test_the_handshake_echoes_only_after_constant_time_token_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compared: list[tuple[str, str]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        compared.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(
        "dotmac_connector_whatsapp.plugin.hmac.compare_digest", recording_compare
    )
    query = CORPUS["handshake"]["query"]
    request = IngressRequest(params=query)

    answer = _handler().challenge(request, config=CONFIG, secrets=MATERIAL)

    assert answer is not None
    assert answer.body == query["hub.challenge"].encode()
    assert answer.media_type == "text/plain"
    assert len(compared) == 1


@pytest.mark.parametrize("token", [None, "wrong"])
def test_missing_and_wrong_handshake_tokens_have_the_same_refusal(
    token: str | None,
) -> None:
    params = {"hub.mode": "subscribe", "hub.challenge": "challenge-123"}
    if token is not None:
        params["hub.verify_token"] = token
    assert (
        _handler().challenge(
            IngressRequest(params=params), config=CONFIG, secrets=MATERIAL
        )
        is None
    )


@pytest.mark.parametrize("key_name,position", [("primary", 0), ("previous", 1)])
def test_verification_evaluates_every_active_slot_and_reports_only_positions(
    key_name: str, position: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(
        "dotmac_connector_whatsapp.plugin.hmac.compare_digest", recording_compare
    )
    result = _handler().verify(
        _delivery_request("bodies/01_text_message.json", key_name),
        config=CONFIG,
        secrets=MATERIAL,
    )

    assert result == VerificationResult(
        accepted=True, matched_secret_positions=(position,)
    )
    assert len(calls) == 2
    assert repr(result).find(KEYS[key_name]) == -1


def test_malformed_signature_is_refused_before_secret_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_compare(_left: str, _right: str) -> bool:
        raise AssertionError("malformed headers are rejected before comparison")

    monkeypatch.setattr(
        "dotmac_connector_whatsapp.plugin.hmac.compare_digest", must_not_compare
    )
    result = _handler().verify(
        IngressRequest(
            raw_body=_body("bodies/01_text_message.json"),
            headers={"X-Hub-Signature-256": "sha256=bad"},
        ),
        config=CONFIG,
        secrets=MATERIAL,
    )
    assert result == VerificationResult(accepted=False)


def test_a_well_formed_forgery_costs_the_same_secret_work_as_a_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    real_compare = hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        nonlocal calls
        calls += 1
        return real_compare(left, right)

    monkeypatch.setattr(
        "dotmac_connector_whatsapp.plugin.hmac.compare_digest", recording_compare
    )
    result = _handler().verify(
        IngressRequest(
            raw_body=_body("bodies/01_text_message.json"),
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
        ),
        config=CONFIG,
        secrets=MATERIAL,
    )
    assert result == VerificationResult(accepted=False)
    assert calls == 2


def test_signature_is_over_the_exact_wire_bytes() -> None:
    relative = "bodies/01_text_message.json"
    request = _delivery_request(relative)
    compact = json.dumps(json.loads(request.raw_body), separators=(",", ":")).encode()
    assert compact != request.raw_body

    result = _handler().verify(request, config=CONFIG, secrets=MATERIAL)
    changed = _handler().verify(
        IngressRequest(raw_body=compact, headers=request.headers),
        config=CONFIG,
        secrets=MATERIAL,
    )

    assert result.accepted is True
    assert changed == VerificationResult(accepted=False)


@pytest.mark.parametrize(
    "fixture",
    [item["file"] for item in CORPUS["fixtures"]],
    ids=lambda value: Path(value).stem,
)
def test_the_connector_matches_every_preimplementation_observation(
    fixture: str,
) -> None:
    request = _delivery_request(fixture)
    verification = _handler().verify(request, config=CONFIG, secrets=MATERIAL)
    assert verification.accepted

    events, acknowledgement = _handler().normalize(request, config=CONFIG)
    expected = next(item for item in CORPUS["fixtures"] if item["file"] == fixture)[
        "expected_observations"
    ]

    assert [event.provider_event_id for event in events] == [
        item["provider_event_id"] for item in expected
    ]
    assert [event.event_type for event in events] == [
        item["event_type"] for item in expected
    ]
    assert [event.payload["transport_evidence"]["locator"] for event in events] == [
        item["locator"] for item in expected
    ]
    assert [
        event.payload["transport_evidence"].get("reason_code") for event in events
    ] == [item.get("reason_code") for item in expected]
    assert acknowledgement is not None
    assert acknowledgement.body == b'{"status":"ok"}'
    assert acknowledgement.media_type == "application/json"


def test_media_and_location_are_typed_without_presentation_placeholders() -> None:
    media, _ = _handler().normalize(
        _delivery_request("bodies/02_media_image_message.json"), config=CONFIG
    )
    location, _ = _handler().normalize(
        _delivery_request("bodies/03_location_message.json"), config=CONFIG
    )

    media_message = media[0].payload["message"]
    assert media_message["body"] == ""
    assert media_message["attachments"] == [
        {
            "asset_type": "image",
            "provider_media_id": "media-1",
            "mime_type": "image/jpeg",
        }
    ]
    location_message = location[0].payload["message"]
    assert location_message["body"] == ""
    assert location_message["attachments"][0]["location"] == {
        "latitude": 6.5243793,
        "longitude": 3.3792057,
        "name": "Customer location",
        "address": "Lagos, Nigeria",
    }
    assert "maps" not in json.dumps(location_message).lower()


def test_provider_ids_cross_the_product_boundary_raw() -> None:
    events, _ = _handler().normalize(
        _delivery_request("bodies/06_batch_mixed.json"), config=CONFIG
    )
    assert events[0].provider_event_id == "wamid.meta-1"
    assert events[0].payload["provider_event_id"] == "wamid.meta-1"
    assert not events[0].provider_event_id.startswith("wa:")


def test_error_identity_is_item_scoped_not_request_scoped() -> None:
    events, _ = _handler().normalize(
        _delivery_request("bodies/06_batch_mixed.json"), config=CONFIG
    )
    error = events[-1]
    item = json.loads(_body("bodies/06_batch_mixed.json"))["entry"][2]["changes"][0][
        "value"
    ]["errors"][0]
    digest = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
    assert error.provider_event_id == f"error:waba-1:{digest}"


@pytest.mark.parametrize(
    ("document", "reason", "locator"),
    [
        ({"entry": ["bad"]}, "entry_object_invalid", "/entry/0"),
        (
            {"entry": [{"id": "waba-1", "changes": "bad"}]},
            "changes_list_invalid",
            "/entry/0/changes",
        ),
        (
            {"entry": [{"id": "waba-1", "changes": ["bad"]}]},
            "change_object_invalid",
            "/entry/0/changes/0",
        ),
        (
            {"entry": [{"id": "waba-1", "changes": [{"value": "bad"}]}]},
            "value_object_invalid",
            "/entry/0/changes/0/value",
        ),
    ],
)
def test_bad_containers_become_typed_evidence_instead_of_disappearing(
    document: dict[str, object], reason: str, locator: str
) -> None:
    events, _ = _handler().normalize(
        IngressRequest(raw_body=json.dumps(document).encode()), config=CONFIG
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "whatsapp.entry.malformed.v1"
    assert event.payload["transport_evidence"] == {
        "locator": locator,
        "identity_source": "derived",
        "reason_code": reason,
    }


def test_invalid_location_coordinates_are_observed_as_malformed() -> None:
    document = json.loads(_body("bodies/03_location_message.json"))
    document["entry"][0]["changes"][0]["value"]["messages"][0]["location"][
        "latitude"
    ] = 91
    events, _ = _handler().normalize(
        IngressRequest(raw_body=json.dumps(document).encode()), config=CONFIG
    )

    assert events[0].event_type == "whatsapp.entry.malformed.v1"
    assert (
        events[0].payload["transport_evidence"]["reason_code"]
        == "message_content_invalid"
    )


def test_media_without_a_provider_asset_id_is_observed_as_malformed() -> None:
    document = json.loads(_body("bodies/02_media_image_message.json"))
    del document["entry"][0]["changes"][0]["value"]["messages"][0]["image"]["id"]
    events, _ = _handler().normalize(
        IngressRequest(raw_body=json.dumps(document).encode()), config=CONFIG
    )

    assert events[0].event_type == "whatsapp.entry.malformed.v1"
    assert (
        events[0].payload["transport_evidence"]["reason_code"]
        == "message_content_invalid"
    )


def test_invalid_status_timestamp_is_observed_as_malformed() -> None:
    document = json.loads(_body("bodies/04_status_delivered.json"))
    document["entry"][0]["changes"][0]["value"]["statuses"][0]["timestamp"] = (
        "not-an-instant"
    )
    events, _ = _handler().normalize(
        IngressRequest(raw_body=json.dumps(document).encode()), config=CONFIG
    )

    assert events[0].event_type == "whatsapp.entry.malformed.v1"
    assert (
        events[0].payload["transport_evidence"]["reason_code"]
        == "status_timestamp_invalid"
    )


def test_an_undeclared_capability_has_no_handler() -> None:
    with pytest.raises(InvalidManifestError):
        PLUGIN.ingress_handler_for("messaging.receive.v2")


# --------------------------------------------------------------------------
# Approved-template catalogue: the pre-flight gate
#
# Every negative below asserts BOTH the typed outcome and that no `/messages`
# request was ever made. The second half is the point: a refusal that arrives
# after a provider round trip is a different, worse behaviour that the first
# half alone cannot tell apart.
# --------------------------------------------------------------------------


def _sent_paths(seen: list[httpx.Request]) -> list[str]:
    return [request.url.path.rsplit("/", 1)[-1] for request in seen]


def test_an_approved_matching_template_still_sends() -> None:
    """The sensitivity proof for every refusal test below.

    A check over a transport that never sends anything passes for the wrong
    reason. This is the positive control: with the SAME transport and the SAME
    assertions, an approved template with matching arity does reach
    `/messages`. So `_sent_paths(seen) == ["message_templates"]` in the
    negatives means the gate stopped it, not that the harness was inert.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row(body="Hello {{1}}")])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t1"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request({"variables": {"1": "Ada"}})
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert _sent_paths(seen) == ["message_templates", "messages"]


def test_an_unapproved_template_is_refused_before_the_wire_call() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert _is_catalogue(request)
        return _catalogue_response([_row(status="REJECTED")])

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "template_not_approved"
    # The provider status travels as the REASON, which Sub had nowhere to put:
    # its refusal was a generic rejection distinguishable only by prose.
    assert outcome.error_detail == "REJECTED"
    assert _sent_paths(seen) == ["message_templates"]


@pytest.mark.parametrize("status", ["PENDING", "PAUSED", "DISABLED", "IN_APPEAL"])
def test_only_approved_may_be_sent_against(status: str) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response([_row(status=status)])

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.error_code == "template_not_approved"
    assert _sent_paths(seen) == ["message_templates"]


def test_a_template_the_catalogue_does_not_carry_is_refused() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response([])

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "template_not_found"
    assert _sent_paths(seen) == ["message_templates"]


def test_a_template_approved_only_in_another_language_is_refused() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response([_row(language="fr")])

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.error_code == "template_language_unavailable"
    assert _sent_paths(seen) == ["message_templates"]


@pytest.mark.parametrize(
    ("body", "variables"),
    [
        ("Hello {{1}} and {{2}}", {"1": "Ada"}),
        ("Hello {{1}}", {"1": "Ada", "2": "surplus"}),
        ("No placeholders at all", {"1": "Ada"}),
    ],
)
def test_a_parameter_count_the_catalogue_does_not_describe_is_refused(
    body: str, variables: dict[str, object]
) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response([_row(body=body)])

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request({"variables": variables})
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "template_variable_arity_mismatch"
    assert _sent_paths(seen) == ["message_templates"]


def test_a_media_header_template_cannot_be_filled_from_a_flat_variable_map() -> None:
    """A flat `variables` map can only reach the BODY.

    Sending the body and leaving the header parameter out would produce a
    template with a hole in it, which Meta rejects and the recipient never sees.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response(
            [_row(body="Hello {{1}}", header={"format": "IMAGE"})]
        )

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request({"variables": {"1": "Ada"}})
    )

    assert outcome.error_code == "template_variable_arity_mismatch"
    assert _sent_paths(seen) == ["message_templates"]


def test_explicit_components_are_matched_component_by_component() -> None:
    seen: list[httpx.Request] = []
    catalogue = _row(
        body="Hello {{1}}",
        header={"format": "IMAGE"},
        buttons=[{"type": "URL", "url": "https://example.invalid/{{1}}"}],
    )

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([catalogue])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t2"}]})

    components: list[dict[str, object]] = [
        {
            "type": "header",
            "parameters": [
                {"type": "image", "image": {"link": "https://example.invalid/a.jpg"}}
            ],
        },
        {"type": "body", "parameters": [{"type": "text", "text": "Ada"}]},
        {
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [{"type": "text", "text": "abc"}],
        },
    ]
    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request({"components": components})
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert _sent_paths(seen) == ["message_templates", "messages"]

    seen.clear()
    without_button = [item for item in components if item["type"] != "button"]
    refused = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request({"components": without_button})
    )
    assert refused.error_code == "template_variable_arity_mismatch"
    assert _sent_paths(seen) == ["message_templates"]


def test_a_send_binding_that_cannot_name_its_account_is_refused() -> None:
    seen: list[httpx.Request] = []
    config = {key: value for key, value in SEND_CONFIG.items() if key != "waba_id"}

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _template_request(config=config)
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "waba_id_required"
    assert seen == []
    # And the schema refuses the same configuration at activation, so this is a
    # defence in depth rather than the only guard.
    schema = Draft202012Validator(MANIFEST.capabilities[1].config_schema)
    assert tuple(schema.iter_errors(config))


# --------------------------------------------------------------------------
# Cache freshness: cold, fresh, stale, and a failed refresh
# --------------------------------------------------------------------------


def test_a_cold_read_is_reused_while_it_is_fresh() -> None:
    seen: list[httpx.Request] = []
    clock = _Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row()])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t3"}]})

    plugin = _delivery_plugin(respond, clock)
    assert (
        plugin.handler_for("messaging.send.v1")(_template_request()).status
        is OutcomeStatus.SUCCEEDED
    )
    clock.advance(299)
    assert (
        plugin.handler_for("messaging.send.v1")(_template_request()).status
        is OutcomeStatus.SUCCEEDED
    )

    assert _sent_paths(seen) == ["message_templates", "messages", "messages"]


def test_a_stale_entry_is_re_read_and_a_withdrawn_approval_takes_effect() -> None:
    """The whole reason there is no stale-while-revalidate.

    Meta withdraws an approval without telling the sender. If the expired entry
    were served for even one request, this send would go out against a template
    the account is no longer allowed to use.
    """
    seen: list[httpx.Request] = []
    clock = _Clock()
    status = ["APPROVED"]

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row(status=status[0])])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t4"}]})

    plugin = _delivery_plugin(respond, clock)
    assert (
        plugin.handler_for("messaging.send.v1")(_template_request()).status
        is OutcomeStatus.SUCCEEDED
    )

    status[0] = "PAUSED"
    clock.advance(300)
    outcome = plugin.handler_for("messaging.send.v1")(_template_request())

    assert outcome.error_code == "template_not_approved"
    assert _sent_paths(seen) == ["message_templates", "messages", "message_templates"]


def test_a_configured_ttl_of_zero_re_reads_on_every_send() -> None:
    seen: list[httpx.Request] = []
    clock = _Clock()
    config = SEND_CONFIG | {"template_cache_ttl_seconds": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row()])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t5"}]})

    plugin = _delivery_plugin(respond, clock)
    for _attempt in range(2):
        assert (
            plugin.handler_for("messaging.send.v1")(
                _template_request(config=config)
            ).status
            is OutcomeStatus.SUCCEEDED
        )

    assert _sent_paths(seen) == [
        "message_templates",
        "messages",
        "message_templates",
        "messages",
    ]


def test_a_failed_refresh_fails_closed_and_leaves_nothing_behind() -> None:
    """Cold-and-broken and stale-and-broken must behave identically.

    Sub left the expired tuple in its dict on a failed refresh. It was
    unreachable, but only because one branch happened not to read it. Here the
    entry is evicted, and the proof is that the NEXT healthy read goes back to
    the provider rather than answering from what was held.
    """
    seen: list[httpx.Request] = []
    clock = _Clock()
    healthy = [True]

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            if not healthy[0]:
                return httpx.Response(500)
            return _catalogue_response([_row()])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t6"}]})

    plugin = _delivery_plugin(respond, clock)
    assert (
        plugin.handler_for("messaging.send.v1")(_template_request()).status
        is OutcomeStatus.SUCCEEDED
    )

    healthy[0] = False
    clock.advance(300)
    refused = plugin.handler_for("messaging.send.v1")(_template_request())
    assert refused.status is OutcomeStatus.RETRYABLE
    assert refused.error_code == "template_provider_retryable"
    assert refused.provider_status_code == 500

    # Time has NOT moved on; a surviving entry would still be inside its TTL.
    healthy[0] = True
    assert (
        plugin.handler_for("messaging.send.v1")(_template_request()).status
        is OutcomeStatus.SUCCEEDED
    )
    assert _sent_paths(seen) == [
        "message_templates",
        "messages",
        "message_templates",
        "message_templates",
        "messages",
    ]


def test_a_cold_catalogue_read_that_fails_refuses_rather_than_assuming() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503)

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.error_code == "template_provider_retryable"
    assert _sent_paths(seen) == ["message_templates"]


def test_a_rate_limited_catalogue_read_carries_the_provider_instruction() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "42"})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.retry_after_seconds == 42


def test_a_catalogue_read_timeout_is_retryable_not_ambiguous() -> None:
    """A GET has no effect to duplicate.

    The send path calls a read timeout RECONCILIATION_REQUIRED because the
    message may have landed. Classifying a catalogue read the same way would
    park a message the provider never saw.
    """

    def ambiguous(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private provider detail", request=request)

    outcome = _delivery_plugin(ambiguous).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.RETRYABLE
    assert outcome.error_code == "template_provider_unavailable"
    assert "private" not in repr(outcome)


def test_a_refused_catalogue_read_is_terminal() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(403, text="held-access-token private")

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "template_provider_rejected"
    assert outcome.provider_status_code == 403
    assert "held-access-token" not in repr(outcome)
    assert _sent_paths(seen) == ["message_templates"]


@pytest.mark.parametrize("body", ["not json at all", '{"data": "not-a-list"}'])
def test_an_unreadable_catalogue_response_is_not_an_empty_catalogue(
    body: str,
) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=body)

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _template_request()
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "template_response_invalid"
    assert _sent_paths(seen) == ["message_templates"]


def test_the_cache_is_scoped_to_the_credential_that_filled_it() -> None:
    """One process serves many installations.

    A key that named only the WABA would hand one installation's answer to
    another whose credential was never checked against that account.
    """
    seen: list[httpx.Request] = []
    clock = _Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row()])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t7"}]})

    plugin = _delivery_plugin(respond, clock)
    first = _template_request()
    assert plugin.handler_for("messaging.send.v1")(first).status is (
        OutcomeStatus.SUCCEEDED
    )

    second = DispatchRequest(
        capability_id=first.capability_id,
        event_type=first.event_type,
        payload=first.payload,
        config=first.config,
        secrets={"access_token": "a-different-installations-token"},
        idempotency_key=first.idempotency_key,
    )
    assert plugin.handler_for("messaging.send.v1")(second).status is (
        OutcomeStatus.SUCCEEDED
    )

    assert _sent_paths(seen) == [
        "message_templates",
        "messages",
        "message_templates",
        "messages",
    ]


def test_the_send_path_asks_the_provider_for_one_template_by_name() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if _is_catalogue(request):
            return _catalogue_response([_row()])
        return httpx.Response(200, json={"messages": [{"id": "wamid.t8"}]})

    _delivery_plugin(respond).handler_for("messaging.send.v1")(_template_request())

    catalogue = seen[0]
    assert catalogue.url.path == "/v23.0/443723705501042/message_templates"
    assert catalogue.url.params["name"] == "service_notice"
    # Sub's exact field list and page size.
    assert catalogue.url.params["fields"] == "name,status,language,category,components"
    assert catalogue.url.params["limit"] == "200"
    assert catalogue.headers["authorization"] == "Bearer held-access-token"


# --------------------------------------------------------------------------
# Attachment mapping and validation
# --------------------------------------------------------------------------


def _media_request(
    params: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    body: dict[str, object] = {
        "recipient": "+2348000000001",
        "media_type": "image",
        "content_base64": base64.b64encode(b"binary").decode(),
        "content_type": "image/png",
    }
    body.update(params or {})
    return _send_request("send_media", body, config)


def test_the_documented_media_limits_are_the_provider_numbers() -> None:
    assert dict(DEFAULT_MEDIA_BYTE_LIMITS) == {
        "image": 5 * 1024 * 1024,
        "document": 100 * 1024 * 1024,
        "audio": 16 * 1024 * 1024,
        "video": 16 * 1024 * 1024,
    }
    assert DEFAULT_MAX_CAPTION_CHARACTERS == 1024
    assert DEFAULT_MAX_FILENAME_CHARACTERS == 255


def test_an_attachment_the_provider_does_not_accept_never_leaves() -> None:
    seen: list[httpx.Request] = []

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        # Sub's own allowlist accepts image/gif; Meta accepts it for nothing,
        # so today that file is uploaded in full and then rejected.
        _media_request({"content_type": "image/gif"})
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "media_content_type_unsupported"
    assert seen == []


def test_a_mime_type_supported_for_another_media_type_is_still_refused() -> None:
    seen: list[httpx.Request] = []

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _media_request({"media_type": "image", "content_type": "application/pdf"})
    )

    assert outcome.error_code == "media_content_type_unsupported"
    assert seen == []


def test_content_type_parameters_do_not_defeat_the_allowlist() -> None:
    """Sub's named behaviour, kept: a charset suffix is not a different type."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-9"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.m9"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _media_request(
            {"media_type": "document", "content_type": "Text/Plain; charset=utf-8"}
        )
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert b'name="type"\r\n\r\ntext/plain' in seen[0].content


def test_an_oversize_attachment_never_reaches_the_upload() -> None:
    seen: list[httpx.Request] = []
    oversize = base64.b64encode(b"\0" * (5 * 1024 * 1024 + 1)).decode()

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _media_request({"content_base64": oversize})
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "media_content_too_large"
    assert seen == []


def test_a_narrowed_configured_limit_is_the_one_that_bites() -> None:
    seen: list[httpx.Request] = []
    config = SEND_CONFIG | {"media_limits": {"image_bytes": 4}}

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _media_request(config=config)
    )

    assert outcome.error_code == "media_content_too_large"
    assert seen == []


def test_a_limit_above_the_provider_limit_is_refused_at_activation() -> None:
    """A configuration may narrow a limit, never widen one.

    Honouring 5 MB while the operator wrote 200 MB would make the configuration
    a lie; refusing says so at the moment it can still be fixed.
    """
    widened: dict[str, object] = {"media_limits": {"image_bytes": 200 * 1024 * 1024}}
    schema = Draft202012Validator(MANIFEST.capabilities[1].config_schema)
    assert tuple(schema.iter_errors(SEND_CONFIG | widened))

    diagnostics = PLUGIN.validate_connection(
        config=dict(CONFIG) | widened, secrets=MATERIAL
    )
    assert diagnostics and diagnostics[0].code == "media_limits_invalid"


def test_an_empty_attachment_is_refused() -> None:
    seen: list[httpx.Request] = []

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _media_request({"content_base64": base64.b64encode(b"").decode()})
    )

    assert outcome.error_code == "media_reference_required"
    assert seen == []


@pytest.mark.parametrize(
    ("params", "code"),
    [
        (
            {
                "media_type": "audio",
                "content_type": "audio/mpeg",
                "caption": "not renderable on audio",
            },
            "media_caption_unsupported",
        ),
        ({"caption": "x" * 1025}, "media_caption_too_long"),
        ({"filename": "not-a-document.png"}, "media_filename_unsupported"),
        (
            {
                "media_type": "document",
                "content_type": "application/pdf",
                "filename": "n" * 256,
            },
            "media_filename_too_long",
        ),
    ],
)
def test_caption_and_filename_rules_refuse_rather_than_edit_the_message(
    params: dict[str, object], code: str
) -> None:
    """Sub trimmed to 1024/255 and silently dropped a caption audio cannot show.

    Editing product content to fit a provider constraint is a decision that
    belongs to whoever wrote the message. The limits stay; the edit does not.
    """
    seen: list[httpx.Request] = []

    outcome = _delivery_plugin(_recording(seen)).handler_for("messaging.send.v1")(
        _media_request(params)
    )

    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == code
    assert seen == []


def test_a_caption_at_the_limit_is_sent_whole() -> None:
    seen: list[httpx.Request] = []
    caption = "x" * 1024

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-2"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.m2"}]})

    outcome = _delivery_plugin(respond).handler_for("messaging.send.v1")(
        _media_request({"caption": caption})
    )

    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert json.loads(seen[1].content)["image"]["caption"] == caption


def test_a_link_reference_is_type_checked_but_not_size_checked() -> None:
    """The connector never held these bytes, and does not pretend it did."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.m3"}]})

    plugin = _delivery_plugin(respond)
    sent = plugin.handler_for("messaging.send.v1")(
        _media_request(
            {
                "content_base64": None,
                "link": "https://example.invalid/a.png",
                "content_type": "image/png",
            }
        )
    )
    assert sent.status is OutcomeStatus.SUCCEEDED
    assert _sent_paths(seen) == ["messages"]

    refused = plugin.handler_for("messaging.send.v1")(
        _media_request(
            {
                "content_base64": None,
                "link": "https://example.invalid/a.gif",
                "content_type": "image/gif",
            }
        )
    )
    assert refused.error_code == "media_content_type_unsupported"
    assert _sent_paths(seen) == ["messages"]


# --------------------------------------------------------------------------
# The catalogue as a provider-neutral POLL capability
# --------------------------------------------------------------------------

CATALOGUE_CONFIG: dict[str, object] = {
    "waba_id": "443723705501042",
    "graph_api_version": "v23.0",
    "timeout_seconds": 10,
}


def test_the_catalogue_poll_follows_the_cursor_and_types_every_row() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("after") is None:
            return _catalogue_response(
                [_row(body="Hello {{1}}", header={"format": "IMAGE"})],
                after="page-2",
            )
        return _catalogue_response([_row(name="past_due", status="REJECTED")])

    handler = _delivery_plugin(respond).poll_handler_for("messaging.templates.read.v1")
    events, cursor = handler.poll(
        None, config=dict(CATALOGUE_CONFIG), secrets={"access_token": "held"}
    )

    assert cursor is None
    assert len(seen) == 2
    assert [event.event_type for event in events] == [
        "whatsapp.message_template.v1",
        "whatsapp.message_template.v1",
    ]
    approved = events[0].payload["message_template"]
    assert approved["approved"] is True
    assert approved["body_parameter_count"] == 1
    assert approved["header_format"] == "IMAGE"
    assert approved["header_parameter_count"] == 1
    # A non-approved template is reported as a FACT, not withheld: the product
    # projection needs to know a template stopped being usable.
    assert events[1].payload["message_template"]["approved"] is False
    assert events[0].payload["provider_account_scope"] == "443723705501042"
    assert events[0].payload["channel"] == "whatsapp"


def test_a_catalogue_observation_identity_tracks_its_content() -> None:
    def approved(request: httpx.Request) -> httpx.Response:
        return _catalogue_response([_row()])

    def paused(request: httpx.Request) -> httpx.Response:
        return _catalogue_response([_row(status="PAUSED")])

    secrets = {"access_token": "held"}
    first, _ = (
        _delivery_plugin(approved)
        .poll_handler_for("messaging.templates.read.v1")
        .poll(None, config=dict(CATALOGUE_CONFIG), secrets=secrets)
    )
    again, _ = (
        _delivery_plugin(approved)
        .poll_handler_for("messaging.templates.read.v1")
        .poll(None, config=dict(CATALOGUE_CONFIG), secrets=secrets)
    )
    changed, _ = (
        _delivery_plugin(paused)
        .poll_handler_for("messaging.templates.read.v1")
        .poll(None, config=dict(CATALOGUE_CONFIG), secrets=secrets)
    )

    assert first[0].provider_event_id == again[0].provider_event_id
    assert first[0].provider_event_id != changed[0].provider_event_id


def test_the_catalogue_poll_refuses_to_return_a_catalogue_it_cannot_finish() -> None:
    """A short catalogue reads exactly like a withdrawn template.

    Sub asked for one page of 200 and dropped whatever followed. That silence is
    indistinguishable from "not approved", so this connector fails instead.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _catalogue_response([_row()], after=f"page-{len(seen)}")

    handler = _delivery_plugin(respond).poll_handler_for("messaging.templates.read.v1")
    with pytest.raises(CatalogueReadError):
        handler.poll(
            None, config=dict(CATALOGUE_CONFIG), secrets={"access_token": "held"}
        )

    assert len(seen) == MAX_CATALOGUE_PAGES


@pytest.mark.parametrize(
    ("config", "secrets"),
    [
        ({"graph_api_version": "v23.0", "timeout_seconds": 10}, {"access_token": "h"}),
        (CATALOGUE_CONFIG, {}),
    ],
)
def test_the_catalogue_poll_refuses_an_incomplete_binding(
    config: dict[str, object], secrets: dict[str, str]
) -> None:
    handler = _delivery_plugin(_recording([])).poll_handler_for(
        "messaging.templates.read.v1"
    )
    with pytest.raises((CatalogueReadError, DeliveryContractError)):
        handler.poll(None, config=dict(config), secrets=dict(secrets))


def test_each_capability_is_served_only_by_its_declared_mode() -> None:
    with pytest.raises(ValueError):
        PLUGIN.poll_handler_for("messaging.send.v1")
    with pytest.raises(ValueError):
        PLUGIN.handler_for("messaging.templates.read.v1")
    with pytest.raises(ValueError):
        PLUGIN.ingress_handler_for("messaging.templates.read.v1")
