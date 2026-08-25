from __future__ import annotations

import ast
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from dotmac_connector_whatsapp import MANIFEST, PLUGIN, connector
from dotmac_integration import InboundDisposition, IngressRequest
from dotmac_integration.conformance import assert_plugin_conforms

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "meta_whatsapp"
CORPUS_MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
CURRENT = "current-signing-material"
PREVIOUS = "previous-signing-material"
VERIFY = "subscription-verify-material"
CONFIG: dict[str, object] = {
    "signing_slots": ["current", "previous"],
    "challenge_slot": "verify",
}
MATERIAL = {"current": CURRENT, "previous": PREVIOUS, "verify": VERIFY}


def _body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _signature(body: bytes, material: str = CURRENT) -> str:
    return "sha256=" + hmac.new(material.encode(), body, hashlib.sha256).hexdigest()


def _request(
    body: bytes,
    *,
    signature: str | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> IngressRequest:
    chosen = dict(headers or {})
    if signature is not None:
        chosen["X-Hub-Signature-256"] = signature
    return IngressRequest(raw_body=body, headers=chosen, params=params or {})


def _handler():
    return PLUGIN.ingress_handler_for("messaging.receive.v1")


def test_the_installed_plugin_contract_conforms() -> None:
    assert_plugin_conforms(PLUGIN)
    assert MANIFEST.connector_key == "meta_whatsapp"
    assert MANIFEST.capability_ids == {"messaging.receive.v1"}
    assert {mode.value for mode in PLUGIN.modes} == {"ingress"}


def test_meta_signature_covers_the_exact_raw_body() -> None:
    body = _body("message.json")
    handler = _handler()

    assert handler.verify(
        _request(body, signature=_signature(body)),
        config=CONFIG,
        secrets=MATERIAL,
    )
    assert not handler.verify(
        _request(body + b"\n", signature=_signature(body)),
        config=CONFIG,
        secrets=MATERIAL,
    )


def test_rotation_checks_every_signing_slot_without_short_circuiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _body("message.json")
    signature = _signature(body, PREVIOUS)
    calls = 0
    real_new = connector.hmac.new

    def counted_new(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        return real_new(*args, **kwargs)

    monkeypatch.setattr(connector.hmac, "new", counted_new)

    assert _handler().verify(
        _request(body, signature=signature),
        config=CONFIG,
        secrets=MATERIAL,
    )
    assert calls == 2


def test_an_ambiguous_or_malformed_signature_header_is_refused() -> None:
    body = _body("message.json")
    signature = _signature(body)
    handler = _handler()

    assert not handler.verify(
        _request(
            body,
            headers={
                "X-Hub-Signature-256": signature,
                "x-hub-signature-256": signature,
            },
        ),
        config=CONFIG,
        secrets=MATERIAL,
    )
    assert not handler.verify(
        _request(body, signature="sha256=not-hex"),
        config=CONFIG,
        secrets=MATERIAL,
    )


def test_the_subscription_handshake_is_separate_from_delivery() -> None:
    handler = _handler()
    request = _request(
        b"",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY,
            "hub.challenge": "challenge-123",
        },
    )

    acknowledgement = handler.challenge(request, config=CONFIG, secrets=MATERIAL)

    assert acknowledgement is not None
    assert acknowledgement.body == b"challenge-123"
    assert acknowledgement.media_type == "text/plain"
    assert not handler.verify(request, config=CONFIG, secrets=MATERIAL)


def test_a_wrong_handshake_token_is_refused_without_echoing_it() -> None:
    request = _request(
        b"",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "do-not-echo",
        },
    )
    assert _handler().challenge(request, config=CONFIG, secrets=MATERIAL) is None


def test_a_text_message_ports_the_qualifying_sub_shape() -> None:
    body = _body("message.json")
    events, acknowledgement = _handler().normalize(_request(body), config=CONFIG)

    assert acknowledgement is not None
    assert acknowledgement.body == b'{"status":"ok"}'
    assert len(events) == 1
    event = events[0]
    assert event.provider_event_id == "wa:msg:wamid.meta-1"
    assert event.event_type == "whatsapp.message.received.v1"
    assert event.disposition is InboundDisposition.DELIVER
    assert "provider_event_id" not in event.payload
    assert event.payload == {
        "provider": "meta_cloud_api",
        "provider_account_scope": "phone-number-id",
        "channel": "whatsapp",
        "observed_at": "2026-07-10T08:00:00+00:00",
        "message": {
            "contact_address": "2348035550114",
            "body": "My internet is down",
            "contact_name": "Ada Nwosu",
            "subject": None,
            "external_message_id": "wamid.meta-1",
            "external_thread_id": None,
            "provider_account_id": "phone-number-id",
            "external_account_id": None,
            "page_id": None,
            "instagram_account_id": None,
            "surface": None,
            "permalink_url": None,
            "media_url": None,
            "contact_profile": None,
            "attachments": [],
        },
        "transport_evidence": {
            "locator": "/entry/0/changes/0/value/messages/0",
            "identity_source": "provider",
            "reason_code": None,
        },
    }


