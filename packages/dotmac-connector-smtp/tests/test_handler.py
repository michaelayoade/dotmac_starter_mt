"""The DELIVERY handler: payload and config in, one classified Outcome out."""

from __future__ import annotations

import ssl

import pytest
from dotmac_connector_smtp import MANIFEST, PLUGIN, SEND_CAPABILITY
from dotmac_connector_smtp.plugin import (
    PayloadError,
    SmtpConnector,
    message_from_payload,
    outcome_for,
)
from dotmac_connector_smtp.provider import SendResult, SmtpTransport
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import (
    NETWORK_TARGETS_CONFIG_KEY,
    DispatchRequest,
    InvalidManifestError,
    NetworkTarget,
    network_targets_to_json,
)
from smtp_sink_support import BAD_PW, GOOD_PW, TLS_HOST, SmtpSink

LOOPBACK = [NetworkTarget("127.0.0.0/8", "tcp", 587, 65535)]


def payload(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "dispatch_id": "11111111-2222-3333-4444-555555555555",
        "address": "learner@recipient.example.test",
        "subject": "Hello",
        "text": "plain body",
        "html": "<p>html body</p>",
    }
    base.update(kw)
    return base


def request(
    sink: SmtpSink,
    *,
    body: dict[str, object] | None = None,
    targets=LOOPBACK,
    secrets: dict[str, object] | None = None,
    **config: object,
) -> DispatchRequest:
    cfg: dict[str, object] = {
        "host": TLS_HOST,
        "address": "127.0.0.1",
        "port": sink.port,
        "from_address": "Academy <academy@mail.example.test>",
    }
    cfg.update(config)
    cfg[NETWORK_TARGETS_CONFIG_KEY] = network_targets_to_json(targets)
    return DispatchRequest(
        capability_id=SEND_CAPABILITY,
        event_type="delivery.dispatch.v1",
        payload=body if body is not None else payload(),
        config=cfg,
        secrets=secrets if secrets is not None else {},
        idempotency_key="idem-1",
    )


@pytest.fixture
def handler(trusting: ssl.SSLContext):
    return SmtpConnector(
        transport=SmtpTransport(ssl_context=trusting, timeout=3)
    ).handler_for(SEND_CAPABILITY)


def test_accepted_is_succeeded_with_the_message_id_as_reference(handler, sink) -> None:
    out = handler(request(sink))
    assert isinstance(out, Outcome) and out.status is OutcomeStatus.SUCCEEDED
    assert (
        out.provider_reference
        == "<11111111-2222-3333-4444-555555555555@mail.example.test>"
    )
    assert out.provider_status_code is None and out.error_code is None
    assert len(sink.messages) == 1


def test_the_password_is_materialised_from_the_secret_binding(handler, sink) -> None:
    out = handler(request(sink, secrets={"smtp_password": GOOD_PW}, username="mailer"))
    assert out.status is OutcomeStatus.SUCCEEDED and sink.auth_seen
    missing = handler(request(sink, username="mailer"))
    assert (missing.status, missing.error_code) == (
        OutcomeStatus.RETRYABLE,
        "config_invalid",
    )


def test_wrong_password_is_retryable_and_never_echoed(handler, sink) -> None:
    out = handler(request(sink, secrets={"smtp_password": BAD_PW}, username="mailer"))
    assert (out.status, out.error_code) == (
        OutcomeStatus.RETRYABLE,
        "smtp_auth_rejected",
    )
    assert BAD_PW not in repr(out)
    # One bad password must not cost every queued message an AUTH attempt.
    assert out.retry_after_seconds is not None and out.retry_after_seconds >= 900


def test_a_non_ascii_credential_is_a_config_fault_not_a_payload_fault(
    handler, sink
) -> None:
    out = handler(
        request(sink, secrets={"smtp_password": "pässword"}, username="mailer")
    )
    assert (out.status, out.error_code) == (OutcomeStatus.RETRYABLE, "config_invalid")
    assert sink.messages == []


@pytest.mark.parametrize("reply", ["503 5.5.1 bad sequence", "502 5.5.2", "504 x"])
def test_smtp_reply_codes_never_reach_the_http_status_field(
    handler, sink, reply
) -> None:
    """a20 treats 500/502/503/504 in `provider_status_code` as retryable and a
    503 as an installation-wide pause. After DATA that must not happen."""
    from dotmac_integration.retry import next_state, throttle_cooldown_seconds

    sink.replies["data"] = reply
    out = handler(request(sink))
    assert out.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert out.provider_status_code is None
    assert throttle_cooldown_seconds(out) is None
    assert next_state(out, attempt_count=1) != "retryable"
    sink.replies["rcpt"] = reply
    pre = handler(request(sink))
    assert pre.provider_status_code is None and throttle_cooldown_seconds(pre) is None
    assert next_state(pre, attempt_count=1) != "retryable"


# ------------------------------------------------- AMBIGUOUS after DATA
def test_a_lost_reply_after_data_is_reconciliation_required(handler, sink) -> None:
    sink.drop_after_data = True
    out = handler(request(sink))
    assert out.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert out.error_code == "smtp_ambiguous_after_data"
    assert out.is_final  # never rescheduled by the engine


@pytest.mark.parametrize(
    "reply", ["451 4.3.0 local error", "554 5.7.1 spam", "421 4.7.0 slow down"]
)
def test_any_non_250_final_reply_after_data_is_ambiguous_not_retryable(
    handler, sink, reply
) -> None:
    sink.replies["data"] = reply
    out = handler(request(sink))
    assert out.status is OutcomeStatus.RECONCILIATION_REQUIRED
    assert out.error_code == f"smtp_ambiguous_after_data_{reply[:3]}"
    assert out.status is not OutcomeStatus.RETRYABLE


