from __future__ import annotations

import json

import httpx
from dotmac_connector_flutterwave.plugin import (
    API_CLIENT_ID,
    API_CLIENT_SECRET,
    FlutterwaveConnector,
)
from dotmac_connector_flutterwave.plugin import (
    CAPABILITY_ID as FLUTTERWAVE_CAPABILITY,
)
from dotmac_connector_paystack.plugin import (
    API_SECRET_KEY,
    PaystackConnector,
)
from dotmac_connector_paystack.plugin import (
    CAPABILITY_ID as PAYSTACK_CAPABILITY,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import ConnectorMode


def test_paystack_adds_poll_without_invalidating_the_published_manifest() -> None:
    plugin = PaystackConnector(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [], "meta": {}})
        )
    )
    assert plugin.manifest.version == "0.1.0a2"
    assert tuple(item.version for item in plugin.historical_manifests) == ("0.1.0a1",)
    assert plugin.historical_manifests[0].capabilities[0].config_schema == {
        "type": "object",
        "additionalProperties": False,
    }
    assert plugin.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.POLL})
    assert plugin.manifest.egress is not None
    assert plugin.manifest.egress.hosts == ("api.paystack.co",)
    assert_plugin_conforms(plugin)


def test_paystack_reconciliation_pages_transactions_with_exact_subunits() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": [
                    {
                        "id": 4099260516,
                        "status": "success",
                        "reference": "merchant-ref-1",
                        "amount": 40333,
                        "fees": 333,
                        "currency": "NGN",
                        "paid_at": "2026-08-20T10:00:00.000Z",
                        "created_at": "2026-08-20T09:59:00.000Z",
                    }
                ],
                "meta": {"page": 1, "pageCount": 2},
            },
        )

    plugin = PaystackConnector(transport=httpx.MockTransport(respond))
    events, cursor = plugin.poll_handler_for(PAYSTACK_CAPABILITY).poll(
        None,
        config={"reconcile_from": "2026-08-20T00:00:00Z", "page_size": 50},
        secrets={API_SECRET_KEY: "held-api-secret"},
    )
    request = seen[0]
    assert request.url == httpx.URL(
        "https://api.paystack.co/transaction?perPage=50&page=1&from=2026-08-20T00%3A00%3A00Z"
    )
    assert request.headers["authorization"] == "Bearer held-api-secret"
    assert events[0].payload["amount"] == {"amount": "403.33", "currency": "NGN"}
    assert events[0].payload["arrival_mode"] == "poll"
    assert json.loads(cursor or "{}")["page"] == 2


def test_flutterwave_adds_v4_poll_without_a_v3_fallback() -> None:
    plugin = FlutterwaveConnector(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"access_token": "short-lived"}
                if request.url.host == "idp.flutterwave.com"
                else {"data": [], "meta": {}},
            )
        )
    )
    assert plugin.manifest.version == "0.1.0a2"
    assert tuple(item.version for item in plugin.historical_manifests) == ("0.1.0a1",)
    assert plugin.historical_manifests[0].capabilities[0].config_schema == {
        "type": "object",
        "additionalProperties": False,
    }
    assert plugin.modes == frozenset({ConnectorMode.INGRESS, ConnectorMode.POLL})
    assert plugin.manifest.egress is not None
    assert plugin.manifest.egress.hosts == (
        "developersandbox-api.flutterwave.com",
        "f4bexperience.flutterwave.com",
        "idp.flutterwave.com",
    )
    assert_plugin_conforms(plugin)


def test_flutterwave_v4_reconciliation_uses_oauth_and_list_charges() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "idp.flutterwave.com":
            return httpx.Response(200, json={"access_token": "short-lived-token"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "chg_1",
                        "status": "succeeded",
                        "reference": "merchant-ref-1",
                        "amount": 2500.25,
                        "currency": "KES",
                        "created_datetime": "2026-08-21T10:00:00Z",
                    }
                ],
                "meta": {"page": 1, "total_pages": 1},
            },
        )

    plugin = FlutterwaveConnector(transport=httpx.MockTransport(respond))
    events, cursor = plugin.poll_handler_for(FLUTTERWAVE_CAPABILITY).poll(
        None,
        config={
            "environment": "live",
            "reconcile_from": "2026-08-21T00:00:00Z",
            "page_size": 20,
        },
        secrets={API_CLIENT_ID: "held-client-id", API_CLIENT_SECRET: "held-secret"},
    )
    token_request, charges_request = seen
    assert token_request.url.host == "idp.flutterwave.com"
    assert b"grant_type=client_credentials" in token_request.content
    assert charges_request.url.host == "f4bexperience.flutterwave.com"
    assert charges_request.url.path == "/charges"
    assert charges_request.headers["authorization"] == "Bearer short-lived-token"
    assert events[0].payload["amount"] == {"amount": "2500.25", "currency": "KES"}
    assert events[0].payload["arrival_mode"] == "poll"
    assert cursor is not None
