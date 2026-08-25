"""Remita RRR issuance: the hash contract, exact money, and the five outcomes.

The load-bearing fact in this file is the ORDER of the five strings that go into
the SHA-512. Remita's only feedback for getting it wrong is a rejected request,
so the ordering gets a golden vector, a permutation proof and a proof that it is
NOT the status-check order — three tests, because a single "the hash equals
sha512 of this f-string" test just restates the implementation.

Every credential here is an obviously fake fixture string. No real merchant
identity, API key or service type appears in this file.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import httpx
import pytest
from dotmac_connector_remita import MANIFEST, PLUGIN
from dotmac_connector_remita.outbound import (
    ISSUANCE_CAPABILITY_ID,
    ISSUANCE_PATH,
    CommandContractError,
    RemitaIssuanceHandler,
    authorization_header,
    classify_request_error,
    exact_amount,
    issuance_hash,
    parse_provider_body,
)
from dotmac_connector_remita.plugin import (
    API_KEY,
    CAPABILITY_ID,
    DEMO_HOST,
    LIVE_HOST,
    RemitaPlugin,
)
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import ConnectorMode, DispatchRequest

MERCHANT_ID = "FIXTURE-MERCHANT"
API_KEY_VALUE = "fixture-remita-api-key"
SERVICE_TYPE_ID = "FIXTURE-SERVICE"
ORDER_ID = "fixture-order-0001"
IDEMPOTENCY_KEY = "engine-idempotency-key-1"


def _config(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "merchant_id": MERCHANT_ID,
        "environment": "live",
        "settlement_currency": "NGN",
    }
    value.update(overrides)
    return value


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "order_id": ORDER_ID,
        "service_type_id": SERVICE_TYPE_ID,
        "amount": "20000",
        "currency": "NGN",
        "currency_minor_units": 2,
        "payer_name": "Fixture Payer",
        "payer_email": "payer@example.test",
    }
    value.update(overrides)
    return value


def _request(
    payload: dict[str, object],
    *,
    capability_id: str = ISSUANCE_CAPABILITY_ID,
    config: dict[str, object] | None = None,
) -> DispatchRequest:
    return DispatchRequest(
        capability_id=capability_id,
        event_type="payments.command",
        payload=payload,
        config=config if config is not None else _config(),
        secrets={API_KEY: API_KEY_VALUE},
        idempotency_key=IDEMPOTENCY_KEY,
    )


class _Provider:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _run(
    provider: _Provider,
    payload: dict[str, object],
    *,
    capability_id: str = ISSUANCE_CAPABILITY_ID,
    config: dict[str, object] | None = None,
) -> Outcome:
    handler = RemitaIssuanceHandler(DEMO_HOST, LIVE_HOST, httpx.MockTransport(provider))
    return handler(_request(payload, capability_id=capability_id, config=config))


def _jsonp(**body: object) -> httpx.Response:
    return httpx.Response(200, text=f"jsonp({json.dumps(body)})")


# ── the hash contract ────────────────────────────────────────────────────────


def test_the_issuance_hash_is_sha512_over_five_fields_in_the_documented_order() -> None:
    """GOLDEN VECTOR, built from the fixture credentials above.

    ``dotmac_erp:app/services/remita/client.py:226-232`` concatenates
    ``merchant_id + service_type_id + order_id + amount + api_key`` with no
    separator and takes a hex SHA-512 of the UTF-8 bytes.
    """
    expected = hashlib.sha512(
        b"FIXTURE-MERCHANTFIXTURE-SERVICEfixture-order-000120000.00"
        b"fixture-remita-api-key"
    ).hexdigest()
    assert (
        issuance_hash(
            merchant_id=MERCHANT_ID,
            service_type_id=SERVICE_TYPE_ID,
            order_id=ORDER_ID,
            amount="20000.00",
            api_key=API_KEY_VALUE,
        )
        == expected
    )
    assert len(expected) == 128


def test_every_reordering_of_the_five_fields_produces_a_different_hash() -> None:
    """SENSITIVITY PROOF for the ordering.

    The golden vector above passes for a hash function that ignored order
    entirely — a sorted join, say. This drives the same five values through
    every adjacent swap and requires each one to differ, which is what makes the
    ORDER the thing under test rather than the digest algorithm.
    """
    fields = {
        "merchant_id": MERCHANT_ID,
        "service_type_id": SERVICE_TYPE_ID,
        "order_id": ORDER_ID,
        "amount": "20000.00",
        "api_key": API_KEY_VALUE,
    }
    correct = issuance_hash(**fields)
    names = list(fields)
    for index in range(len(names) - 1):
        swapped = dict(fields)
        left, right = names[index], names[index + 1]
        swapped[left], swapped[right] = fields[right], fields[left]
        assert issuance_hash(**swapped) != correct, (left, right)


def test_issuance_and_status_use_different_orders_and_are_not_interchangeable() -> None:
    """The trap this connector is built to avoid.

    ``client.py`` hashes ``rrr + api_key + merchant_id`` for a status read and
    ``merchant_id + service_type_id + order_id + amount + api_key`` for
    issuance. One shared "remita hash" helper is exactly how those two get
    merged into a single wrong one, so the two orders are kept in separate
    functions and this test pins the difference.
    """
    status_shaped = hashlib.sha512(
        f"{ORDER_ID}{API_KEY_VALUE}{MERCHANT_ID}".encode()
    ).hexdigest()
    assert (
        issuance_hash(
            merchant_id=MERCHANT_ID,
            service_type_id=SERVICE_TYPE_ID,
            order_id=ORDER_ID,
            amount="20000.00",
            api_key=API_KEY_VALUE,
        )
        != status_shaped
    )


def test_the_authorization_header_is_the_provider_comma_form() -> None:
    assert authorization_header(MERCHANT_ID, "abc123") == (
        "remitaConsumerKey=FIXTURE-MERCHANT,remitaConsumerToken=abc123"
    )
    # No scheme prefix and no space after the comma; both would be rejected.
    assert not authorization_header(MERCHANT_ID, "abc").startswith("Bearer")
    assert ", " not in authorization_header(MERCHANT_ID, "abc")


def test_the_hashed_amount_is_byte_identical_to_the_amount_on_the_wire() -> None:
    """The subtle parity bug this test exists to prevent.

    ``client.py:222`` formats the amount ONCE and feeds that one string to both
    the payload and the hash. A connector that hashed ``str(Decimal("20000"))``
    and sent ``"20000.00"`` would produce a correct-looking payload with a hash
    Remita rejects — so the assertion recomputes the hash from the string that
    actually went out.
    """
    provider = _Provider(_jsonp(statuscode="025", RRR="310007769676"))
    _run(provider, _payload(amount=Decimal("20000")))

    request = provider.seen[0]
    sent = json.loads(request.content)
    assert sent["amount"] == "20000.00"
    recomputed = issuance_hash(
        merchant_id=MERCHANT_ID,
        service_type_id=SERVICE_TYPE_ID,
        order_id=ORDER_ID,
        amount=sent["amount"],
        api_key=API_KEY_VALUE,
    )
    assert request.headers["authorization"] == authorization_header(
        MERCHANT_ID, recomputed
    )


# ── the wire ─────────────────────────────────────────────────────────────────


def test_issuance_posts_the_seven_field_paymentinit_body() -> None:
    provider = _Provider(_jsonp(statuscode="025", RRR="310007769676"))
    outcome = _run(provider, _payload(payer_phone="080000000", description="Invoice"))

    request = provider.seen[0]
    assert request.method == "POST"
    assert request.url.host == LIVE_HOST
    assert request.url.path == ISSUANCE_PATH
    assert json.loads(request.content) == {
        "serviceTypeId": SERVICE_TYPE_ID,
        "amount": "20000.00",
        "orderId": ORDER_ID,
        "payerName": "Fixture Payer",
        "payerEmail": "payer@example.test",
        "payerPhone": "080000000",
        "description": "Invoice",
    }
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "310007769676"


def test_absent_optional_fields_degrade_to_empty_strings_never_omission() -> None:
    """``client.py:240``. Remita's contract has no notion of an absent field."""
    provider = _Provider(_jsonp(statuscode="025", RRR="310007769676"))
    _run(provider, _payload())
    body = json.loads(provider.seen[0].content)
    assert body["payerPhone"] == ""
    assert body["description"] == ""
    assert set(body) == {
        "serviceTypeId",
        "amount",
        "orderId",
        "payerName",
        "payerEmail",
        "payerPhone",
        "description",
    }


