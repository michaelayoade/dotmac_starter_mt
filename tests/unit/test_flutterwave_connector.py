from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import cast

import pytest
from dotmac_connector_flutterwave import MANIFEST, PLUGIN, __version__
from dotmac_connector_flutterwave.plugin import (
    CAPABILITY_ID,
    HMAC_SHA256,
    WEBHOOK_SIGNING_PREVIOUS_SECRET,
    WEBHOOK_SIGNING_SECRET,
    PayloadInvalid,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import (
    ConnectorMode,
    InboundDisposition,
    IngressHandler,
    IngressRequest,
    VerificationResult,
)

PRIMARY = "primary-flutterwave-v4-signing-material"
PREVIOUS = "previous-flutterwave-v4-signing-material"


def _handler() -> IngressHandler:
    return PLUGIN.ingress_handler_for(CAPABILITY_ID)


def _secrets() -> dict[str, str]:
    return {
        WEBHOOK_SIGNING_SECRET: PRIMARY,
        WEBHOOK_SIGNING_PREVIOUS_SECRET: PREVIOUS,
    }


def _request(
    payload: object,
    *,
    secret: str = PRIMARY,
    header_name: str = "Flutterwave-Signature",
) -> IngressRequest:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    ).decode()
    return IngressRequest(raw_body=raw, headers={header_name: signature})


def _charge_completed(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "chg_Hq4oBRTJ4r",
        "status": "succeeded",
        "reference": "merchant-ref-1",
        "amount": 2500.25,
        "currency": "KES",
        "created_datetime": "2026-08-21T10:00:00.000Z",
        "meta": {
            "invoice_id": "must-not-cross-the-transport-boundary",
            "account_id": "nor-this",
        },
    }
    data.update(overrides)
    return {
        "webhook_id": "wbk_W5p6ktwU0jQ8RO4By860",
        "timestamp": 1_787_306_400_000,
        "type": "charge.completed",
        "data": data,
    }


def test_manifest_is_the_versioned_v4_ingress_contract() -> None:
    assert MANIFEST.connector_key == "flutterwave"
    assert MANIFEST.version == __version__ == "0.1.0a2"
    assert MANIFEST.capability_ids == {CAPABILITY_ID}
    assert MANIFEST.spi_range.minimum.minor == 3
    assert PLUGIN.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.POLL})
    assert tuple(binding.name for binding in MANIFEST.secret_bindings or ()) == (
        WEBHOOK_SIGNING_SECRET,
        WEBHOOK_SIGNING_PREVIOUS_SECRET,
        "api_client_id",
        "api_client_secret",
    )
    assert MANIFEST.capabilities[0].config_schema["additionalProperties"] is False
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == (
        "developersandbox-api.flutterwave.com",
        "f4bexperience.flutterwave.com",
        "idp.flutterwave.com",
    )
    assert_plugin_conforms(PLUGIN)


def test_connection_validation_requires_only_primary_v4_signing_material() -> None:
    assert (
        PLUGIN.validate_connection(
            config={}, secrets=cast(dict[str, object], _secrets())
        )
        == ()
    )
    diagnostics = PLUGIN.validate_connection(config={}, secrets={})
    assert tuple(item.code for item in diagnostics) == (
        "required_material_unavailable",
    )
    assert WEBHOOK_SIGNING_SECRET not in repr(diagnostics)


def test_flutterwave_has_no_subscription_challenge() -> None:
    answer = _handler().challenge(
        IngressRequest(params={"challenge": "not-a-flutterwave-operation"}),
        config={},
        secrets=_secrets(),
    )
    assert answer is None


def test_hmac_verification_covers_exact_bytes_and_reports_rotation_position() -> None:
    request = _request(_charge_completed(), secret=PREVIOUS)

    result = _handler().verify(request, config={}, secrets=_secrets())

    assert result == VerificationResult(
        accepted=True,
        matched_secret_positions=(1,),
    )
    changed = IngressRequest(
        raw_body=request.raw_body + b" ",
        headers=request.headers,
    )
    assert _handler().verify(changed, config={}, secrets=_secrets()) == (
        VerificationResult(accepted=False)
    )


def test_verification_evaluates_every_active_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(
        "dotmac_connector_flutterwave.plugin.hmac.compare_digest",
        recording_compare,
    )
    result = _handler().verify(
        _request(_charge_completed()),
        config={},
        secrets=_secrets(),
    )

    assert result == VerificationResult(
        accepted=True,
        matched_secret_positions=(0,),
    )
    assert len(calls) == 2
    assert all(
        len(left) == len(right) == hashlib.sha256().digest_size for left, right in calls
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Flutterwave-Signature": "not-base64"},
        {"Flutterwave-Signature": "a", "flutterwave-signature": "b"},
        {"verif-hash": PRIMARY},
    ],
)
def test_missing_malformed_ambiguous_or_v3_authentication_fails_closed(
    headers: dict[str, str],
) -> None:
    result = _handler().verify(
        IngressRequest(raw_body=b"{}", headers=headers),
        config={},
        secrets=_secrets(),
    )
    assert result == VerificationResult(accepted=False)


