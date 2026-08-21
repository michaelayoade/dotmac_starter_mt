from __future__ import annotations

import hashlib

import httpx
import pytest
from dotmac_connector_remita import MANIFEST, __version__
from dotmac_connector_remita.plugin import (
    API_KEY,
    CAPABILITY_ID,
    RemitaPlugin,
    RemitaProtocolError,
    RemitaRequestError,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import ConnectorMode


def _config(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "merchant_id": "2547916",
        "environment": "live",
        "rrrs": ["310007769676"],
    }
    value.update(overrides)
    return value


def _plugin(handler) -> RemitaPlugin:
    return RemitaPlugin(transport=httpx.MockTransport(handler))


def test_manifest_is_poll_only_and_declares_both_fixed_provider_hosts() -> None:
    plugin = _plugin(lambda request: httpx.Response(200, json={"status": "00"}))
    assert MANIFEST.connector_key == "remita"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.capability_ids == {CAPABILITY_ID}
    assert plugin.modes == frozenset({ConnectorMode.POLL})
    assert tuple(item.name for item in MANIFEST.secret_bindings or ()) == (API_KEY,)
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == ("demo.remita.net", "login.remita.net")
    assert_plugin_conforms(plugin)


def test_status_poll_uses_the_documented_sha512_contract() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "RRR": "310007769676",
                "status": "00",
                "message": "Successful",
                "amount": "1250.50",
                "transactionId": "tx-1",
                "paymentDate": "2026-08-20T10:30:00Z",
            },
        )

    plugin = _plugin(respond)
    events, cursor = plugin.poll_handler_for(CAPABILITY_ID).poll(
        None,
        config=_config(),
        secrets={API_KEY: "held-api-key"},
    )

    expected = hashlib.sha512(b"310007769676held-api-key2547916").hexdigest()
    request = seen[0]
    assert request.url.host == "login.remita.net"
    assert request.url.path.endswith(f"/2547916/310007769676/{expected}/status.reg")
    assert request.headers["authorization"] == (
        f"remitaConsumerKey=2547916,remitaConsumerToken={expected}"
    )
    assert len(events) == 1
    # Status is a repeatable observation, not a consumed feed position. The
    # engine still versions the checkpoint claim; the connector has no cursor
    # value to invent.
    assert cursor is None


def test_status_is_carried_verbatim_without_a_payment_decision() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(
            200,
            text='jsonp({"RRR":"310007769676","status":"01",'
            '"message":"Successful","amount":"1250.50"})',
        )
    )
    events, _ = plugin.poll_handler_for(CAPABILITY_ID).poll(
        None,
        config=_config(),
        secrets={API_KEY: "held-api-key"},
    )

    payload = events[0].payload
    assert payload["provider_status"] == "01"
    assert payload["amount"] == "1250.50"
    assert payload["currency"] == "NGN"
    assert "paid" not in payload
    assert "pending" not in payload
    assert "failed" not in payload


def test_an_unchanged_status_has_a_stable_provider_event_identity() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(
            200,
            json={"RRR": "310007769676", "status": "00", "amount": "10.00"},
        )
    )
    handler = plugin.poll_handler_for(CAPABILITY_ID)
    first, cursor = handler.poll(
        None, config=_config(), secrets={API_KEY: "held-api-key"}
    )
    second, _ = handler.poll(
        cursor, config=_config(), secrets={API_KEY: "held-api-key"}
    )
    assert first[0].provider_event_id == second[0].provider_event_id


def test_malformed_provider_amount_refuses_the_complete_poll() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(
            200,
            json={"RRR": "310007769676", "status": "00", "amount": "NaN"},
        )
    )
    with pytest.raises(RemitaProtocolError, match="amount"):
        plugin.poll_handler_for(CAPABILITY_ID).poll(
            None, config=_config(), secrets={API_KEY: "held-api-key"}
        )


def test_provider_errors_never_render_material_or_response_body() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(401, text="held-api-key provider-private")
    )
    with pytest.raises(RemitaRequestError) as caught:
        plugin.poll_handler_for(CAPABILITY_ID).poll(
            None, config=_config(), secrets={API_KEY: "held-api-key"}
        )
    assert "held-api-key" not in str(caught.value)
    assert "provider-private" not in str(caught.value)