def test_the_demo_environment_reaches_the_demo_host() -> None:
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    _run(provider, _payload(), config=_config(environment="demo"))
    assert provider.seen[0].url.host == DEMO_HOST


# ── money ────────────────────────────────────────────────────────────────────


def test_an_exact_amount_becomes_the_provider_two_decimal_string() -> None:
    assert exact_amount(Decimal("20000"), 2) == "20000.00"
    assert exact_amount("1250.50", 2) == "1250.50"
    assert exact_amount(20000, 2) == "20000.00"
    # A zero-exponent currency writes no fraction; the exponent comes from the
    # product, so no currency table lives in this transport.
    assert exact_amount("5000", 0) == "5000"


def test_a_binary_floating_point_amount_is_refused_by_type() -> None:
    with pytest.raises(CommandContractError) as refusal:
        exact_amount(20000.50, 2)
    assert refusal.value.code == "amount_not_exact"


def test_a_float_amount_is_terminal_and_no_reference_is_requested() -> None:
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    outcome = _run(provider, _payload(amount=20000.50))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "amount_not_exact"
    assert provider.seen == []


def test_an_amount_finer_than_the_currency_is_refused_not_rounded() -> None:
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    outcome = _run(provider, _payload(amount="20000.505"))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "amount_precision_invalid"
    assert provider.seen == []