def test_v4_successful_charge_becomes_one_exact_neutral_observation() -> None:
    events, acknowledgement = _handler().normalize(
        _request(_charge_completed()), config={}
    )

    assert len(events) == 1
    event = events[0]
    assert event.provider_event_id == ("charge.completed:wbk_W5p6ktwU0jQ8RO4By860")
    assert event.event_type == CAPABILITY_ID
    assert event.disposition is InboundDisposition.DELIVER
    assert event.payload == {
        "capability_id": CAPABILITY_ID,
        "observation_kind": "capture",
        "provider_status": "succeeded",
        "amount": {"amount": "2500.25", "currency": "KES"},
        "merchant_reference": "merchant-ref-1",
        "occurred_at": "2026-08-21T10:00:00.000Z",
        "arrival_mode": "ingress",
        "confirmation_evidence": "connector_verified",
        "transport_evidence": {
            "provider_event_type": "charge.completed",
            "provider_webhook_id": "wbk_W5p6ktwU0jQ8RO4By860",
            "provider_transaction_id": "chg_Hq4oBRTJ4r",
            "identity_source": "derived_from_provider_fields",
            "authentication_scheme": HMAC_SHA256,
            "payload_integrity": HMAC_SHA256,
        },
    }
    assert "provider_fee" not in event.payload
    assert acknowledgement is not None and acknowledgement.body == b""


def test_v4_webhook_id_alias_is_not_required_for_stable_identity() -> None:
    payload = _charge_completed()
    payload["id"] = payload.pop("webhook_id")

    event = _handler().normalize(_request(payload), config={})[0][0]

    assert event.provider_event_id == ("charge.completed:wbk_W5p6ktwU0jQ8RO4By860")


def test_v4_fractional_unix_created_datetime_is_translated_to_utc() -> None:
    event = _handler().normalize(
        _request(_charge_completed(created_datetime=1735116842.116)), config={}
    )[0][0]

    assert event.payload["occurred_at"] == "2024-12-25T08:54:02.116000Z"


def test_v4_millisecond_event_timestamp_is_the_occurred_at_fallback() -> None:
    event = _handler().normalize(
        _request(_charge_completed(created_datetime=None)), config={}
    )[0][0]

    assert event.payload["occurred_at"] == "2026-08-21T10:00:00.000000Z"


def test_v3_payload_shape_is_not_silently_accepted_by_the_v4_connector() -> None:
    payload = {
        "event": "charge.completed",
        "data": {
            "id": 880010,
            "tx_ref": "FW-FEE-2",
            "amount": "5000.00",
            "app_fee": "70.00",
            "currency": "NGN",
            "status": "successful",
            "created_at": "2026-08-21T11:00:00Z",
        },
    }

    event = _handler().normalize(_request(payload), config={})[0][0]

    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.provider_event.recorded.v1"
    assert event.payload["reason_code"] == "event_not_in_ingress_slice"


def test_failed_charge_is_a_failed_observation_not_a_successful_capture() -> None:
    event = _handler().normalize(
        _request(_charge_completed(status="failed")), config={}
    )[0][0]

    assert event.event_type == CAPABILITY_ID
    assert event.payload["observation_kind"] == "capture_failed"
    assert event.payload["provider_status"] == "failed"


def test_provider_metadata_cannot_address_a_product() -> None:
    event = _handler().normalize(_request(_charge_completed()), config={})[0][0]

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in keys(item)}
        if isinstance(value, list | tuple):
            return {key for item in value for key in keys(item)}
        return set()

    forbidden = {
        "account_id",
        "allocation",
        "balance",
        "balance_due",
        "billing_account_id",
        "coverage",
        "customer_id",
        "invoice_id",
        "net_amount",
        "receivable",
        "scope_kind",
        "subscription_id",
        "tenant_id",
    }
    assert keys(event.payload).isdisjoint(forbidden)


def test_verified_non_settlement_event_is_recorded_but_not_delivered() -> None:
    payload = {
        "type": "transfer.disburse",
        "webhook_id": "wbk-transfer-1",
        "data": {"id": "trf-1", "status": "SUCCESSFUL"},
    }

    events, acknowledgement = _handler().normalize(_request(payload), config={})

    assert len(events) == 1
    event = events[0]
    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.provider_event.recorded.v1"
    assert event.payload["reason_code"] == "event_not_in_ingress_slice"
    assert event.payload["provider_status"] == "SUCCESSFUL"
    assert acknowledgement is not None and acknowledgement.body == b""


@pytest.mark.parametrize(
    "replacement",
    [
        {"amount": "not-money"},
        {"currency": ""},
        {"status": ""},
    ],
)
def test_verified_malformed_settlement_is_evidence_not_a_money_fact(
    replacement: dict[str, object],
) -> None:
    event = _handler().normalize(_request(_charge_completed(**replacement)), config={})[
        0
    ][0]

    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.settlement.malformed.v1"
    assert event.payload["reason_code"] == "settlement_shape_invalid"


def test_verified_settlement_without_any_provider_timestamp_is_not_delivered() -> None:
    payload = _charge_completed(created_datetime=None)
    payload["timestamp"] = None

    event = _handler().normalize(_request(payload), config={})[0][0]

    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.settlement.malformed.v1"
    assert event.payload["reason_code"] == "settlement_shape_invalid"


def test_identity_falls_back_to_one_canonical_event_not_request_bytes() -> None:
    payload = _charge_completed(id=None, reference=None)
    payload["webhook_id"] = None
    first = _handler().normalize(_request(payload), config={})[0][0]
    data = payload["data"]
    assert isinstance(data, Mapping)
    reordered = {
        "data": dict(reversed(list(data.items()))),
        "type": "charge.completed",
        "timestamp": payload["timestamp"],
        "webhook_id": None,
    }
    second = _handler().normalize(_request(reordered), config={})[0][0]

    assert first.provider_event_id == second.provider_event_id
    evidence = first.payload["transport_evidence"]
    assert isinstance(evidence, Mapping)
    assert evidence["identity_source"] == "derived_from_event"


def test_unverified_shape_errors_carry_no_raw_material() -> None:
    secret_marker = "sensitive-body-marker"
    request = IngressRequest(raw_body=secret_marker.encode())

    with pytest.raises(PayloadInvalid) as captured:
        _handler().normalize(request, config={})

    assert secret_marker not in str(captured.value)
    assert secret_marker not in repr(captured.value)
