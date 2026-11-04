"""Refund commands: what may be retried, and what must never be.

This suite is mostly NEGATIVE on purpose. A payment connector that only proves
the happy path proves the half that costs nothing when it is wrong. The cases
worth the file are:

* a read timeout after a refund is sent is AMBIGUOUS, so nothing refunds twice;
* an ambiguous refund is DECIDED by `find_refund`, never by sending it again;
* one idempotency key produces one provider reference, on every attempt.

Two of the checks below are SENSITIVITY PROOFS rather than assertions about
behaviour: the identical transport failure against the pure read `find_refund`
must come back RETRYABLE (a classifier that answered AMBIGUOUS to everything
would pass every other test here), and the two totality guards are driven with
a deliberately broken table.

Payment intent, saved-authorization charge, payout and customer operations were
removed from this release (D5, 2026-11-04); `test_the_removed_surfaces_are_gone`
pins that.
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

REFUND = "payments.refund.v1"
REMOVED_CAPABILITIES = (
    "payments.intent.v1",
    "payments.payout.v1",
    "payments.customer.v1",
)
REMOVED_OPERATIONS = (
    "initialize_payment",
    "charge_authorization",
    "resolve_bank_account",
    "create_transfer_recipient",
    "initiate_transfer",
    "create_customer",
    "update_customer",
    "read_customer",
)


def _ops(handler: Callable[[httpx.Request], httpx.Response]) -> PaystackOperations:
    return PaystackOperations(transport=httpx.MockTransport(handler), timeout_seconds=5)


def _envelope(data: object, **extra: object) -> dict[str, object]:
    return {"status": True, "message": "ok", "data": data, **extra}


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


def _refund(ops: PaystackOperations, *, key: str = KEY, **overrides: object):
    params: dict[str, object] = {
        "transaction": "TRX_target",
        "amount": "1500.00",
        "currency": "NGN",
    }
    params.update(overrides)
    return ops.run("refund", params, idempotency_key=key, api_secret=SECRET)


LOOKUP_KEY = "lookup-delivery-9a1e-another-key"


def _find(ops: PaystackOperations, *, key: str = LOOKUP_KEY, **overrides: object):
    # A lookup is its OWN delivery: a different engine key from the refund it
    # asks about. The subject travels in the payload.
    params: dict[str, object] = {
        "transaction": "TRX_target",
        "refund_reference": provider_reference(KEY),
    }
    params.update(overrides)
    return ops.run("find_refund", params, idempotency_key=key, api_secret=SECRET)


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": 4242,
        "status": "processed",
        "merchant_note": provider_reference(KEY),
        "amount": 150000,
        "currency": "NGN",
    }
    record.update(overrides)
    return record


# ── the contract the manifest publishes ──────────────────────────────────────


def test_refund_is_a_delivery_capability_separate_from_observation() -> None:
    assert MANIFEST.version == __version__ == "0.1.0a3"
    assert MANIFEST.spi_range.minimum.minor == 4
    assert PLUGIN.modes == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
    )
    declared = {c.capability_id: c.modes for c in MANIFEST.capabilities}
    assert set(declared) == {CAPABILITY_ID, REFUND}
    assert declared[CAPABILITY_ID] == frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL}
    )
    # A settlement watcher must not acquire the authority to move money just
    # because the same distribution can do both.
    assert declared[REFUND] == frozenset({ConnectorMode.DELIVERY})
    assert OUTBOUND_CAPABILITY_IDS == (REFUND,)
    assert MANIFEST.egress is not None
    assert MANIFEST.egress.hosts == (API_HOST,)


def test_the_plugin_still_conforms_to_the_spi_with_three_modes_declared() -> None:
    assert_plugin_conforms(PLUGIN)


def test_the_operations_are_exactly_refund_and_its_read() -> None:
    assert set(OPERATIONS) == {"refund", "find_refund"}
    assert ACTIONS_BY_CAPABILITY[REFUND] == frozenset({"refund", "find_refund"})


def test_the_removed_surfaces_are_gone() -> None:
    declared = {c.capability_id for c in MANIFEST.capabilities}
    for capability in REMOVED_CAPABILITIES:
        assert capability not in declared
        assert capability not in ACTIONS_BY_CAPABILITY
    for operation in REMOVED_OPERATIONS:
        assert operation not in OPERATIONS


@pytest.mark.parametrize("operation", REMOVED_OPERATIONS)
def test_a_removed_operation_never_reaches_the_provider(operation: str) -> None:
    seen, respond = _recording(_envelope({}))
    result = _ops(respond).run(operation, {}, idempotency_key=KEY, api_secret=SECRET)
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "operation_unsupported"
    assert seen == []


@pytest.mark.parametrize("operation", REMOVED_OPERATIONS)
def test_a_removed_operation_is_not_dispatchable_through_the_refund_binding(
    operation: str,
) -> None:
    seen, respond = _recording(_envelope({}))
    outcome = _handler(REFUND, respond)(_dispatch(REFUND, operation, {}))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "operation_not_allowed"
    assert seen == []


@pytest.mark.parametrize("capability", REMOVED_CAPABILITIES)
def test_a_removed_capability_is_not_a_delivery_binding(capability: str) -> None:
    seen, respond = _recording(_envelope({}))
    outcome = _handler(capability, respond)(_dispatch(capability, "refund", {}))
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "capability_unsupported"
    assert seen == []


# ── a decline is not a retryable error ───────────────────────────────────────


def test_a_declined_refund_is_terminal_and_is_never_retried() -> None:
    result = _refund(
        _ops(_responder(_envelope({"id": 9, "status": "failed", "amount": 150000})))
    )
    assert result.outcome is OperationOutcome.DECLINED
    assert result.error_code == "provider_declined"
    outcome = result.as_outcome()
    assert outcome.status is not OutcomeStatus.RETRYABLE
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.is_final


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


def test_a_read_timeout_after_a_refund_is_ambiguous() -> None:
    result = _refund(_ops(_raiser(httpx.ReadTimeout)))
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
    result = _refund(_ops(_raiser(failure)))
    assert result.as_outcome().status is OutcomeStatus.RECONCILIATION_REQUIRED


def test_a_connect_failure_never_reached_the_provider_so_it_is_retryable() -> None:
    for failure in (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
        result = _refund(_ops(_raiser(failure)))
        assert result.outcome is OperationOutcome.RETRYABLE, failure
        assert result.error_code == "provider_connect_failed"


def test_the_ambiguity_rule_discriminates_and_does_not_answer_ambiguous_to_all() -> (
    None
):
    """Sensitivity proof: the identical failure against the pure read."""
    result = _find(_ops(_raiser(httpx.ReadTimeout)))
    assert result.outcome is OperationOutcome.RETRYABLE
    assert result.as_outcome().status is OutcomeStatus.RETRYABLE


def test_a_server_error_is_ambiguous_for_a_refund_and_retryable_for_the_read() -> None:
    assert _refund(_ops(_responder({}, 502))).outcome is OperationOutcome.AMBIGUOUS
    assert _find(_ops(_responder({}, 502))).outcome is OperationOutcome.RETRYABLE


def test_a_two_hundred_that_says_it_did_not_happen_is_terminal() -> None:
    result = _refund(
        _ops(_responder({"status": False, "message": "Transaction not found"}))
    )
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "provider_refused_request"


def _unreadable(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>maintenance</html>")


def test_an_unreadable_two_hundred_is_never_read_as_a_refusal() -> None:
    """ "We could not tell" is not "it did not happen"."""
    refunded = _refund(_ops(_unreadable))
    assert refunded.outcome is OperationOutcome.AMBIGUOUS
    assert refunded.error_code == "provider_response_unreadable"
    assert _find(_ops(_unreadable)).outcome is OperationOutcome.RETRYABLE


# ── one idempotency key, one provider reference ──────────────────────────────


def test_the_same_idempotency_key_stamps_the_same_note_on_every_attempt() -> None:
    seen, respond = _recording(_envelope(_record()))
    ops = _ops(respond)
    first = _refund(ops)
    second = _refund(ops)
    notes = [json.loads(request.content)["merchant_note"] for request in seen]
    assert notes == [provider_reference(KEY)] * 2
    assert first.provider_reference == second.provider_reference


def test_a_different_delivery_gets_a_different_reference() -> None:
    assert provider_reference(KEY) != provider_reference(KEY + "-2")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "short",
        "UPPER-case-key-0123456789",
        "has space in the key-123",
        "a" * 65,
        "-leading-dash-0123456",
    ],
)
def test_a_refund_whose_key_is_not_a_reference_is_refused_not_repaired(key) -> None:
    seen, respond = _recording(_envelope({"id": 1}))
    result = _refund(_ops(respond), key=key)
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "idempotency_key_shape"
    # Nothing was sent: a normalised or generated note would never be found.
    assert seen == []


def test_the_key_is_stamped_verbatim_and_a_payments_reference_is_accepted() -> None:
    seen, respond = _recording(_envelope(_record()))
    reference = "rf1-" + "0123456789abcdef" * 2
    _refund(_ops(respond), key=reference)
    assert json.loads(seen[0].content)["merchant_note"] == reference
    assert provider_reference(reference) == reference


def test_a_refund_carries_the_derived_key_where_a_reconciler_can_read_it() -> None:
    seen, respond = _recording(
        _envelope(
            {"id": 4242, "status": "pending", "amount": 150000, "currency": "NGN"}
        )
    )
    result = _refund(_ops(respond))
    body = json.loads(seen[0].content)
    assert body["merchant_note"] == provider_reference(KEY)
    assert body["transaction"] == "TRX_target"
    assert body["amount"] == 150000
    assert isinstance(body["amount"], int)
    assert body["currency"] == "NGN"
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.provider_status == "pending"


def test_a_refund_the_provider_echoes_at_another_amount_is_not_a_success() -> None:
    result = _refund(
        _ops(
            _responder(
                _envelope(
                    {
                        "id": 4242,
                        "status": "pending",
                        "amount": 149999,
                        "currency": "NGN",
                    }
                )
            )
        )
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_evidence_incomplete"


def test_a_refund_with_no_provider_receipt_is_not_conclusive() -> None:
    result = _refund(_ops(_responder(_envelope({"status": "pending"}))))
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_receipt_missing"


def test_a_refund_with_no_requested_amount_takes_the_transactions_own() -> None:
    seen, respond = _recording(_envelope({"id": 4242, "status": "processed"}))
    result = _ops(respond).run(
        "refund",
        {"transaction": "TRX_target"},
        idempotency_key=KEY,
        api_secret=SECRET,
    )
    assert "amount" not in json.loads(seen[0].content)
    assert result.outcome is OperationOutcome.SUCCEEDED


# ── find_refund: deciding an ambiguous refund without re-sending it ──────────


def test_find_refund_is_a_get_that_filters_by_transaction_and_sends_no_body() -> None:
    seen, respond = _recording(_envelope([]))
    _find(_ops(respond))
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/refund"
    assert request.url.params["transaction"] == "TRX_target"
    assert request.content == b""
    assert request.headers["authorization"] == f"Bearer {SECRET}"


def test_find_refund_zero_matches_means_not_found_as_of_this_read() -> None:
    other = _record(merchant_note=provider_reference("another-delivery"), id=1)
    result = _find(_ops(_responder(_envelope([other]))))
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.reply == {"matches": "0"}
    assert result.provider_reference == provider_reference(KEY)


def test_find_refund_an_empty_list_means_not_found_as_of_this_read() -> None:
    result = _find(_ops(_responder(_envelope([]))))
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.reply == {"matches": "0"}


def test_find_refund_one_exact_match_means_found_and_carries_evidence() -> None:
    other = _record(merchant_note="hand typed note", id=1)
    result = _find(_ops(_responder(_envelope([other, _record()]))))
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.reply == {
        "matches": "1",
        "refund_id": "4242",
        "refund_status": "processed",
    }
    assert result.provider_transaction_id == "4242"
    assert result.provider_status == "processed"


def test_find_refund_more_than_one_match_needs_a_human() -> None:
    result = _find(
        _ops(_responder(_envelope([_record(), _record(id=4243, status="failed")])))
    )
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "refund_match_not_unique"
    assert result.reply == {"matches": "many"}
    assert result.as_outcome().status is OutcomeStatus.RECONCILIATION_REQUIRED


def test_find_refund_matches_the_note_exactly_not_by_substring() -> None:
    padded = _record(merchant_note=provider_reference(KEY) + "0")
    result = _find(_ops(_responder(_envelope([padded]))))
    assert result.reply == {"matches": "0"}


def test_find_refund_a_match_without_a_provider_id_is_not_conclusive() -> None:
    result = _find(_ops(_responder(_envelope([_record(id=None)]))))
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "provider_receipt_missing"


def test_find_refund_a_list_that_reports_another_page_decides_nothing() -> None:
    body = _envelope([], meta={"pageCount": 2})
    result = _find(_ops(_responder(body)))
    assert result.outcome is OperationOutcome.AMBIGUOUS
    assert result.error_code == "refund_list_truncated"


def test_find_refund_a_full_page_with_no_meta_decides_nothing() -> None:
    full = [_record(merchant_note="x", id=n) for n in range(100)]
    result = _find(_ops(_responder(_envelope(full))))
    assert result.error_code == "refund_list_truncated"


def test_find_refund_a_non_list_payload_is_not_read_as_no_refund() -> None:
    result = _find(_ops(_responder(_envelope({"id": 1}))))
    assert result.outcome is OperationOutcome.RETRYABLE
    assert result.error_code == "provider_receipt_missing"
    assert "matches" not in result.reply


def test_find_refund_never_sends_the_refund() -> None:
    seen, respond = _recording(_envelope([]))
    _find(_ops(respond))
    assert [request.method for request in seen] == ["GET"]


def test_find_refund_requires_the_transaction_and_a_well_formed_subject() -> None:
    seen, respond = _recording(_envelope([]))
    missing = _find(_ops(respond), transaction="")
    assert missing.error_code == "transaction_required"
    for bad in (None, "", "hand typed note", "X" * 20, "a" * 65, 7):
        refused = _find(_ops(respond), refund_reference=bad)
        assert refused.outcome is OperationOutcome.TERMINAL
        assert refused.error_code == "refund_reference_invalid"
    assert seen == []
    # The lookup's own delivery key is irrelevant to what it searches for.
    assert _find(_ops(respond), key="").outcome is OperationOutcome.SUCCEEDED


def test_find_refund_a_forbidden_credential_is_terminal() -> None:
    result = _find(_ops(_responder({"status": False}, 403)))
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "authentication_rejected"


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


def test_an_amount_that_is_not_exact_is_refused_before_a_refund_leaves() -> None:
    seen, respond = _recording(_envelope(_record()))
    result = _refund(_ops(respond), amount="100.005")
    assert result.outcome is OperationOutcome.TERMINAL
    assert result.error_code == "amount_finer_than_wire_scale"
    assert seen == []


# ── credentials and persisted text ───────────────────────────────────────────


def test_a_rate_limit_is_retryable_and_the_providers_own_delay_travels() -> None:
    result = _find(_ops(_responder({}, 429, {"retry-after": "17"})))
    assert result.outcome is OperationOutcome.RETRYABLE
    assert result.as_outcome().retry_after_seconds == 17


def test_rejected_credentials_are_terminal_rather_than_retried_forever() -> None:
    result = _refund(_ops(_responder({"status": False}, 401)))
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
    result = _ops(respond).run(
        "refund",
        {"transaction": "TRX_target"},
        idempotency_key=KEY,
        api_secret="",
    )
    assert result.error_code == "api_secret_key_unavailable"
    assert seen == []


def test_no_provider_text_or_credential_reaches_the_persisted_outcome() -> None:
    body = {"status": False, "message": f"rejected for {SECRET} and payer@example.test"}
    outcome = _refund(_ops(_responder(body, 422))).as_outcome()
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


def test_every_operation_is_reachable_through_exactly_one_capability() -> None:
    assert set(ACTIONS_BY_CAPABILITY) == set(OUTBOUND_CAPABILITY_IDS)
    assert _misallocated_operations(OPERATIONS, ACTIONS_BY_CAPABILITY) == frozenset()


def test_the_capability_allocation_guard_still_bites() -> None:
    """Sensitivity proof: the guard must fail on both ways of being wrong."""
    unreachable = {REFUND: frozenset({"find_refund"})}
    assert "refund" in _misallocated_operations(OPERATIONS, unreachable)
    overlapping = {
        REFUND: frozenset({"refund", "find_refund"}),
        "payments.other.v1": frozenset({"refund"}),
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
    seen, respond = _recording(_envelope(_record()))
    outcome = _handler(REFUND, respond)(
        _dispatch(
            REFUND,
            "refund",
            {"transaction": "TRX_target", "amount": "1500.00", "currency": "NGN"},
        )
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.provider_reference == provider_reference(KEY)
    assert outcome.provider_status_code == 200
    assert json.loads(seen[0].content)["merchant_note"] == provider_reference(KEY)


def test_find_refund_is_dispatchable_through_the_refund_binding() -> None:
    seen, respond = _recording(_envelope([]))
    outcome = _handler(REFUND, respond)(
        _dispatch(
            REFUND,
            "find_refund",
            {"transaction": "TRX_target", "refund_reference": provider_reference(KEY)},
        )
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert [request.method for request in seen] == ["GET"]


def test_a_dispatched_timeout_reaches_the_engine_as_reconciliation_required() -> None:
    outcome = _handler(REFUND, _raiser(httpx.ReadTimeout))(
        _dispatch(REFUND, "refund", {"transaction": "TRX_target"})
    )
    assert outcome.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert outcome.status is not OutcomeStatus.RETRYABLE


def test_a_refund_binding_without_a_configured_timeout_is_refused() -> None:
    seen, respond = _recording(_envelope([]))
    request = _dispatch(REFUND, "find_refund", {"transaction": "TRX_target"})
    stripped = DispatchRequest(
        capability_id=request.capability_id,
        event_type=request.event_type,
        payload=request.payload,
        config={},
        secrets=request.secrets,
        idempotency_key=request.idempotency_key,
    )
    outcome = _handler(REFUND, respond)(stripped)
    assert outcome.status is OutcomeStatus.TERMINAL
    assert outcome.error_code == "timeout_seconds_invalid"
    assert seen == []


def test_a_refund_binding_without_the_server_secret_is_refused() -> None:
    seen, respond = _recording(_envelope([]))
    request = _dispatch(REFUND, "find_refund", {"transaction": "TRX_target"})
    stripped = DispatchRequest(
        capability_id=request.capability_id,
        event_type=request.event_type,
        payload=request.payload,
        config=request.config,
        secrets={},
        idempotency_key=request.idempotency_key,
    )
    outcome = _handler(REFUND, respond)(stripped)
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
        raise OperationContractError("transaction_required")
    assert raised.value.code.isidentifier()


# ── the lookup through the engine: its own key, a verdict that survives ──────


def test_a_lookup_under_another_delivery_key_finds_the_refund_the_first_sent() -> None:
    """The double-refund case. The refund and the lookup are separate deliveries
    (the engine refuses to reuse a key for a different payload), so the lookup's
    key differs and the subject must come from the payload."""
    store: list[dict[str, object]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            record = {
                "id": 77,
                "status": "pending",
                "merchant_note": body["merchant_note"],
                "amount": body["amount"],
                "currency": body["currency"],
            }
            store.append(record)
            return httpx.Response(200, json=_envelope(record))
        return httpx.Response(200, json=_envelope(list(store)))

    ops = _ops(provider)
    sent = _refund(ops, key="rf1-" + "aa" * 16)
    # The answer to the send is lost; reconcile with a different delivery key.
    found = ops.run(
        "find_refund",
        {"transaction": "TRX_target", "refund_reference": sent.provider_reference},
        idempotency_key="rf1-" + "bb" * 16 + "-lookup",
        api_secret=SECRET,
    )
    assert found.outcome is OperationOutcome.SUCCEEDED
    assert found.reply["matches"] == "1"
    assert found.reply["refund_id"] == "77"


def test_the_lookup_verdict_reaches_the_engine_in_the_outcome_result() -> None:
    none = _find(_ops(_responder(_envelope([])))).as_outcome()
    one = _find(_ops(_responder(_envelope([_record()])))).as_outcome()
    assert none.status is one.status is OutcomeStatus.SUCCEEDED
    assert none.result == {"matches": "0"}
    assert one.result == {
        "matches": "1",
        "refund_id": "4242",
        "refund_status": "processed",
    }
    assert none.result != one.result


def test_only_a_successful_lookup_carries_a_result() -> None:
    many = _find(_ops(_responder(_envelope([_record(), _record(id=1)])))).as_outcome()
    assert many.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert many.result is None
    seen, respond = _recording(_envelope(_record()))
    assert _refund(_ops(respond)).as_outcome().result is None


def test_the_lookup_through_the_delivery_handler_carries_the_result() -> None:
    seen, respond = _recording(_envelope([_record()]))
    outcome = _handler(REFUND, respond)(
        _dispatch(
            REFUND,
            "find_refund",
            {"transaction": "TRX_target", "refund_reference": provider_reference(KEY)},
        )
    )
    assert outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome.result is not None and outcome.result["matches"] == "1"
