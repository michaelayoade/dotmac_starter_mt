"""Acceptance tests for the first provider connector, over the prebuilt corpus."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from dotmac_connector_whatsapp import MANIFEST, PLUGIN, __version__
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.discovery import ConnectorRegistry
from dotmac_integration.runtime_policy import derive_runtime_policy
from dotmac_integration.spi import (
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
    assert __version__ == "0.1.0a2"
    assert MANIFEST.version == __version__
    assert str(MANIFEST.spi_range) == ">=1.3,<2.0"
    assert_plugin_conforms(PLUGIN)


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
    )
    assert MANIFEST.egress == EgressDeclaration()
    assert MANIFEST.capabilities[0].config_schema == {
        "type": "object",
        "additionalProperties": False,
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

    assert policy.egress_hosts == ()
    assert policy.secret_bindings == (
        ("meta_whatsapp", "webhook_signing_previous_secret", False),
        ("meta_whatsapp", "webhook_signing_secret", True),
        ("meta_whatsapp", "webhook_verify_token", True),
    )


def test_the_published_a1_contract_remains_an_exact_historical_manifest() -> None:
    assert len(PLUGIN.historical_manifests) == 1
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
