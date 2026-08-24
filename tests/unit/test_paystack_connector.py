from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

import pytest
from dotmac_connector_paystack import MANIFEST, PLUGIN, __version__
from dotmac_connector_paystack.plugin import (
    CAPABILITY_ID,
    WEBHOOK_SIGNING_PREVIOUS_SECRET,
    WEBHOOK_SIGNING_SECRET,
    PayloadInvalid,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import (
    ConnectorMode,
    InboundDisposition,
    IngressRequest,
    VerificationResult,
)

PRIMARY = "primary-paystack-signing-material"
PREVIOUS = "previous-paystack-signing-material"


def _handler():
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
    header_name: str = "X-Paystack-Signature",
) -> IngressRequest:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return IngressRequest(raw_body=raw, headers={header_name: signature})


def _charge_success(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 4_099_260_516,
        "status": "success",
        "reference": "merchant-ref-1",
        "amount": 40_333,
        "fees": 333,
        "currency": "NGN",
        "paid_at": "2026-08-20T10:00:00.000Z",
        "metadata": {
            "invoice_id": "must-not-cross-the-transport-boundary",
            "account_id": "nor-this",
        },
    }
    data.update(overrides)
    return {"event": "charge.success", "data": data}


def test_manifest_is_the_versioned_ingress_runtime_contract() -> None:
    assert MANIFEST.connector_key == "paystack"
    assert MANIFEST.version == __version__ == "0.1.0a2"
    # The observation contract is unchanged by the outbound slice, and it is
    # still mapped to the two modes that OBSERVE. The command capabilities and
    # their DELIVERY mapping are asserted in
    # `tests/unit/test_paystack_outbound_operations.py`.
    assert CAPABILITY_ID in MANIFEST.capability_ids
    observation = next(
        capability
        for capability in MANIFEST.capabilities
        if capability.capability_id == CAPABILITY_ID
    )
    assert observation.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.POLL})
    # SPI 1.4 is what makes a per-capability mode mapping expressible, and a
    # multi-mode connector without one has conformance calling an ingress
    # factory for a delivery-only contract.
    assert MANIFEST.spi_range.minimum.minor == 4
    assert PLUGIN.modes == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )
    assert tuple(binding.name for binding in MANIFEST.secret_bindings or ()) == (
        WEBHOOK_SIGNING_SECRET,
        WEBHOOK_SIGNING_PREVIOUS_SECRET,
        "api_secret_key",
    )
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == ("api.paystack.co",)
    assert_plugin_conforms(PLUGIN)


def test_connection_validation_requires_only_the_declared_primary_material() -> None:
    assert PLUGIN.validate_connection(config={}, secrets=_secrets()) == ()
    diagnostics = PLUGIN.validate_connection(config={}, secrets={})
    assert tuple(item.code for item in diagnostics) == (
        "required_material_unavailable",
    )
    assert WEBHOOK_SIGNING_SECRET not in repr(diagnostics)


def test_paystack_has_no_subscription_challenge() -> None:
    answer = _handler().challenge(
        IngressRequest(params={"challenge": "not-a-paystack-operation"}),
        config={},
        secrets=_secrets(),
    )
    assert answer is None


def test_verification_covers_exact_bytes_and_reports_rotation_position() -> None:
    request = _request(_charge_success(), secret=PREVIOUS)

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


def test_verification_evaluates_every_active_secret_in_constant_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(
        "dotmac_connector_paystack.plugin.hmac.compare_digest",
        recording_compare,
    )
    result = _handler().verify(
        _request(_charge_success()),
        config={},
        secrets=_secrets(),
    )

    assert result == VerificationResult(
        accepted=True,
        matched_secret_positions=(0,),
    )
    assert len(calls) == 2
    assert all(PRIMARY not in pair and PREVIOUS not in pair for pair in calls)


def test_a_missing_malformed_or_ambiguous_signature_fails_closed() -> None:
    body = _request(_charge_success()).raw_body
    for headers in (
        {},
        {"X-Paystack-Signature": "not-hex"},
        {
            "X-Paystack-Signature": "0" * 128,
            "x-paystack-signature": "1" * 128,
        },
    ):
        result = _handler().verify(
            IngressRequest(raw_body=body, headers=headers),
            config={},
            secrets=_secrets(),
        )
        assert result == VerificationResult(accepted=False)


def test_charge_success_becomes_one_exact_provider_neutral_observation() -> None:
    events, acknowledgement = _handler().normalize(
        _request(_charge_success()), config={}
    )

    assert len(events) == 1
    event = events[0]
    assert event.provider_event_id == "charge.success:4099260516"
    assert event.event_type == CAPABILITY_ID
    assert event.disposition is InboundDisposition.DELIVER
    assert event.payload == {
        "capability_id": CAPABILITY_ID,
        "observation_kind": "capture",
        "provider_status": "success",
        "amount": {"amount": "403.33", "currency": "NGN"},
        "provider_fee": {"amount": "3.33", "currency": "NGN"},
        "merchant_reference": "merchant-ref-1",
        "occurred_at": "2026-08-20T10:00:00.000Z",
        "arrival_mode": "ingress",
        "confirmation_evidence": "connector_verified",
        "transport_evidence": {
            "provider_event_type": "charge.success",
            "provider_transaction_id": "4099260516",
            "identity_source": "derived_from_provider_fields",
        },
    }
    assert acknowledgement is not None
    assert acknowledgement.body == b""


def test_xof_uses_paystacks_wire_scale_without_inventing_fractional_money() -> None:
    events, _ = _handler().normalize(
        _request(_charge_success(amount=100, fees=0, currency="XOF")), config={}
    )

    assert events[0].payload["amount"] == {
        "amount": "1.00",
        "currency": "XOF",
    }


def test_provider_metadata_cannot_address_a_product() -> None:
    event = _handler().normalize(_request(_charge_success()), config={})[0][0]

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


def test_a_verified_non_settlement_event_is_recorded_but_not_delivered() -> None:
    payload = {
        "event": "customeridentification.success",
        "data": {"id": 77, "status": "success"},
    }

    events, acknowledgement = _handler().normalize(_request(payload), config={})

    assert len(events) == 1
    event = events[0]
    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.provider_event.recorded.v1"
    assert event.payload["reason_code"] == "event_not_in_ingress_slice"
    assert event.payload["provider_status"] == "success"
    assert acknowledgement is not None and acknowledgement.body == b""


@pytest.mark.parametrize(
    "replacement",
    [
        {"amount": "40.33"},
        {"amount": 40_333, "currency": ""},
        {"amount": 40_333, "status": ""},
        {"amount": 40_333, "paid_at": None},
    ],
)
def test_a_verified_malformed_settlement_is_evidence_not_a_money_fact(
    replacement: dict[str, object],
) -> None:
    event = _handler().normalize(_request(_charge_success(**replacement)), config={})[
        0
    ][0]

    assert event.disposition is InboundDisposition.RECORD_ONLY
    assert event.event_type == "payments.settlement.malformed.v1"
    assert event.payload["reason_code"] == "settlement_shape_invalid"


def test_identity_falls_back_to_one_canonical_event_not_the_request_bytes() -> None:
    payload = _charge_success(id=None, reference=None)
    first = _handler().normalize(_request(payload), config={})[0][0]
    data = payload["data"]
    assert isinstance(data, Mapping)
    reordered = {
        "data": dict(reversed(list(data.items()))),
        "event": "charge.success",
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
