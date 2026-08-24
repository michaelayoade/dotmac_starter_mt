"""Outbound Paystack commands: what may be retried, and what must never be.

This suite is mostly NEGATIVE on purpose. A payment connector that only proves
the happy path proves the half that costs nothing when it is wrong. The three
cases worth the file are:

* a DECLINE is terminal, so the engine never re-presents a burnt reference;
* a read timeout after a send is AMBIGUOUS, so nothing captures a card twice;
* one idempotency key produces one provider reference, on every attempt.

Two of the checks below are SENSITIVITY PROOFS rather than assertions about
behaviour — `test_the_ambiguity_rule_discriminates...` drives the identical
transport failure against a pure read and requires RETRYABLE, because a
classifier that answered AMBIGUOUS to everything would pass every other test
here; and the two totality guards are driven with a deliberately broken table.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from dotmac_connector_paystack import MANIFEST, PLUGIN, __version__
from dotmac_connector_paystack.delivery import (
    ACTIONS_BY_CAPABILITY,
    OUTBOUND_CAPABILITY_IDS,
    PaystackDeliveryHandler,
    _misallocated_operations,
)
from dotmac_connector_paystack.operations import (
    API_HOST,
    ENGINE_STATUS,
    OPERATIONS,
    MoneyContractError,
    OperationContractError,
    OperationOutcome,
    PaystackOperations,
    _unmapped_outcomes,
    exact_amount,
    minor_units,
    provider_reference,
)
from dotmac_connector_paystack.plugin import CAPABILITY_ID
from dotmac_integration.conformance import assert_plugin_conforms
from dotmac_integration.retry import OutcomeStatus
from dotmac_integration.spi import ConnectorMode, DispatchRequest

SECRET = "paystack-test-server-material"
KEY = "delivery-7f3c-attempt-independent"

INTENT = "payments.intent.v1"
REFUND = "payments.refund.v1"
PAYOUT = "payments.payout.v1"
CUSTOMER = "payments.customer.v1"


def _ops(handler: Callable[[httpx.Request], httpx.Response]) -> PaystackOperations:
    return PaystackOperations(transport=httpx.MockTransport(handler), timeout_seconds=5)


def _envelope(data: dict[str, object]) -> dict[str, object]:
    return {"status": True, "message": "ok", "data": data}


def _responder(
    body: dict[str, object], status: int = 200, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    return respond


def _raiser(exc: type[httpx.RequestError], message: str = "private detail"):
    def respond(request: httpx.Request) -> httpx.Response:
        raise exc(message, request=request)

    return respond


def _recording(
    body: dict[str, object], status: int = 200
) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return seen, respond


def _charge(ops: PaystackOperations, *, key: str = KEY, **overrides: object):
    params: dict[str, object] = {
        "email": "payer@example.test",
        "amount": "5000.00",
        "currency": "NGN",
        "authorization_code": "AUTH_reusable1",
    }
    params.update(overrides)
    return ops.run(
        "charge_authorization", params, idempotency_key=key, api_secret=SECRET
    )


def _transfer(ops: PaystackOperations, *, key: str = KEY, **overrides: object):
    params: dict[str, object] = {
        "amount": "12000.00",
        "currency": "NGN",
        "recipient": "RCP_payee1",
        "reason": "supplier settlement",
    }
    params.update(overrides)
    return ops.run("initiate_transfer", params, idempotency_key=key, api_secret=SECRET)


def _resolve(ops: PaystackOperations):
    return ops.run(
        "resolve_bank_account",
        {"account_number": "0123456789", "bank_code": "058"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )


# ── the contract the manifest publishes ──────────────────────────────────────


def test_outbound_commands_are_delivery_capabilities_separate_from_observation() -> (
    None
):
    assert MANIFEST.version == __version__ == "0.1.0a2"
    assert MANIFEST.spi_range.minimum.minor == 4
    assert PLUGIN.modes == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )
    declared = {c.capability_id: c.modes for c in MANIFEST.capabilities}
    assert declared[CAPABILITY_ID] == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL}
    )
    for capability_id in OUTBOUND_CAPABILITY_IDS:
        # A settlement watcher must not acquire the authority to move money
        # just because the same distribution can do both.
        assert declared[capability_id] == frozenset({ConnectorMode.DELIVERY})
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == (API_HOST,)


def test_the_plugin_still_conforms_to_the_spi_with_three_modes_declared() -> None:
    assert_plugin_conforms(PLUGIN)


def test_the_nine_operations_are_exactly_the_named_provider_neutral_commands() -> None:
    assert set(OPERATIONS) == {
        "initialize_payment",
        "charge_authorization",
        "refund",
        "resolve_bank_account",
        "create_transfer_recipient",
        "initiate_transfer",
        "create_customer",
        "update_customer",
        "read_customer",
    }


# ── a decline is not a retryable error ───────────────────────────────────────


@pytest.mark.parametrize("token", ["failed", "reversed", "abandoned"])
def test_a_declined_charge_is_terminal_and_is_never_retried(token: str) -> None:
    result = _charge(
        _ops(
            _responder(
                _envelope(
                    {
                        "id": 4099260516,
                        "status": token,
                        "reference": "ignored",
                        "amount": 500000,
                        "currency": "NGN",
                        "gateway_response": "Declined",
                    }
                )
            )
        )
    )
    assert result.outcome is OperationOutcome.DECLINED
    assert result.error_code == "provider_declined"
    outcome = result.as_outcome()
    # THE assertion of this file's first third: a decline must not re-present a
    # reference Paystack has already burnt.
    assert outcome.status is not OutcomeStatus.RETRYABLE
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.is_final


def test_a_declined_transfer_is_terminal_too() -> None:
    result = _transfer(
        _ops(
            _responder(
                _envelope(
                    {
                        "id": 91,
                        "status": "failed",
                        "transfer_code": "TRF_1",
                        "amount": 1200000,
                    }
                )
            )
        )
    )
    assert result.outcome is OperationOutcome.DECLINED
    assert result.as_outcome().status is OutcomeStatus.TERMINAL


def test_the_decline_mapping_cannot_be_softened_into_a_retry() -> None:
    """A table, checked directly — not inferred from one response fixture."""
    assert ENGINE_STATUS[OperationOutcome.DECLINED] is OutcomeStatus.TERMINAL
    assert ENGINE_STATUS[OperationOutcome.AMBIGUOUS] is (
        OutcomeStatus.RECONCILIATION_REQUIRED
    )
    assert OutcomeStatus.RETRYABLE not in {
        ENGINE_STATUS[OperationOutcome.DECLINED],
        ENGINE_STATUS[OperationOutcome.AMBIGUOUS],
        ENGINE_STATUS[OperationOutcome.TERMINAL],
    }


# ── a timeout after the send is ambiguous, not failed and not retryable ──────


@pytest.mark.parametrize(
    "operation", ["charge_authorization", "initiate_transfer", "refund"]
)
def test_a_read_timeout_after_a_money_command_is_ambiguous(operation: str) -> None:
    ops = _ops(_raiser(httpx.ReadTimeout))
    params: dict[str, dict[str, object]] = {
        "charge_authorization": {
            "email": "payer@example.test",
            "amount": "5000.00",
            "currency": "NGN",
            "authorization_code": "AUTH_reusable1",
        },
        "initiate_transfer": {
            "amount": "12000.00",
            "currency": "NGN",
            "recipient": "RCP_payee1",
        },
        "refund": {"transaction": "dmi-target-transaction"},
    }
    result = ops.run(
        operation, params[operation], idempotency_key=KEY, api_secret=SECRET
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_outcome_ambiguous"
    outcome = result.as_outcome()
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.status is not OutcomeStatus.RETRYABLE
    # The reconciler needs a handle, and it needs it most at the moment the
    # answer went missing.
    assert outcome.provider_reference == provider_reference(KEY)


@pytest.mark.parametrize(
    "failure", [httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError]
)
def test_every_post_send_failure_is_ambiguous_not_only_a_read_timeout(
    failure: type[httpx.RequestError],
) -> None:
    result = _charge(_ops(_raiser(failure)))
    assert result.as_outcome().status is OutcomeStatus.RECONCILIATION_REQUIRED


def test_a_connect_failure_never_reached_the_provider_so_it_is_retryable() -> None:
    for failure in (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
        result = _charge(_ops(_raiser(failure)))
        assert result.outcome is OperationOutcome.RETRYABLE, failure
        assert result.error_code == "provider_connect_failed"


def test_the_ambiguity_rule_discriminates_and_does_not_answer_ambiguous_to_all() -> (
    None
):
    """Sensitivity proof for the classification above.

    The identical transport failure against a PURE READ must come back
    RETRYABLE. Without this, a classifier hard-wired to `AMBIGUOUS` would
    satisfy every timeout assertion in this file and be wrong about six of the
    nine operations.
    """
    result = _resolve(_ops(_raiser(httpx.ReadTimeout)))
    assert result.outcome is OperationOutcome.RETRYABLE
    assert result.as_outcome().status is OutcomeStatus.RETRYABLE


def test_a_server_error_is_ambiguous_for_money_and_retryable_for_a_read() -> None:
    assert _charge(_ops(_responder({}, 502))).outcome is OperationOutcome.AMBIGUOUS
    assert _resolve(_ops(_responder({}, 502))).outcome is OperationOutcome.RETRYABLE


def test_an_in_flight_provider_status_is_reconciled_never_re_sent() -> None:
    for token in ("pending", "queued", "processing", "otp", "received"):
        result = _transfer(
            _ops(
                _responder(
                    _envelope({"id": 5, "status": token, "transfer_code": "TRF_9"})
                )
            )
        )
        assert result.outcome is OperationOutcome.AMBIGUOUS, token
        assert result.error_code == "provider_status_not_conclusive"
        assert result.provider_status == token


def test_an_unrecognised_provider_status_is_never_assumed_successful() -> None:
    result = _charge(
        _ops(_responder(_envelope({"id": 7, "status": "successful", "amount": 500000})))
    )
    # `successful` is a word a different provider uses. Recognising it here
    # would turn "we did not understand the answer" into "it worked".
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_status_unknown"


def test_a_two_hundred_that_says_it_did_not_happen_is_terminal() -> None:
    result = _charge(
        _ops(_responder({"status": False, "message": "Invalid authorization code"}))
    )
    # The provider answered and said it did not act. There is nothing to
    # reconcile and nothing a retry would fix.
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "provider_refused_request"


def _unreadable(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>maintenance</html>")


def test_an_unreadable_two_hundred_is_never_read_as_a_refusal() -> None:
    """The inversion that matters: "we could not tell" is not "it did not
    happen". An unreadable body has no `status` field, and treating its absence
    as a refusal would report a possibly-captured charge as terminal."""
    charged = _charge(_ops(_unreadable))
    assert charged.outcome is OperationOutcome.AMBIGUOUS
    assert charged.error_code == "provider_response_unreadable"
    # …and the same body against a pure read stays retryable, which is what
    # shows the rule discriminates rather than defaulting.
    assert _resolve(_ops(_unreadable)).outcome is OperationOutcome.RETRYABLE


# ── one idempotency key, one provider reference ──────────────────────────────


def test_the_same_idempotency_key_never_produces_two_different_charges() -> None:
    seen, respond = _recording(
        _envelope(
            {
                "id": 1,
                "status": "success",
                "amount": 500000,
                "currency": "NGN",
                "reference": "echoed",
            }
        )
    )
    ops = _ops(respond)
    first = _charge(ops)
    second = _charge(ops)

    references = [json.loads(request.content)["reference"] for request in seen]
    assert len(references) == 2
    # Attempt two presents the reference attempt one used, so Paystack's own
    # duplicate-reference refusal — not this code's good intentions — is what
    # makes the second capture impossible.
    assert references[0] == references[1] == provider_reference(KEY)
    assert first.provider_reference == second.provider_reference


def test_a_different_delivery_gets_a_different_reference() -> None:
    assert provider_reference(KEY) != provider_reference(KEY + "-2")


def test_the_reference_satisfies_both_paystack_charsets_at_once() -> None:
    reference = provider_reference("An Awkward Key: with/chars+and.dots=")
    # Transactions allow `- . =` and alphanumerics; transfers allow `- _` and
    # alphanumerics and want lower case. Only their intersection is safe.
    assert reference.isalnum()
    assert reference == reference.lower()
    assert 1 < len(reference) <= 100


def test_a_reused_reference_is_ambiguous_evidence_not_a_fresh_failure() -> None:
    for message, status in (
        ("Duplicate Transaction Reference", 400),
        (
            "Please provide a unique reference. Reference already exists on a transfer",
            404,
        ),
    ):
        result = _charge(
            _ops(_responder({"status": False, "message": message}, status))
        )
        # The provider is telling us the earlier attempt landed. Sending again
        # is the one response that must never happen.
        assert result.outcome is OperationOutcome.AMBIGUOUS, message
        assert result.error_code == "provider_reference_already_used"
        assert result.provider_reference == provider_reference(KEY)


def test_a_money_command_with_no_idempotency_key_is_refused_not_improvised() -> None:
    seen, respond = _recording(_envelope({"id": 1}))
    result = _charge(_ops(respond), key="")
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "idempotency_key_required"
    # Nothing was sent. A generated key would be unique per attempt, which is
    # the duplicate-charge machine this refusal exists to prevent.
    assert seen == []


def test_a_refund_carries_the_derived_key_where_a_reconciler_can_read_it() -> None:
    seen, respond = _recording(
        _envelope(
            {"id": 4242, "status": "pending", "amount": 150000, "currency": "NGN"}
        )
    )
    result = _ops(respond).run(
        "refund",
        {"transaction": "TRX_target", "amount": "1500.00", "currency": "NGN"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    body = json.loads(seen[0].content)
    # Paystack accepts no reference on a refund, so the durable handle goes
    # where `GET /refund?transaction=...` will show it — Sub's mechanism.
    assert body["merchant_note"] == provider_reference(KEY)
    assert body["transaction"] == "TRX_target"
    assert body["amount"] == 150000
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.provider_status == "pending"


def test_a_refund_the_provider_echoes_at_another_amount_is_not_a_success() -> None:
    result = _ops(
        _responder(
            _envelope(
                {"id": 4242, "status": "pending", "amount": 149999, "currency": "NGN"}
            )
        )
    ).run(
        "refund",
        {"transaction": "TRX_target", "amount": "1500.00", "currency": "NGN"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_evidence_incomplete"


def test_a_refund_with_no_requested_amount_takes_the_transactions_own() -> None:
    seen, respond = _recording(_envelope({"id": 4242, "status": "processed"}))
    result = _ops(respond).run(
        "refund",
        {"transaction": "TRX_target"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    body = json.loads(seen[0].content)
    assert "amount" not in body
    # Nothing was requested, so there is nothing to compare an echo against.
    assert result.outcome is OperationOutcome.SUCCEEDED


# ── money is exact and never a float ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("major", "minor"),
    [
        ("0.10", 10),
        ("0.07", 7),
        ("1.00", 100),
        ("5000.00", 500000),
        ("1234.56", 123456),
        ("999999999999.99", 99999999999999),
    ],
)
def test_an_exact_amount_round_trips_through_the_wire_scale(
    major: str, minor: int
) -> None:
    assert minor_units(major, currency="NGN") == minor
    assert exact_amount(minor, allow_zero=False) == major
    # Both directions have ONE owner, so inbound and outbound cannot disagree
    # about what a kobo is.
    assert minor_units(exact_amount(minor, allow_zero=False), currency="NGN") == minor


@pytest.mark.parametrize(
    "amount",
    [
        50.0,  # a float, which cannot hold 0.10
        0.1 + 0.2,  # 0.30000000000000004
        5000,  # an int: naira or kobo? unreadable, so refused
        "5000",  # no, major units must be written as such
        "-1.00",
        "0.00",
        "abc",
        None,
        True,
    ],
)
def test_money_never_arrives_as_a_float_or_an_ambiguous_integer(amount: object) -> None:
    with pytest.raises(MoneyContractError):
        minor_units(amount, currency="NGN")


def test_a_float_derived_string_cannot_sneak_past_the_amount_contract() -> None:
    with pytest.raises(MoneyContractError) as raised:
        minor_units(str(0.1 + 0.2), currency="NGN")
    assert raised.value.code == "amount_not_exact"


def test_an_amount_finer_than_the_wire_scale_is_refused_never_rounded() -> None:
    with pytest.raises(MoneyContractError) as raised:
        minor_units("100.005", currency="NGN")
    assert raised.value.code == "amount_finer_than_wire_scale"


def test_the_wire_body_carries_integer_minor_units_and_an_explicit_currency() -> None:
    seen, respond = _recording(
        _envelope(
            {
                "id": 1,
                "status": "success",
                "amount": 500000,
                "currency": "NGN",
                "reference": "r",
            }
        )
    )
    _charge(_ops(respond))
    body = json.loads(seen[0].content)
    assert body["amount"] == 500000
    assert isinstance(body["amount"], int)
    assert body["currency"] == "NGN"


def test_a_success_whose_money_does_not_match_what_we_sent_is_not_a_success() -> None:
    result = _charge(
        _ops(
            _responder(
                _envelope(
                    {"id": 1, "status": "success", "amount": 499999, "currency": "NGN"}
                )
            )
        )
    )
    # No tolerance here on purpose: a tolerance is a policy, and policy is the
    # product's. What the connector may say is that this is not conclusive.
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_evidence_incomplete"


def test_a_success_with_no_provider_identifier_is_not_conclusive_either() -> None:
    result = _charge(
        _ops(
            _responder(
                _envelope({"status": "success", "amount": 500000, "currency": "NGN"})
            )
        )
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_evidence_incomplete"


# ── the remaining commands ───────────────────────────────────────────────────


def test_initialize_payment_returns_the_checkout_handles_it_exists_to_obtain() -> None:
    seen, respond = _recording(
        _envelope(
            {
                "authorization_url": "https://checkout.example.test/abc",
                "access_code": "abc",
                "reference": "echoed",
            }
        )
    )
    result = _ops(respond).run(
        "initialize_payment",
        {"email": "payer@example.test", "amount": "2500.00", "currency": "NGN"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert seen[0].url.path == "/transaction/initialize"
    assert seen[0].url.host == API_HOST
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.reply["access_code"] == "abc"
    assert result.provider_reference == provider_reference(KEY)


def test_an_initialization_with_no_checkout_handle_is_ambiguous() -> None:
    result = _ops(_responder(_envelope({"reference": "echoed"}))).run(
        "initialize_payment",
        {"email": "payer@example.test", "amount": "2500.00", "currency": "NGN"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_receipt_missing"


def test_resolving_a_bank_account_is_a_query_with_no_body() -> None:
    seen, respond = _recording(
        _envelope({"account_number": "0123456789", "account_name": "A Payee"})
    )
    result = _resolve(_ops(respond))
    assert seen[0].url.path == "/bank/resolve"
    assert dict(seen[0].url.params) == {
        "account_number": "0123456789",
        "bank_code": "058",
    }
    assert not seen[0].content
    assert result.reply["account_name"] == "A Payee"
    assert result.outcome is OperationOutcome.SUCCEEDED


def test_creating_a_transfer_recipient_refuses_a_type_paystack_does_not_have() -> None:
    result = _ops(_responder(_envelope({}))).run(
        "create_transfer_recipient",
        {
            "type": "carrier_pigeon",
            "name": "A Payee",
            "account_number": "0123456789",
            "bank_code": "058",
            "currency": "NGN",
        },
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "recipient_type_invalid"


def test_a_transfer_never_lets_a_payload_choose_which_pot_it_leaves_from() -> None:
    seen, respond = _recording(
        _envelope(
            {
                "id": 3,
                "status": "success",
                "transfer_code": "TRF_9",
                "amount": 1200000,
                "currency": "NGN",
            }
        )
    )
    _transfer(_ops(respond), source="someone-elses-balance")
    body = json.loads(seen[0].content)
    assert body["source"] == "balance"
    assert body["reference"] == provider_reference(KEY)


def test_customer_create_is_ambiguous_on_a_timeout_but_update_is_retryable() -> None:
    ops = _ops(_raiser(httpx.ReadTimeout))
    created = ops.run(
        "create_customer",
        {"email": "payer@example.test"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    updated = ops.run(
        "update_customer",
        {"customer": "CUS_1", "first_name": "A"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    # Paystack does not document what a repeated create does with an email it
    # already holds, and an undocumented duplicate semantic is ambiguous.
    assert created.outcome is OperationOutcome.AMBIGUOUS
    # A PUT of the same fields to the same code is idempotent by construction.
    assert updated.outcome is OperationOutcome.RETRYABLE


def test_reading_an_absent_customer_is_a_definite_answer_not_a_fault() -> None:
    result = _ops(_responder({"status": False, "message": "not found"}, 404)).run(
        "read_customer",
        {"customer": "payer@example.test"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "provider_record_not_found"


def test_a_rate_limit_is_retryable_and_the_providers_own_delay_travels() -> None:
    result = _charge(_ops(_responder({}, 429, {"retry-after": "17"})))
    assert result.outcome is OperationOutcome.RETRYABLE
    assert result.as_outcome().retry_after_seconds == 17


def test_rejected_credentials_are_terminal_rather_than_retried_forever() -> None:
    result = _charge(_ops(_responder({"status": False}, 401)))
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "authentication_rejected"


def test_an_unsupported_operation_never_reaches_a_provider() -> None:
    seen, respond = _recording(_envelope({}))
    result = _ops(respond).run(
        "drain_the_balance", {}, idempotency_key=KEY, api_secret=SECRET
    )
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "operation_unsupported"
    assert seen == []


def test_a_missing_server_secret_stops_the_command_before_any_request() -> None:
    seen, respond = _recording(_envelope({}))
    result = _charge_with_secret(_ops(respond), api_secret="")
    assert result.error_code == "api_secret_key_unavailable"
    assert seen == []


def _charge_with_secret(ops: PaystackOperations, *, api_secret: str):
    return ops.run(
        "charge_authorization",
        {
            "email": "payer@example.test",
            "amount": "5000.00",
            "currency": "NGN",
            "authorization_code": "AUTH_reusable1",
        },
        idempotency_key=KEY,
        api_secret=api_secret,
    )


def test_the_bearer_credential_authenticates_every_outbound_call() -> None:
    seen, respond = _recording(
        _envelope({"account_number": "0123456789", "account_name": "A Payee"})
    )
    _resolve(_ops(respond))
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"


# ── nothing a connector holds may reach a persisted column ───────────────────


def test_no_provider_text_or_credential_reaches_the_persisted_outcome() -> None:
    body = {"status": False, "message": f"rejected for {SECRET} and payer@example.test"}
    outcome = _charge(_ops(_responder(body, 422))).as_outcome()
    # `error_detail` is persisted by the engine, and the only text available
    # here was produced by a call made with materialized credentials.
    assert outcome.error_detail is None
    rendered = repr(outcome)
    assert SECRET not in rendered
    assert "payer@example.test" not in rendered


# ── one binding is one blast radius ──────────────────────────────────────────


def _dispatch(
    capability_id: str, action: str, params: dict[str, object]
) -> DispatchRequest:
    return DispatchRequest(
        capability_id=capability_id,
        event_type="command",
        payload={"action": action, "params": params},
        config={"timeout_seconds": 5},
        secrets={"api_secret_key": SECRET},
        idempotency_key=KEY,
    )


def _handler(
    capability_id: str, respond: Callable[[httpx.Request], httpx.Response]
) -> PaystackDeliveryHandler:
    return PaystackDeliveryHandler(capability_id, httpx.MockTransport(respond))


def test_a_binding_may_not_be_talked_into_a_command_it_was_not_granted() -> None:
    seen, respond = _recording(_envelope({"id": 1}))
    outcome = _handler(INTENT, respond)(
        _dispatch(INTENT, "refund", {"transaction": "TRX_1"})
    )
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "operation_not_allowed"
    assert seen == []


def test_every_operation_is_reachable_through_exactly_one_capability() -> None:
    assert set(ACTIONS_BY_CAPABILITY) == set(OUTBOUND_CAPABILITY_IDS)
    assert _misallocated_operations(OPERATIONS, ACTIONS_BY_CAPABILITY) == frozenset()


def test_the_capability_allocation_guard_still_bites() -> None:
    """Sensitivity proof: the guard must fail on both ways of being wrong."""
    unreachable = {INTENT: frozenset({"initialize_payment"})}
    assert "refund" in _misallocated_operations(OPERATIONS, unreachable)
    overlapping = {
        INTENT: frozenset({"refund"}),
        REFUND: frozenset({"refund"}),
    }
    assert "refund" in _misallocated_operations(OPERATIONS, overlapping)


def test_the_outcome_mapping_is_total() -> None:
    assert _unmapped_outcomes(OperationOutcome, ENGINE_STATUS) == frozenset()


def test_the_outcome_totality_guard_still_bites() -> None:
    """Sensitivity proof: a check over a complete table passes for free."""
    grown = frozenset(OperationOutcome) | {"partially_captured"}
    assert _unmapped_outcomes(grown, ENGINE_STATUS) == frozenset({"partially_captured"})


# ── the delivery adapter ─────────────────────────────────────────────────────


def test_the_delivery_handler_hands_the_engine_its_own_vocabulary() -> None:
    seen, respond = _recording(
        _envelope(
            {
                "id": 1,
                "status": "success",
                "amount": 500000,
                "currency": "NGN",
                "reference": "echoed",
            }
        )
    )
    outcome = _handler(INTENT, respond)(
        _dispatch(
            INTENT,
            "charge_authorization",
            {
                "email": "payer@example.test",
                "amount": "5000.00",
                "currency": "NGN",
                "authorization_code": "AUTH_reusable1",
            },
        )
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == provider_reference(KEY)
    assert outcome.provider_status_code == 200
    assert json.loads(seen[0].content)["reference"] == provider_reference(KEY)


def test_a_dispatched_timeout_reaches_the_engine_as_reconciliation_required() -> None:
    outcome = _handler(PAYOUT, _raiser(httpx.ReadTimeout))(
        _dispatch(
            PAYOUT,
            "initiate_transfer",
            {"amount": "12000.00", "currency": "NGN", "recipient": "RCP_1"},
        )
    )
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.status is not OutcomeStatus.RETRYABLE


def test_an_outbound_binding_without_a_configured_timeout_is_refused() -> None:
    seen, respond = _recording(_envelope({}))
    request = _dispatch(CUSTOMER, "read_customer", {"customer": "CUS_1"})
    stripped = DispatchRequest(
        capability_id=request.capability_id,
        event_type=request.event_type,
        payload=request.payload,
        config={},
        secrets=request.secrets,
        idempotency_key=request.idempotency_key,
    )
    outcome = _handler(CUSTOMER, respond)(stripped)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "timeout_seconds_invalid"
    assert seen == []


def test_an_outbound_binding_without_the_server_secret_is_refused() -> None:
    seen, respond = _recording(_envelope({}))
    request = _dispatch(CUSTOMER, "read_customer", {"customer": "CUS_1"})
    stripped = DispatchRequest(
        capability_id=request.capability_id,
        event_type=request.event_type,
        payload=request.payload,
        config=request.config,
        secrets={},
        idempotency_key=request.idempotency_key,
    )
    outcome = _handler(CUSTOMER, respond)(stripped)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "api_secret_key_unavailable"
    assert seen == []


def test_the_observation_capability_cannot_be_dispatched_as_a_command() -> None:
    seen, respond = _recording(_envelope({}))
    outcome = _handler(CAPABILITY_ID, respond)(
        _dispatch(CAPABILITY_ID, "refund", {"transaction": "TRX_1"})
    )
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "capability_unsupported"
    assert seen == []


def test_a_malformed_command_payload_is_terminal_before_a_request_leaves() -> None:
    seen, respond = _recording(_envelope({}))
    handler = _handler(REFUND, respond)
    for payload in ({"action": "refund"}, {"action": "refund", "params": "not-a-map"}):
        request = DispatchRequest(
            capability_id=REFUND,
            event_type="command",
            payload=payload,
            config={"timeout_seconds": 5},
            secrets={"api_secret_key": SECRET},
            idempotency_key=KEY,
        )
        outcome = handler(request)
        assert outcome.status is OutcomeStatus.TERMINAL
    assert seen == []


def test_a_contract_error_names_a_field_rather_than_echoing_a_payload() -> None:
    with pytest.raises(OperationContractError) as raised:
        raise OperationContractError("email_required")
    assert raised.value.code.isidentifier()