def test_a_currency_the_installation_does_not_settle_in_is_refused() -> None:
    """Remita's payload carries NO currency field, so an unchecked command in
    another currency would mint a reference denominated in naira regardless."""
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    outcome = _run(provider, _payload(currency="USD"))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "currency_unsupported"
    assert provider.seen == []
    # SPECIFICITY: an installation that DOES settle in USD accepts it, so the
    # refusal above is about the mismatch and not about a hardcoded naira.
    accepted = _run(
        _Provider(_jsonp(statuscode="025", RRR="1")),
        _payload(currency="USD"),
        config=_config(settlement_currency="USD"),
    )
    assert accepted.status is OutcomeStatus.SUCCEEDED


def test_a_command_with_no_currency_is_refused_rather_than_assumed_naira() -> None:
    payload = _payload()
    del payload["currency"]
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    outcome = _run(provider, payload)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "currency_required"
    assert provider.seen == []


# ── provider codes ───────────────────────────────────────────────────────────


def test_reference_generated_is_the_only_success_code() -> None:
    outcome = _run(
        _Provider(
            _jsonp(
                statuscode="025",
                RRR="310007769676",
                status="Payment Reference generated",
            )
        ),
        _payload(),
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == "310007769676"
    assert outcome.provider_status_code == 200


def test_a_duplicate_order_is_ambiguous_because_a_reference_already_exists() -> None:
    """``021``. Neither retryable — it can never succeed — nor terminal, because
    a reference for this order is live at the provider and the product has to be
    told about it."""
    outcome = _run(_Provider(_jsonp(statuscode="021", status="Duplicate")), _payload())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_duplicate_order"
    assert outcome.status is not OutcomeStatus.RETRYABLE
    assert outcome.status is not OutcomeStatus.TERMINAL


def test_a_declined_provider_code_is_terminal_and_never_retryable() -> None:
    """``027`` invalid service type, and every other refusal. Repeating the
    identical request cannot change the answer."""
    for code in ("027", "096", "not-a-known-code"):
        outcome = _run(_Provider(_jsonp(statuscode=code)), _payload())
        assert outcome.status is OutcomeStatus.TERMINAL, code
        assert outcome.is_final, code
        assert outcome.error_code == "provider_declined", code


def test_success_without_a_reference_is_ambiguous_rather_than_a_crash() -> None:
    """``client.py:275`` reads ``data["RRR"]`` unguarded and raises `KeyError`
    on the far side of a provider effect. A reference may have been minted."""
    outcome = _run(_Provider(_jsonp(statuscode="025")), _payload())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_receipt_missing"


def test_a_response_with_no_status_code_at_all_is_ambiguous() -> None:
    outcome = _run(_Provider(_jsonp(RRR="310007769676")), _payload())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_status_missing"


def test_both_the_jsonp_wrapper_and_bare_json_are_accepted() -> None:
    wrapped = parse_provider_body('jsonp ({"statuscode":"025","RRR":"1"})')
    bare = parse_provider_body('{"statuscode":"025","RRR":"1"}')
    assert wrapped == bare == {"statuscode": "025", "RRR": "1"}
    assert parse_provider_body("<html/>") is None
    assert parse_provider_body('["not","an","object"]') is None


def test_an_unreadable_body_is_ambiguous_not_a_failure() -> None:
    outcome = _run(_Provider(httpx.Response(200, text="<html/>")), _payload())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_response_unreadable"


# ── transport ────────────────────────────────────────────────────────────────


def test_a_timeout_after_the_request_was_sent_is_ambiguous_not_retryable() -> None:
    """Remita may have minted a reference the product has never seen. Retrying
    issues a second one; dead-lettering hides the first."""
    outcome = _run(_Provider(httpx.ReadTimeout("read timed out")), _payload())
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.error_code == "provider_outcome_ambiguous"
    assert outcome.status is not OutcomeStatus.RETRYABLE


def test_a_connect_failure_before_any_byte_left_is_retryable() -> None:
    """SPECIFICITY for the test above: both are transport failures and only one
    of them happened with bytes on the wire."""
    for never_sent in (
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError("refused"),
    ):
        outcome = _run(_Provider(never_sent), _payload())
        assert outcome.status is OutcomeStatus.RETRYABLE
        assert outcome.error_code == "provider_connect_failed"


def test_every_httpx_transport_failure_lands_on_one_of_the_two_answers() -> None:
    for exc in (
        httpx.ReadTimeout("x"),
        httpx.WriteTimeout("x"),
        httpx.PoolTimeout("x"),
        httpx.RemoteProtocolError("x"),
        httpx.ConnectTimeout("x"),
        httpx.ConnectError("x"),
    ):
        assert classify_request_error(exc).status in {
            OutcomeStatus.RETRYABLE,
            OutcomeStatus.RECONCILIATION_REQUIRED,
        }, exc


def test_http_faults_split_into_retryable_and_terminal() -> None:
    assert _run(_Provider(httpx.Response(503, text="")), _payload()).status is (
        OutcomeStatus.RETRYABLE
    )
    limited = _run(
        _Provider(httpx.Response(429, headers={"retry-after": "7"}, text="")),
        _payload(),
    )
    assert limited.status is OutcomeStatus.RETRYABLE
    assert limited.retry_after_seconds == 7
    for status, code in (
        (401, "authentication_rejected"),
        (400, "provider_rejected_request"),
    ):
        outcome = _run(_Provider(httpx.Response(status, text="")), _payload())
        assert outcome.status is OutcomeStatus.TERMINAL, status
        assert outcome.error_code == code, status


def test_a_refusal_never_carries_material_or_payload_into_a_persisted_field() -> None:
    outcome = _run(
        _Provider(httpx.Response(401, text=f"{API_KEY_VALUE} provider-private")),
        _payload(),
    )
    rendered = f"{outcome.error_code}|{outcome.error_detail}"
    assert API_KEY_VALUE not in rendered
    assert "provider-private" not in rendered
    assert "payer@example.test" not in rendered


# ── the manifest and the deliberate absences ─────────────────────────────────


def test_the_manifest_maps_each_capability_to_exactly_one_executable_mode() -> None:
    modes = {c.capability_id: c.modes for c in MANIFEST.capabilities}
    assert modes[CAPABILITY_ID] == frozenset({ConnectorMode.POLL})
    assert modes[ISSUANCE_CAPABILITY_ID] == frozenset({ConnectorMode.DELIVERY})
    assert str(MANIFEST.spi_range) == ">=1.4,<2.0"
    assert PLUGIN.modes == frozenset({ConnectorMode.POLL, ConnectorMode.DELIVERY})


def test_issuance_reaches_no_host_the_connector_did_not_already_declare() -> None:
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == (DEMO_HOST, LIVE_HOST)


def test_the_published_poll_only_contract_stays_resolvable() -> None:
    historical = {m.capability_ids for m in PLUGIN.historical_manifests}
    assert {CAPABILITY_ID} in historical


def test_a_delivery_factory_refuses_the_poll_capability_and_the_reverse() -> None:
    """SENSITIVITY. A factory answering for any declared capability would hand
    a poll handler to the dispatch worker and still pass discovery."""
    with pytest.raises(ValueError):
        PLUGIN.handler_for(CAPABILITY_ID)
    with pytest.raises(ValueError):
        PLUGIN.poll_handler_for(ISSUANCE_CAPABILITY_ID)
    assert isinstance(PLUGIN.handler_for(ISSUANCE_CAPABILITY_ID), RemitaIssuanceHandler)


def test_an_unbound_capability_is_terminal_and_sends_nothing() -> None:
    provider = _Provider(_jsonp(statuscode="025", RRR="1"))
    outcome = _run(provider, _payload(), capability_id=CAPABILITY_ID)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "capability_unsupported"
    assert provider.seen == []


def test_validating_an_issuance_binding_mints_no_reference() -> None:
    """The only issuance call there is CREATES a payment obligation, so a live
    probe here would bill someone to answer whether the credentials look
    usable. The status leg may probe; this one may not."""
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _jsonp(statuscode="025", RRR="1")

    plugin = RemitaPlugin(transport=httpx.MockTransport(record))
    assert plugin.validate_connection(config=_config(), secrets={API_KEY: "k"}) == ()
    assert calls == []


def test_an_issuance_binding_with_no_settlement_currency_is_refused() -> None:
    plugin = RemitaPlugin()
    config = _config()
    del config["settlement_currency"]
    diagnostics = plugin.validate_connection(config=config, secrets={API_KEY: "k"})
    assert [d.code for d in diagnostics] == ["configuration_invalid"]


def test_no_status_push_path_ships_with_issuance() -> None:
    """Remita has no webhook at all. Payment status stays a POLL concern, and
    adding an ingress mode here would be inventing a channel the provider does
    not have."""
    assert ConnectorMode.INGRESS not in PLUGIN.modes
    assert MANIFEST.capability_ids == {CAPABILITY_ID, ISSUANCE_CAPABILITY_ID}