def test_attachment_only_location_preserves_coordinates_without_inventing_body() -> (
    None
):
    events, _ = _handler().normalize(_request(_body("location.json")), config=CONFIG)

    message = events[0].payload["message"]
    assert message["body"] is None
    assert message["attachments"][0]["location"] == {
        "latitude": 6.5243793,
        "longitude": 3.3792057,
        "name": "Customer location",
        "address": "Lagos, Nigeria",
    }


def test_delivery_status_is_a_provider_neutral_receipt_observation() -> None:
    events, _ = _handler().normalize(_request(_body("status.json")), config=CONFIG)

    assert events[0].provider_event_id == (
        "wa:status:wamid.outbound-1:failed:1783670600"
    )
    assert events[0].event_type == "whatsapp.message.status.v1"
    assert events[0].payload["delivery_receipt"] == {
        "external_message_id": "wamid.outbound-1",
        "status": "failed",
        "recipient_id": "2348035550114",
        "error_codes": ["131026"],
    }


def test_unsupported_and_malformed_facts_are_durable_but_not_deliverable() -> None:
    payload = json.loads(_body("message.json"))
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "contacts"
    message["contacts"] = [{"name": {"formatted_name": "Customer"}}]
    first = json.dumps(payload, separators=(",", ":")).encode()
    second = json.dumps(payload, indent=2).encode()

    first_event = _handler().normalize(_request(first), config=CONFIG)[0][0]
    second_event = _handler().normalize(_request(second), config=CONFIG)[0][0]

    assert first_event.disposition is InboundDisposition.RECORD_ONLY
    assert first_event.payload["transport_evidence"]["reason_code"] == (
        "message_type_unsupported"
    )
    assert first_event.provider_event_id == second_event.provider_event_id
    assert hashlib.sha256(first).hexdigest() != hashlib.sha256(second).hexdigest()


def test_invalid_json_is_refused_instead_of_inventing_a_request_identity() -> None:
    for body in (b"{", b"not-json"):
        with pytest.raises(connector.WhatsAppPayloadInvalid) as refused:
            _handler().normalize(_request(body), config=CONFIG)
        assert refused.value.args == ()


def test_connection_validation_names_missing_slots_not_material() -> None:
    sentinel = "credential-that-must-never-render"
    diagnostics = PLUGIN.validate_connection(
        config=CONFIG,
        secrets={"current": sentinel, "verify": VERIFY},
    )

    assert diagnostics[0].ok is False
    assert diagnostics[0].code == "whatsapp_material_missing"
    assert sentinel not in repr(diagnostics)


@pytest.mark.parametrize(
    "case",
    CORPUS_MANIFEST["fixtures"],
    ids=lambda case: Path(case["file"]).stem,
)
def test_the_authorized_corpus_matches_the_connector(case: dict[str, Any]) -> None:
    body = (CORPUS / case["file"]).read_bytes()
    events, acknowledgement = _handler().normalize(_request(body), config=CONFIG)

    assert acknowledgement is not None
    actual = [
        {
            "provider_event_id": event.provider_event_id,
            "event_type": event.event_type,
            **event.payload["transport_evidence"],
        }
        for event in events
    ]
    expected = [
        {
            "provider_event_id": observation["provider_event_id"],
            "event_type": observation["event_type"],
            "locator": observation["locator"],
            "identity_source": observation["identity_source"],
            "reason_code": observation.get("reason_code"),
        }
        for observation in case["expected_observations"]
    ]
    assert actual == expected


def test_the_connector_owns_no_persistence_network_or_private_engine() -> None:
    source = Path(connector.__file__).read_text(encoding="utf-8")
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }

    assert imports.isdisjoint({"sqlalchemy", "alembic", "httpx", "requests", "fastapi"})
    internal = {name for name in imports if name == "app" or name.startswith("dotmac_")}
    assert internal == {"dotmac_integration"}
    assert not any(
        marker in path.stem
        for path in Path(connector.__file__).parent.glob("*.py")
        for marker in ("retry", "backoff", "checkpoint", "dead_letter")
    )