def test_outcome_for_marks_every_payload_sent_failure_ambiguous() -> None:
    for status in ("failed", "rejected", "bounced"):
        out = outcome_for(
            SendResult(status, "451", None, payload_sent=True), message_id="<x@y>"
        )
        assert out.status is OutcomeStatus.RECONCILIATION_REQUIRED


# ------------------------------------------------ definite, pre-DATA verdicts
@pytest.mark.parametrize(
    ("key", "reply", "status", "code"),
    [
        ("rcpt", "550 5.1.1 no such user", OutcomeStatus.TERMINAL, "smtp_bounced_550"),
        ("rcpt", "550 5.7.1 denied", OutcomeStatus.TERMINAL, "smtp_rejected_550"),
        ("rcpt", "451 4.7.1 later", OutcomeStatus.RETRYABLE, "smtp_reply_451"),
        ("data_cmd", "554 5.3.0 refused", OutcomeStatus.TERMINAL, "smtp_rejected_554"),
        (
            "mail",
            "550 5.7.1 sender denied",
            OutcomeStatus.RETRYABLE,
            "smtp_operator_fault",
        ),
    ],
)
def test_pre_data_verdicts(handler, sink, key, reply, status, code) -> None:
    sink.replies[key] = reply
    out = handler(request(sink))
    assert (out.status, out.error_code) == (status, code)
    assert sink.messages == []


def test_reply_bodies_never_reach_the_outcome(handler, sink) -> None:
    sink.replies["rcpt"] = "550 5.1.1 <learner@recipient.example.test> unknown"
    out = handler(request(sink))
    assert "recipient.example.test" not in repr(out) + (out.error_detail or "")


def test_tls_failure_and_no_starttls_are_retryable(
    handler, sink, wrong_name_sink
) -> None:
    sink.advertise_starttls = False
    out = handler(request(sink))
    assert (out.status, out.error_code) == (
        OutcomeStatus.RETRYABLE,
        "smtp_starttls_unavailable",
    )
    bad = handler(request(wrong_name_sink, secrets={}))
    assert (bad.status, bad.error_code) == (
        OutcomeStatus.RETRYABLE,
        "smtp_tls_verification_failed",
    )


# ------------------------------------------------------------ refusals
def test_off_target_is_refused_before_any_connection(handler, sink) -> None:
    for targets in ([NetworkTarget("192.0.2.0/24", "tcp", 587, 587)], []):
        out = handler(request(sink, targets=targets))
        assert (out.status, out.error_code) == (
            OutcomeStatus.RETRYABLE,
            "network_target_refused",
        )
    assert sink.connections == 0


def test_an_empty_bound_target_list_refuses_every_send(handler, sink) -> None:
    req = request(sink, targets=[])
    assert req.config[NETWORK_TARGETS_CONFIG_KEY] == []
    assert handler(req).error_code == "network_target_refused"


@pytest.mark.parametrize(
    "bad",
    [
        {"host": ""},
        {"address": "mail.example.test"},
        {"port": 0},
        {"port": "587"},
        {"from_address": "not-an-address"},
        {"unexpected": "x"},
    ],
)
def test_unusable_configuration_is_retryable_and_connects_nowhere(
    handler, sink, bad
) -> None:
    out = handler(request(sink, **bad))
    assert (out.status, out.error_code) == (OutcomeStatus.RETRYABLE, "config_invalid")
    assert sink.connections == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"dispatch_id": "bad id\r\nBcc: x"},
        {"address": "a@b\r\nBcc: x@y"},
        {"address": "no-at-sign"},
        {"text": None, "html": None},
        {"subject": 5},
        {"headers": {"X-Evil": "1"}},
        {"headers": {"Reply-To": "a\r\nBcc: x@y.example"}},
        {"attachments": "nope"},
    ],
)
def test_malformed_payloads_are_terminal_and_connect_nowhere(
    handler, sink, bad
) -> None:
    out = handler(request(sink, body=payload(**bad)))
    assert (out.status, out.error_code) == (OutcomeStatus.TERMINAL, "invalid_payload")
    assert sink.connections == 0 and sink.messages == []


def test_attachments_without_a_bound_resolver_are_terminal(handler, sink) -> None:
    body = payload(
        attachments=[
            {
                "kind": "document",
                "ref": "d1",
                "filename": "a.pdf",
                "content_type": "application/pdf",
            }
        ]
    )
    out = handler(request(sink, body=body))
    assert (out.status, out.error_code) == (
        OutcomeStatus.TERMINAL,
        "attachment_refused",
    )
    assert sink.connections == 0


def test_payload_parser_names_only_the_key() -> None:
    with pytest.raises(PayloadError) as err:
        message_from_payload(payload(address="secret@x\r\n"))
    assert str(err.value) == "address"


def test_an_unanticipated_error_propagates_so_the_engine_marks_it_ambiguous(
    sink,
) -> None:
    class Exploding(SmtpTransport):
        def send(self, *a, **k):
            raise RuntimeError("boom")

    h = SmtpConnector(transport=Exploding()).handler_for(SEND_CAPABILITY)
    with pytest.raises(RuntimeError):
        h(request(sink))


def test_the_shipped_plugin_is_wired_to_the_manifest() -> None:
    assert PLUGIN.manifest is MANIFEST
    with pytest.raises(InvalidManifestError):
        PLUGIN.handler_for("email.undeclared.v1")
