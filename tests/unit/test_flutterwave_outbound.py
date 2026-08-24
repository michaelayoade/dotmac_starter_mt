"""Flutterwave v4 outbound commands: money, idempotency and the five outcomes.

This is the direction where a mistake takes money from a customer twice, so the
negatives carry the weight here: a decline that retries, a timeout classified as
a failure, and an amount that touched a binary float are each a defect a
green-looking happy path would hide.

Every credential in this file is a fixture string, chosen to be obviously fake.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from dotmac_connector_flutterwave import MANIFEST, PLUGIN
from dotmac_connector_flutterwave.outbound import (
    IDEMPOTENCY_HEADER,
    INTENT_CAPABILITY_ID,
    REFUND_CAPABILITY_ID,
    CommandContractError,
    FlutterwaveDeliveryHandler,
    classify_request_error,
    exact_json,
)
from dotmac_connector_flutterwave.plugin import (
    API_CLIENT_ID,
    API_CLIENT_SECRET,
    CAPABILITY_ID,
    WEBHOOK_SIGNING_SECRET,
    FlutterwaveConnector,
)
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import ConnectorMode, DispatchRequest

CLIENT_ID = "fixture-flutterwave-client-id"
CLIENT_SECRET = "fixture-flutterwave-client-secret"
BEARER = "fixture-bearer-token"
IDEMPOTENCY_KEY = "engine-idempotency-key-1"

SANDBOX = "developersandbox-api.flutterwave.com"
IDENTITY = "idp.flutterwave.com"


def _secrets() -> dict[str, object]:
    return {API_CLIENT_ID: CLIENT_ID, API_CLIENT_SECRET: CLIENT_SECRET}


def _intent(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "intent_reference": "DMAC-INV-0001-ab12cd34",
        "amount": "1250.50",
        "currency": "NGN",
        "currency_minor_units": 2,
        "payer_contact": "payer@example.test",
        "return_url": "https://product.example.test/return",
        "merchant_reference": "opaque-merchant-ref",
    }
    payload.update(overrides)
    return payload


def _refund(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_transaction_id": "chg_Hq4oBRTJ4r",
        "note": "opaque-refund-note",
    }
    payload.update(overrides)
    return payload


def _request(
    payload: dict[str, object],
    *,
    capability_id: str = INTENT_CAPABILITY_ID,
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    return DispatchRequest(
        capability_id=capability_id,
        event_type="payments.command",
        payload=payload,
        config=(
            config
            if config is not None
            # The shape OUTBOUND_CONFIG_SCHEMA declares, both keys required.
            else {"environment": "sandbox", "timeout_seconds": 20}
        ),
        secrets=_secrets(),
        idempotency_key=IDEMPOTENCY_KEY,
    )


class _Provider:
    """Records every request and answers the token call, then one command call."""

    def __init__(
        self,
        command: httpx.Response | Exception,
        *,
        token: httpx.Response | None = None,
    ) -> None:
        self.command = command
        self.token = token or httpx.Response(200, json={"access_token": BEARER})
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.host == IDENTITY:
            return self.token
        if isinstance(self.command, Exception):
            raise self.command
        return self.command


def _run(
    provider: _Provider,
    payload: dict[str, object],
    *,
    capability_id: str = INTENT_CAPABILITY_ID,
    config: dict[str, object] | None = None,
) -> Outcome:
    handler = FlutterwaveDeliveryHandler(httpx.MockTransport(provider))
    return handler(_request(payload, capability_id=capability_id, config=config))


def _ok(**data: object) -> httpx.Response:
    body: dict[str, object] = {"id": "chg_Hq4oBRTJ4r", "status": "pending"}
    body.update(data)
    return httpx.Response(200, json={"data": body})


# ── the manifest ─────────────────────────────────────────────────────────────


def test_the_manifest_maps_each_capability_to_exactly_one_executable_mode() -> None:
    modes = {c.capability_id: c.modes for c in MANIFEST.capabilities}
    assert modes[CAPABILITY_ID] == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL}
    )
    assert modes[INTENT_CAPABILITY_ID] == frozenset({ConnectorMode.DELIVERY})
    assert modes[REFUND_CAPABILITY_ID] == frozenset({ConnectorMode.DELIVERY})
    assert str(MANIFEST.spi_range) == ">=1.4,<2.0"
    assert PLUGIN.modes == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )


def test_outbound_reaches_no_host_the_connector_did_not_already_declare() -> None:
    """An outbound capability is not a licence to widen reachability."""
    assert MANIFEST.egress is not None
    assert set(MANIFEST.egress.hosts) == {
        SANDBOX,
        "f4bexperience.flutterwave.com",
        IDENTITY,
    }


def test_the_published_ingress_only_contract_stays_resolvable() -> None:
    """The adoption window. Without it, an installation pinned to the shipped
    observation-only digest becomes an unknown manifest the moment delivery
    lands."""
    historical = {m.capability_ids for m in PLUGIN.historical_manifests}
    assert {CAPABILITY_ID} in historical


def test_a_delivery_factory_refuses_an_ingress_capability_and_the_reverse() -> None:
    """SENSITIVITY. A factory that answered for any declared capability would
    hand an ingress handler to the dispatch worker and pass discovery."""
    with pytest.raises(ValueError):
        PLUGIN.handler_for(CAPABILITY_ID)
    with pytest.raises(ValueError):
        PLUGIN.ingress_handler_for(INTENT_CAPABILITY_ID)
    with pytest.raises(ValueError):
        PLUGIN.poll_handler_for(REFUND_CAPABILITY_ID)
    assert isinstance(
        PLUGIN.handler_for(INTENT_CAPABILITY_ID), FlutterwaveDeliveryHandler
    )


# ── money never round-trips through a float ──────────────────────────────────


def test_the_standard_encoder_cannot_write_an_exact_amount_at_all() -> None:
    """SENSITIVITY PROOF for `exact_json` existing.

    If `json.dumps` could write a `Decimal`, the hand-written encoder would be
    unnecessary complexity rather than the only exact path — and this test is
    what keeps that claim honest rather than asserted in a docstring.
    """
    with pytest.raises(TypeError):
        json.dumps({"amount": Decimal("1250.50")})


def test_an_exact_amount_is_written_as_a_json_number_with_its_own_digits() -> None:
    assert exact_json({"amount": Decimal("1250.50")}) == b'{"amount":1250.50}'
    # The classic float casualties, written exactly.
    assert exact_json({"a": Decimal("0.1") + Decimal("0.2")}) == b'{"a":0.3}'
    assert exact_json({"a": Decimal("70.70")}) == b'{"a":70.70}'
    # Round-tripping through binary floating point loses each of them.
    assert str(0.1 + 0.2) != "0.3"


def test_the_encoder_refuses_a_binary_floating_point_value() -> None:
    """A coercion here is how an inexact amount reaches a payment wire."""
    with pytest.raises(CommandContractError) as refusal:
        exact_json({"amount": 1250.50})
    assert refusal.value.code == "value_not_exactly_encodable"


def test_a_float_amount_is_refused_terminally_and_nothing_is_sent() -> None:
    provider = _Provider(_ok())
    outcome = _run(provider, _intent(amount=1250.50))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "amount_not_exact"
    assert provider.seen == []


def test_an_amount_finer_than_the_currency_is_refused_not_rounded() -> None:
    """A transport that rounds money has made a financial decision."""
    provider = _Provider(_ok())
    outcome = _run(provider, _intent(amount="1250.505"))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "amount_precision_invalid"
    assert provider.seen == []


def test_the_amount_on_the_wire_is_the_exact_decimal_the_product_supplied() -> None:
    provider = _Provider(_ok())
    _run(provider, _intent(amount=Decimal("1250.50")))
    body = provider.seen[-1].content
    assert b'"amount":1250.50' in body
    # Not a string, and not a float's repr.
    assert b'"amount":"1250.50"' not in body
    assert b"1250.5," not in body


def test_a_zero_minor_unit_currency_is_written_without_a_fraction() -> None:
    """The exponent comes from the PRODUCT, so a currency the connector has
    never heard of works without a table here."""
    provider = _Provider(_ok())
    outcome = _run(
        provider,
        _intent(amount="5000", currency="JPY", currency_minor_units=0),
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert b'"amount":5000' in provider.seen[-1].content
    assert b'"currency":"JPY"' in provider.seen[-1].content


def test_a_command_with_no_currency_is_refused_rather_than_defaulted() -> None:
    """The port delta against ``payment_gateway.py:333``'s
    ``config.get("default_currency") or "NGN"``. A currency assumed three layers
    into a default chain is how an amount is charged in the wrong one."""
    provider = _Provider(_ok())
    payload = _intent()
    del payload["currency"]
    outcome = _run(provider, payload)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "currency_required"
    assert provider.seen == []
    # SPECIFICITY: the same command WITH a currency reaches the provider, so the
    # refusal above is attributable to the missing currency and nothing else.
    assert _run(_Provider(_ok()), _intent()).status is OutcomeStatus.SUCCEEDED


def test_a_partial_refund_amount_without_a_currency_is_refused() -> None:
    provider = _Provider(_ok(id="rfd_1", status="pending"))
    outcome = _run(
        provider,
        _refund(amount="100.00"),
        capability_id=REFUND_CAPABILITY_ID,
    )
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "currency_minor_units_required"
    assert provider.seen == []


# ── the wire ─────────────────────────────────────────────────────────────────


def test_initialize_posts_the_v4_charge_with_the_product_reference() -> None:
    provider = _Provider(_ok())
    outcome = _run(provider, _intent())

    token_call, command = provider.seen
    assert token_call.url.host == IDENTITY
    assert command.method == "POST"
    assert command.url.host == SANDBOX
    assert command.url.path == "/charges"
    assert command.headers["authorization"] == f"Bearer {BEARER}"
    body = json.loads(command.content)
    assert body["reference"] == "DMAC-INV-0001-ab12cd34"
    assert body["currency"] == "NGN"
    assert body["redirect_url"] == "https://product.example.test/return"
    assert body["customer"] == {"email": "payer@example.test"}
    assert body["meta"] == {"merchant_reference": "opaque-merchant-ref"}
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "chg_Hq4oBRTJ4r"
    assert outcome.provider_status_code == 200


def test_a_saved_instrument_rides_as_an_opaque_mandate_reference() -> None:
    provider = _Provider(_ok())
    _run(provider, _intent(mandate_ref="pmd_saved_instrument"))
    body = json.loads(provider.seen[-1].content)
    assert body["payment_method_id"] == "pmd_saved_instrument"


def test_refund_targets_the_provider_charge_and_full_refund_sends_no_amount() -> None:
    provider = _Provider(_ok(id="rfd_1", status="succeeded"))
    outcome = _run(provider, _refund(), capability_id=REFUND_CAPABILITY_ID)

    command = provider.seen[-1]
    assert command.url.path == "/charges/chg_Hq4oBRTJ4r/refunds"
    body = json.loads(command.content)
    # Absence is a MEANING at the provider: no amount is a full refund.
    assert "amount" not in body
    assert body["comments"] == "opaque-refund-note"
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "rfd_1"


def test_a_partial_refund_carries_the_complete_money_triple() -> None:
    provider = _Provider(_ok(id="rfd_2", status="pending"))
    _run(
        provider,
        _refund(amount="100.00", currency="NGN", currency_minor_units=2),
        capability_id=REFUND_CAPABILITY_ID,
    )
    assert b'"amount":100.00' in provider.seen[-1].content
    assert b'"currency":"NGN"' in provider.seen[-1].content


def test_a_charge_id_is_escaped_into_the_refund_path() -> None:
    provider = _Provider(_ok(id="rfd_3", status="pending"))
    _run(
        provider,
        _refund(provider_transaction_id="chg/../../admin"),
        capability_id=REFUND_CAPABILITY_ID,
    )
    assert provider.seen[-1].url.path == "/charges/chg%2F..%2F..%2Fadmin/refunds"


# ── idempotency ──────────────────────────────────────────────────────────────


def test_the_engine_idempotency_key_is_carried_to_the_provider_verbatim() -> None:
    """Flutterwave v4 accepts an idempotency header; the connector mints
    nothing and rewrites nothing."""
    provider = _Provider(_ok())
    _run(provider, _intent())
    assert provider.seen[-1].headers[IDEMPOTENCY_HEADER] == IDEMPOTENCY_KEY


def test_a_refund_carries_the_same_header_rather_than_a_note_substring() -> None:
    """The port delta against the source, whose only refund dedupe was a
    ``request_key`` stuffed into the free-text ``comments`` field and recovered
    later by substring match."""
    provider = _Provider(_ok(id="rfd_4", status="pending"))
    _run(provider, _refund(), capability_id=REFUND_CAPABILITY_ID)
    assert provider.seen[-1].headers[IDEMPOTENCY_HEADER] == IDEMPOTENCY_KEY


# ── outcome classification ───────────────────────────────────────────────────


def test_a_declined_command_is_terminal_and_never_retryable() -> None:
    """The single most important negative here. A decline is a DECISION;
    repeating the identical request cannot change the answer, and a retry on a
    payment command is how a customer is charged twice."""
    for status in (400, 402, 422):
        outcome = _run(_Provider(httpx.Response(status, json={})), _intent())
        assert outcome.status is OutcomeStatus.TERMINAL, status
        assert outcome.status is not OutcomeStatus.RETRYABLE, status
        assert outcome.is_final, status
        assert outcome.error_code == "provider_declined", status


def test_a_two_hundred_carrying_a_declined_status_is_still_a_decline() -> None:
    outcome = _run(_Provider(_ok(status="failed")), _intent())
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "provider_declined"
    # A declined charge is a real object at the provider. Reconciliation must
    # still be able to find it.
    assert outcome.provider_reference == "chg_Hq4oBRTJ4r"


def test_a_timeout_after_the_request_was_sent_is_ambiguous_not_retryable() -> None:
    """The effect may have LANDED. Retrying risks a second charge and a
    dead-letter hides a charge that exists, so neither answer is available."""
    sent = httpx.ReadTimeout("read timed out")
    outcome = _run(_Provider(sent), _intent())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_outcome_ambiguous"
    assert outcome.status is not OutcomeStatus.RETRYABLE
    assert outcome.status is not OutcomeStatus.TERMINAL


def test_a_connect_failure_before_any_byte_left_is_retryable() -> None:
    """SPECIFICITY for the test above. Both are timeouts; only one of them
    happened with bytes on the wire. A classifier that answered ambiguous for
    every transport failure would pass that test and be wrong here."""
    for never_sent in (
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError("refused"),
    ):
        outcome = _run(_Provider(never_sent), _intent())
        assert outcome.status is OutcomeStatus.RETRYABLE
        assert outcome.error_code == "provider_connect_failed"


def test_every_httpx_transport_failure_lands_on_one_of_the_two_answers() -> None:
    """No transport failure may fall through to SUCCEEDED or TERMINAL."""
    for exc in (
        httpx.ReadTimeout("x"),
        httpx.WriteTimeout("x"),
        httpx.PoolTimeout("x"),
        httpx.ReadError("x"),
        httpx.RemoteProtocolError("x"),
        httpx.ConnectTimeout("x"),
        httpx.ConnectError("x"),
    ):
        outcome = classify_request_error(exc)
        assert outcome.status in {
            OutcomeStatus.RETRYABLE,
            OutcomeStatus.RECONCILIATION_REQUIRED,
        }, exc


def test_rate_limiting_and_server_faults_are_retryable() -> None:
    limited = _run(
        _Provider(httpx.Response(429, headers={"retry-after": "12"}, json={})),
        _intent(),
    )
    assert limited.status is OutcomeStatus.RETRYABLE
    assert limited.retry_after_seconds == 12
    assert _run(_Provider(httpx.Response(503, json={})), _intent()).status is (
        OutcomeStatus.RETRYABLE
    )


def test_a_duplicate_reference_is_ambiguous_because_the_first_one_landed() -> None:
    outcome = _run(
        _Provider(httpx.Response(409, json={"data": {"id": "chg_existing"}})),
        _intent(),
    )
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_duplicate_reference"
    assert outcome.provider_reference == "chg_existing"


def test_an_accepted_command_with_nothing_to_correlate_it_by_is_ambiguous() -> None:
    outcome = _run(_Provider(httpx.Response(200, json={"data": {}})), _intent())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_receipt_missing"


def test_an_unreadable_success_body_is_ambiguous_not_a_success() -> None:
    outcome = _run(_Provider(httpx.Response(200, text="<html/>")), _intent())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_response_unreadable"


def test_a_status_token_the_connector_has_never_seen_fails_closed() -> None:
    """Guessing "success" for an unknown token is how a pending charge gets
    booked as a payment."""
    outcome = _run(_Provider(_ok(status="quarantined")), _intent())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_status_unrecognized"
    assert outcome.provider_reference == "chg_Hq4oBRTJ4r"


def test_refused_credentials_are_terminal_and_no_command_is_attempted() -> None:
    provider = _Provider(_ok(), token=httpx.Response(401, json={}))
    outcome = _run(provider, _intent())
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "authentication_rejected"
    # Exactly one call, to the identity provider. Nothing was charged.
    assert [r.url.host for r in provider.seen] == [IDENTITY]


def test_missing_material_is_terminal_before_any_network_call() -> None:
    handler = FlutterwaveDeliveryHandler(
        httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    outcome = handler(
        DispatchRequest(
            capability_id=INTENT_CAPABILITY_ID,
            event_type="payments.command",
            payload=_intent(),
            config={"environment": "sandbox"},
            secrets={},
            idempotency_key=IDEMPOTENCY_KEY,
        )
    )
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "required_material_unavailable"


def test_an_unbound_capability_is_terminal_and_sends_nothing() -> None:
    provider = _Provider(_ok())
    outcome = _run(provider, _intent(), capability_id=CAPABILITY_ID)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "capability_unsupported"
    assert provider.seen == []


def test_an_undeclared_environment_is_terminal_and_sends_nothing() -> None:
    provider = _Provider(_ok())
    outcome = _run(provider, _intent(), config={})
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "environment_required"
    assert provider.seen == []


def test_a_refusal_never_carries_payload_material_into_a_persisted_field() -> None:
    """``Outcome.error_detail`` is written to `delivery_attempts`. A refusal
    that interpolated the command would store a payer contact and an amount in
    a column support exports."""
    outcome = _run(
        _Provider(_ok()),
        _intent(amount=1250.50, payer_contact="payer@example.test"),
    )
    rendered = f"{outcome.error_code}|{outcome.error_detail}"
    assert "payer@example.test" not in rendered
    assert "1250.5" not in rendered


# ── validation performs no provider effect ───────────────────────────────────


def test_validating_an_outbound_binding_initializes_no_payment() -> None:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    plugin = FlutterwaveConnector(transport=httpx.MockTransport(record))
    assert (
        plugin.validate_connection(
            config={"environment": "sandbox", "timeout_seconds": 20},
            secrets={WEBHOOK_SIGNING_SECRET: "fixture-signing", **_secrets()},
        )
        == ()
    )
    assert calls == []


def test_an_outbound_binding_missing_its_oauth_material_is_refused() -> None:
    plugin = FlutterwaveConnector()
    diagnostics = plugin.validate_connection(
        config={"environment": "sandbox", "timeout_seconds": 20},
        secrets={WEBHOOK_SIGNING_SECRET: "fixture-signing"},
    )
    assert [d.code for d in diagnostics] == ["required_material_unavailable"]
    assert not diagnostics[0].ok


# ── the deliberate absences ──────────────────────────────────────────────────


def test_no_transfer_or_payout_command_ships_in_this_release() -> None:
    """Held back on purpose: no product consumer exists, and an outbound
    money-movement command whose first execution is also its first review is
    exactly the thing not to ship. A test rather than a comment, so adding one
    is a visible decision."""
    assert MANIFEST.capability_ids == {
        CAPABILITY_ID,
        INTENT_CAPABILITY_ID,
        REFUND_CAPABILITY_ID,
    }
    for forbidden in ("transfer", "payout", "beneficiar", "bulk"):
        assert forbidden not in json.dumps(sorted(MANIFEST.capability_ids))


def test_no_outbound_request_can_reach_an_api_v3_path() -> None:
    initialize = _Provider(_ok())
    _run(initialize, _intent())
    refund = _Provider(_ok(id="rfd_5", status="pending"))
    _run(refund, _refund(), capability_id=REFUND_CAPABILITY_ID)
    for request in (*initialize.seen, *refund.seen):
        assert "/v3" not in request.url.path
        assert request.url.host in {SANDBOX, IDENTITY}
