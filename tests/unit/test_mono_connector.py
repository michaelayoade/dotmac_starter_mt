from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from dotmac_connector_mono import MANIFEST, __version__
from dotmac_connector_mono.plugin import (
    API_SECRET_KEY,
    CAPABILITY_ID,
    MonoPlugin,
    MonoProtocolError,
    MonoRequestError,
)
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.spi import ConnectorMode, PollHandler


def _config(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "account_id": "acc_123",
        "currency": "NGN",
        "page_size": 100,
        "start_date": "01-08-2026",
    }
    value.update(overrides)
    return value


def _response(*, next_url: str | None = None) -> dict[str, object]:
    return {
        "status": "successful",
        "data": [
            {
                "id": "txn_1",
                "narration": "NIP TRANSFER",
                "amount": 125050,
                "type": "credit",
                "balance": 900000,
                "date": "2026-08-20T10:30:00.000Z",
                "category": "transfer",
            },
            {
                "id": "txn_2",
                "narration": "CARD PAYMENT",
                "amount": 5000,
                "type": "debit",
                "balance": None,
                "date": "2026-08-20T11:30:00.000Z",
                "category": "card",
            },
        ],
        "meta": {"page": 1, "total": 2, "next": next_url},
    }


def _plugin(handler) -> MonoPlugin:
    return MonoPlugin(transport=httpx.MockTransport(handler))


def _poll(plugin: MonoPlugin) -> PollHandler:
    return plugin.poll_handler_for(CAPABILITY_ID)


def test_manifest_is_a_poll_only_exact_host_contract() -> None:
    plugin = _plugin(lambda request: httpx.Response(200, json={"data": {}}))
    assert MANIFEST.connector_key == "mono"
    assert MANIFEST.version == __version__ == "0.1.0a1"
    assert MANIFEST.capability_ids == {CAPABILITY_ID}
    assert plugin.modes == frozenset({ConnectorMode.POLL})
    assert tuple(binding.name for binding in MANIFEST.secret_bindings or ()) == (
        API_SECRET_KEY,
    )
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == ("api.withmono.com",)
    assert_plugin_conforms(plugin)


def test_first_poll_uses_the_official_v2_endpoint_and_declared_material() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_response())

    plugin = _plugin(respond)
    events, cursor = _poll(plugin).poll(
        None,
        config=_config(),
        secrets={API_SECRET_KEY: "held-secret"},
    )

    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "api.withmono.com"
    assert request.url.path == "/v2/accounts/acc_123/transactions"
    assert request.url.params["limit"] == "100"
    assert request.url.params["paginate"] == "true"
    assert request.url.params["start"] == "01-08-2026"
    assert request.headers["mono-sec-key"] == "held-secret"
    assert [event.provider_event_id for event in events] == ["txn_1", "txn_2"]
    assert cursor is not None


def test_transactions_preserve_lowest_denomination_and_provider_direction() -> None:
    plugin = _plugin(lambda request: httpx.Response(200, json=_response()))
    events, _ = _poll(plugin).poll(
        None,
        config=_config(),
        secrets={API_SECRET_KEY: "held-secret"},
    )

    assert events[0].event_type == CAPABILITY_ID
    assert events[0].payload == {
        "capability_id": CAPABILITY_ID,
        "provider_account_id": "acc_123",
        "provider_transaction_id": "txn_1",
        "amount_minor": "125050",
        "currency": "NGN",
        "direction": "credit",
        "occurred_at": "2026-08-20T10:30:00.000Z",
        "narration": "NIP TRANSFER",
        "balance_minor": "900000",
        "category": "transfer",
        "arrival_mode": "poll",
    }
    assert events[1].payload["direction"] == "debit"
    assert "balance_minor" not in events[1].payload
    assert "amount" not in events[0].payload


def test_same_origin_pagination_is_normalized_before_it_becomes_a_cursor() -> None:
    next_url = (
        "https://api.withmono.com/v2/accounts/acc_123/transactions?page=2&limit=100"
    )
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_response(next_url=next_url if len(seen) == 1 else None),
        )

    plugin = _plugin(respond)
    _, cursor = _poll(plugin).poll(
        None,
        config=_config(),
        secrets={API_SECRET_KEY: "held-secret"},
    )
    assert cursor is not None
    state = json.loads(cursor)
    assert state["next"] == ("/v2/accounts/acc_123/transactions?page=2&limit=100")
    _poll(plugin).poll(
        cursor,
        config=_config(),
        secrets={API_SECRET_KEY: "held-secret"},
    )
    assert seen[1].url.host == "api.withmono.com"
    assert seen[1].url.path == "/v2/accounts/acc_123/transactions"
    assert seen[1].url.params["page"] == "2"


def test_off_origin_pagination_is_refused_before_a_secret_can_follow_it() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(
            200,
            json=_response(next_url="https://attacker.invalid/collect?page=2"),
        )
    )

    with pytest.raises(MonoProtocolError, match="pagination origin"):
        _poll(plugin).poll(
            None,
            config=_config(),
            secrets={API_SECRET_KEY: "held-secret"},
        )


@pytest.mark.parametrize(
    "replacement",
    [
        {"amount": None},
        {"amount": -1},
        {"type": "incoming"},
        {"date": ""},
        {"id": ""},
    ],
)
def test_a_malformed_money_fact_refuses_the_whole_page(
    replacement: dict[str, object],
) -> None:
    payload = _response()
    data = cast(list[dict[str, object]], payload["data"])
    data[1].update(replacement)
    plugin = _plugin(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(MonoProtocolError):
        _poll(plugin).poll(
            None,
            config=_config(),
            secrets={API_SECRET_KEY: "held-secret"},
        )


def test_provider_errors_do_not_render_response_or_secret_material() -> None:
    plugin = _plugin(
        lambda request: httpx.Response(
            401,
            text="held-secret provider-private-body",
        )
    )
    with pytest.raises(MonoRequestError) as caught:
        _poll(plugin).poll(
            None,
            config=_config(),
            secrets={API_SECRET_KEY: "held-secret"},
        )
    rendered = str(caught.value)
    assert "held-secret" not in rendered
    assert "provider-private-body" not in rendered


def test_connection_validation_is_live_but_material_free() -> None:
    healthy = _plugin(
        lambda request: httpx.Response(200, json={"data": {"id": "acc_123"}})
    )
    assert (
        healthy.validate_connection(
            config=_config(),
            secrets={API_SECRET_KEY: "held-secret"},
        )
        == ()
    )

    denied = _plugin(lambda request: httpx.Response(401, json={"message": "no"}))
    diagnostics = denied.validate_connection(
        config=_config(),
        secrets={API_SECRET_KEY: "held-secret"},
    )
    assert tuple(item.code for item in diagnostics) == ("authentication_rejected",)
    assert "held-secret" not in repr(diagnostics)
