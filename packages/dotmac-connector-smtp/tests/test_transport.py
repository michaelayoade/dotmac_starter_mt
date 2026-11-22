"""The moved transport against the scripted sink: STARTTLS, hostname
verification, MIME, ambiguity after DATA, off-target refusal. No real network."""

from __future__ import annotations

import email
import re
import smtplib
import socket
import ssl
import time
from email import policy
from pathlib import Path

import pytest
from dotmac_connector_smtp.config import SmtpConfig, SmtpConfigError
from dotmac_connector_smtp.provider import (
    CODE_CONNECTION_ERROR,
    CODE_STARTTLS_UNAVAILABLE,
    CODE_TLS_VERIFICATION_FAILED,
    DEFAULT_DATA_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    SEND_DEADLINE_SECONDS,
    AttachmentRef,
    OutboundMessage,
    SmtpAttachmentError,
    SmtpAuthenticationError,
    SmtpOperatorFault,
    SmtpOutcomeUnknown,
    SmtpPayloadError,
    SmtpTargetRefused,
    SmtpTransport,
)
from dotmac_integration.spi import NetworkTarget
from smtp_sink_support import BAD_PW, GOOD_PW, OTHER_HOST, TLS_HOST, SmtpSink

HERE = Path(__file__).parent
DISPATCH = "11111111-2222-3333-4444-555555555555"
NON_ASCII_PW = "p\u00e4ssword"
LOOPBACK = [NetworkTarget("127.0.0.0/8", "tcp", 587, 65535)]


def cfg(sink: SmtpSink, **kw: object) -> SmtpConfig:
    base: dict[str, object] = {
        "host": TLS_HOST,
        "port": sink.port,
        "starttls": True,
        "username": "",
        "from_address": "Academy <academy@mail.example.test>",
        "production": True,
    }
    base.update(kw)
    return SmtpConfig(**base)  # type: ignore[arg-type]


def msg(**kw: object) -> OutboundMessage:
    base: dict[str, object] = {
        "dispatch_id": DISPATCH,
        "address": "learner@recipient.example.test",
        "subject": "Hello",
        "text": "plain body",
        "html": "<p>html body</p>",
    }
    base.update(kw)
    return OutboundMessage(**base)  # type: ignore[arg-type]


def send(
    sink, trusting, message=None, *, config=None, password=None, targets=None, **kw
):
    transport = SmtpTransport(ssl_context=trusting, timeout=3, **kw)
    return transport.send(
        message or msg(),
        config=config or cfg(sink),
        address="127.0.0.1",
        password=password,
        targets=LOOPBACK if targets is None else targets,
    )


# ------------------------------------------------------------------ MIME
def test_golden_mime_is_byte_identical_to_the_original_builder(sink, trusting) -> None:
    """`golden_message.eml` was produced by the ORIGINAL dotmac-delivery-smtp
    builder (boundary normalised); the moved builder must emit the same bytes,
    and header order must not depend on payload key order."""
    message = msg(
        subject="Welcome, Zoë",
        text="plain body\nline two",
        headers={
            "Reply-To": "help@mail.example.test",
            "List-Unsubscribe": "<https://mail.example.test/u>",
        },
    )
    golden = (HERE / "golden_message.eml").read_bytes()
    boundary = re.compile(rb"===============\d+==")
    built = SmtpTransport()._build(message, cfg(sink))
    assert boundary.sub(b"===============BOUNDARY==", built) == golden
    # And that is what crosses the wire (the sink strips the DATA terminator).
    assert send(sink, trusting, message).status == "accepted"
    wire = boundary.sub(b"===============BOUNDARY==", sink.messages[0])
    assert wire + b"\r\n" == golden


def test_header_order_does_not_depend_on_payload_key_order(sink) -> None:
    a = {
        "Reply-To": "h@mail.example.test",
        "List-Unsubscribe": "<https://mail.example.test/u>",
    }
    b = dict(reversed(list(a.items())))
    transport = SmtpTransport()
    strip = re.compile(rb"===============\d+==")
    assert strip.sub(b"", transport._build(msg(headers=a), cfg(sink))) == strip.sub(
        b"", transport._build(msg(headers=b), cfg(sink))
    )


def test_alternative_message_id_and_headers(sink, trusting) -> None:
    send(sink, trusting, msg(headers={"Reply-To": "help@mail.example.test"}))
    parsed = email.message_from_bytes(sink.messages[0], policy=policy.default)
    assert parsed["Message-ID"] == f"<{DISPATCH}@mail.example.test>"
    assert parsed.get_body(("plain",)).get_content().strip() == "plain body"
    assert parsed.get_body(("html",)).get_content().strip() == "<p>html body</p>"
    assert "STARTTLS" in sink.commands


def test_html_only_and_text_only(sink, trusting) -> None:
    assert send(sink, trusting, msg(text=None)).status == "accepted"
    assert send(sink, trusting, msg(html=None)).status == "accepted"
    kinds = [
        email.message_from_bytes(m, policy=policy.default).get_content_type()
        for m in sink.messages
    ]
    assert kinds == ["text/html", "text/plain"]


def test_header_allowlist_enforced_by_the_transport(sink, trusting) -> None:
    with pytest.raises(SmtpConfigError):
        send(sink, trusting, msg(headers={"X-Evil": "1"}))
    assert sink.messages == [] and sink.connections == 0


def test_attachments_need_a_resolver_and_are_capped(sink, trusting) -> None:
    ref = AttachmentRef("document", "d1", "cert.pdf", "application/pdf")
    with pytest.raises(SmtpAttachmentError):
        send(sink, trusting, msg(attachments=[ref]))
    done = send(
        sink,
        trusting,
        msg(attachments=[ref]),
        attachment_resolver=lambda r: b"%PDF-1.4 fake",
    )
    assert done.status == "accepted"
    parsed = email.message_from_bytes(sink.messages[0], policy=policy.default)
    assert next(parsed.iter_attachments()).get_content() == b"%PDF-1.4 fake"
    with pytest.raises(SmtpAttachmentError):
        send(
            sink,
            trusting,
            msg(attachments=[ref, ref, ref]),
            attachment_resolver=lambda r: b"x" * 600,
            max_attachment_bytes=1000,
            max_total_attachment_bytes=1500,
        )


# --------------------------------------------------------------- STARTTLS
def test_tls_is_negotiated_at_1_2_or_newer(sink, trusting) -> None:
    send(sink, trusting)
    assert sink.tls_versions and sink.tls_versions[0] in {"TLSv1.2", "TLSv1.3"}


def test_hostname_verification_rejects_a_valid_chain_for_the_wrong_name(
    wrong_name_sink, trusting
) -> None:
    """The context trusts the other certificate, so only the hostname check can
    refuse it. Nothing past EHLO/STARTTLS may happen, and no credential."""
    result = send(
        wrong_name_sink,
        trusting,
        config=cfg(wrong_name_sink, username="mailer"),
        password=GOOD_PW,
    )
    assert (result.status, result.response_code) == (
        "failed",
        CODE_TLS_VERIFICATION_FAILED,
    )
    assert not result.payload_sent
    assert not wrong_name_sink.auth_seen
    assert "MAIL" not in wrong_name_sink.commands
    assert wrong_name_sink.messages == []
    assert OTHER_HOST != TLS_HOST


def test_the_default_context_verifies_certificates(sink) -> None:
    result = SmtpTransport(timeout=3).send(
        msg(), config=cfg(sink), address="127.0.0.1", password=None, targets=LOOPBACK
    )
    assert (result.status, result.response_code) == (
        "failed",
        CODE_TLS_VERIFICATION_FAILED,
    )


def test_refuses_to_send_when_starttls_is_not_offered(sink, trusting) -> None:
    sink.advertise_starttls = False
    result = send(sink, trusting)
    assert (result.status, result.response_code) == (
        "failed",
        CODE_STARTTLS_UNAVAILABLE,
    )
    assert sink.messages == [] and "MAIL" not in sink.commands


def test_a_plaintext_configuration_is_refused_before_connecting(sink, trusting) -> None:
    for config in (
        cfg(sink, starttls=False, production=True),
        cfg(sink, starttls=False, production=False, username="u"),
    ):
        with pytest.raises(SmtpConfigError):
            send(sink, trusting, config=config, password=GOOD_PW)
    assert sink.connections == 0


def test_weak_or_non_verifying_contexts_are_refused() -> None:
    lax = ssl.create_default_context()
    lax.check_hostname = False
    lax.verify_mode = ssl.CERT_NONE
    with pytest.raises(SmtpConfigError):
        SmtpTransport(ssl_context=lax)
    old = ssl.create_default_context()
    with pytest.warns(DeprecationWarning):
        old.minimum_version = ssl.TLSVersion.TLSv1
    with pytest.raises(SmtpConfigError):
        SmtpTransport(ssl_context=old)


def test_the_default_context_floor_is_tls_1_2() -> None:
    assert SmtpTransport._default_context().minimum_version >= ssl.TLSVersion.TLSv1_2


# ---------------------------------------------------------- credentials
def test_password_is_used_only_after_starttls(sink, trusting) -> None:
    result = send(sink, trusting, config=cfg(sink, username="mailer"), password=GOOD_PW)
    assert result.status == "accepted" and sink.auth_seen
    assert not sink.plaintext_auth_seen


def test_missing_and_wrong_password_raise_without_the_value(sink, trusting) -> None:
    with pytest.raises(SmtpConfigError):
        send(sink, trusting, config=cfg(sink, username="mailer"), password=None)
    assert sink.connections == 0
    with pytest.raises(SmtpAuthenticationError) as bad:
        send(
            sink,
            trusting,
            config=cfg(sink, username="mailer"),
            password=BAD_PW,
        )
    assert BAD_PW not in str(bad.value)


# --------------------------------------------------- off-target refusal
def test_an_endpoint_outside_the_bound_targets_is_refused_before_any_io(
    sink, trusting, monkeypatch
) -> None:
    def boom(*a, **k):
        raise AssertionError("I/O attempted before the target check")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(smtplib.SMTP, "connect", boom)
    transport = SmtpTransport(ssl_context=trusting, timeout=3)
    off_target = [NetworkTarget("192.0.2.0/24", "tcp", 587, 587)]
    for targets in (off_target, [], LOOPBACK[:0]):
        with pytest.raises(SmtpTargetRefused):
            transport.send(
                msg(),
                config=cfg(sink),
                address="127.0.0.1",
                password=None,
                targets=targets,
            )
    wrong_port = [NetworkTarget("127.0.0.0/8", "tcp", 1, 586)]
    with pytest.raises(SmtpTargetRefused):
        transport.send(
            msg(),
            config=cfg(sink, port=587),
            address="127.0.0.1",
            password=None,
            targets=wrong_port,
        )
    assert sink.connections == 0


def test_the_checked_address_is_the_one_connected_to(
    sink, trusting, monkeypatch
) -> None:
    """A hostname is never resolved: the socket goes to the pinned address."""
    resolved: list[object] = []
    original = socket.getaddrinfo
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: resolved.append(a) or original(*a, **k)
    )
    seen: list[tuple[str, int]] = []
    real = socket.create_connection

    def spy(address, *a, **k):
        seen.append(address)
        return real(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", spy)
    assert send(sink, trusting).status == "accepted"
    assert seen == [("127.0.0.1", sink.port)]
    assert not any(TLS_HOST in str(call) for call in resolved)


# --------------------------------------------------- classification on wire
@pytest.mark.parametrize(
    ("key", "reply", "status"),
    [
        ("rcpt", "550 5.1.1 no such user", "bounced"),
        ("rcpt", "550 5.7.1 denied", "rejected"),
        ("rcpt", "451 4.7.1 later", "failed"),
        ("data_cmd", "554 5.3.0 refused", "rejected"),
        ("data_cmd", "451 4.3.0 try later", "failed"),
    ],
)
def test_replies_before_the_payload_leaves_are_definite(
    sink, trusting, key, reply, status
) -> None:
    sink.replies[key] = reply
    result = send(sink, trusting)
    assert result.status == status and result.response_code == reply[:3]
    assert not result.payload_sent and sink.messages == []


@pytest.mark.parametrize(
    ("key", "reply"),
    [("mail", "550 5.7.1 sender denied"), ("rcpt", "550 5.1.8 bad sender")],
)
def test_sender_side_refusals_raise(sink, trusting, key, reply) -> None:
    sink.replies[key] = reply
    with pytest.raises(SmtpOperatorFault):
        send(sink, trusting)
    assert sink.messages == []


def test_a_final_reply_after_the_payload_is_flagged_payload_sent(
    sink, trusting
) -> None:
    sink.replies["data"] = "451 4.3.0 local error"
    result = send(sink, trusting)
    assert (result.status, result.payload_sent) == ("failed", True)
    sink.replies["data"] = "554 5.7.1 spam"
    result = send(sink, trusting)
    assert (result.status, result.payload_sent) == ("rejected", True)


def test_a_reply_lost_after_data_is_unknown_never_failed(sink, trusting) -> None:
    sink.drop_after_data = True
    with pytest.raises(SmtpOutcomeUnknown):
        send(sink, trusting)
    assert len(sink.messages) == 1


def test_connection_problems_before_the_payload_are_retryable(sink, trusting) -> None:
    sink.greeting = "421 4.3.2 closing"
    assert send(sink, trusting).status == "failed"
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    result = send(sink, trusting, config=cfg(sink, port=port))
    assert (result.status, result.response_code, result.payload_sent) == (
        "failed",
        CODE_CONNECTION_ERROR,
        False,
    )


def test_connect_timeout_is_retryable() -> None:
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    try:
        config = SmtpConfig(
            TLS_HOST, silent.getsockname()[1], True, "", "a@mail.example.test", True
        )
        result = SmtpTransport(timeout=0.5).send(
            msg(), config=config, address="127.0.0.1", password=None, targets=LOOPBACK
        )
        assert (result.status, result.response_code, result.response_body) == (
            "failed",
            CODE_CONNECTION_ERROR,
            "timeout",
        )
    finally:
        silent.close()


# ----------------------------------------------------------- timeout budget
def test_data_reply_runs_under_the_long_timeout_and_quit_under_the_short_one(
    sink, trusting, monkeypatch
) -> None:
    seen: list[float | None] = []
    original = smtplib.SMTP.data

    def spy(self, message):
        seen.append(self.sock.gettimeout())
        return original(self, message)

    monkeypatch.setattr(smtplib.SMTP, "data", spy)
    transport = SmtpTransport(ssl_context=trusting, timeout=3, data_timeout=321)
    result = transport.send(
        msg(), config=cfg(sink), address="127.0.0.1", password=None, targets=LOOPBACK
    )
    assert result.status == "accepted" and seen == [321]


def test_default_budget_stays_below_the_300_second_lease() -> None:
    assert 11 * DEFAULT_TIMEOUT_SECONDS + 3 * DEFAULT_DATA_TIMEOUT_SECONDS == 285
    assert 11 * DEFAULT_TIMEOUT_SECONDS + 3 * DEFAULT_DATA_TIMEOUT_SECONDS < 300


@pytest.mark.parametrize("bad", [0, -1, True, "5", None, float("inf"), float("nan")])
@pytest.mark.parametrize("name", ["timeout", "data_timeout"])
def test_non_positive_or_non_finite_timeouts_are_refused(
    name: str, bad: object
) -> None:
    with pytest.raises(SmtpConfigError):
        SmtpTransport(**{name: bad})  # type: ignore[arg-type]


def test_non_ascii_recipient_is_rejected_without_connecting(sink, trusting) -> None:
    result = send(sink, trusting, msg(address="lëarner@recipient.example.test"))
    assert (result.status, result.response_code) == ("rejected", "non_ascii_address")
    assert sink.connections == 0


# ------------------------------------------------------- wall-clock deadline
def test_the_send_deadline_is_below_the_300_second_lease() -> None:
    assert 0 < SEND_DEADLINE_SECONDS <= 240 < 300
    with pytest.raises(SmtpConfigError):
        SmtpTransport(deadline=SEND_DEADLINE_SECONDS + 1)


def _timed(sink, trusting):
    started = time.monotonic()
    transport = SmtpTransport(ssl_context=trusting, timeout=5, deadline=1.0)
    try:
        return transport.send(
            msg(),
            config=cfg(sink),
            address="127.0.0.1",
            password=None,
            targets=LOOPBACK,
        ), time.monotonic() - started
    except SmtpOutcomeUnknown:
        return None, time.monotonic() - started


def test_a_server_trickling_the_greeting_is_cut_off_at_the_deadline(
    sink, trusting
) -> None:
    sink.trickle_greeting = True
    result, elapsed = _timed(sink, trusting)
    assert result is not None and result.status == "failed" and not result.payload_sent
    assert elapsed < 4  # each recv would otherwise be reset for ~30 s


def test_a_server_trickling_the_data_reply_is_unknown_at_the_deadline(
    sink, trusting
) -> None:
    sink.trickle_data_reply = True
    result, elapsed = _timed(sink, trusting)
    assert result is None  # SmtpOutcomeUnknown, never `failed`
    assert elapsed < 4 and len(sink.messages) == 1


def test_a_post_data_failure_of_any_type_is_unknown(
    sink, trusting, monkeypatch
) -> None:
    def boom(self, message):
        raise ValueError("not an smtplib error")

    monkeypatch.setattr(smtplib.SMTP, "data", boom)
    with pytest.raises(SmtpOutcomeUnknown):
        send(sink, trusting)


def test_a_build_error_is_a_payload_error_and_sends_nothing(sink, trusting) -> None:
    with pytest.raises(SmtpPayloadError):
        send(sink, trusting, msg(subject="a\r\nBcc: x@y.example"))
    assert sink.connections == 0


def test_a_non_ascii_credential_raises_a_config_error(sink, trusting) -> None:
    with pytest.raises(SmtpConfigError):
        send(sink, trusting, config=cfg(sink, username="mailer"), password=NON_ASCII_PW)


def test_ehlo_uses_the_from_domain_and_never_the_host_fqdn(
    sink, trusting, monkeypatch
) -> None:
    def forbidden():
        raise AssertionError("getfqdn called")

    monkeypatch.setattr(socket, "getfqdn", forbidden)
    assert send(sink, trusting).status == "accepted"
    assert sink.helo_names and set(sink.helo_names) == {"mail.example.test"}


def test_the_transport_refuses_a_plaintext_config_whatever_the_caller_built(
    sink, trusting
) -> None:
    with pytest.raises(SmtpConfigError):
        send(sink, trusting, config=cfg(sink, starttls=False, production=False))
    assert sink.connections == 0


# ------------------------------------------------ F-1: raw shutdown, checks
def test_the_watchdog_shuts_down_the_raw_socket_never_the_tls_object(
    sink, trusting, monkeypatch
) -> None:
    tls_calls: list[object] = []
    raw_calls: list[object] = []
    original_raw = socket.socket.shutdown
    monkeypatch.setattr(
        ssl.SSLSocket, "shutdown", lambda self, how: tls_calls.append(how)
    )
    monkeypatch.setattr(
        socket.socket,
        "shutdown",
        lambda self, how: raw_calls.append(how) or original_raw(self, how),
    )
    sink.trickle_data_reply = True  # TLS is up when the deadline fires
    result, _ = _timed(sink, trusting)
    assert result is None
    assert raw_calls and not tls_calls


def _expire_after(monkeypatch, name: str) -> None:
    """Mark the deadline as passed right after the named session step."""
    from dotmac_connector_smtp import provider

    original = getattr(provider._PinnedSMTP, name)

    def wrapper(self, *a, **k):
        out = original(self, *a, **k)
        self._watchdog.fired = True
        return out

    monkeypatch.setattr(provider._PinnedSMTP, name, wrapper)


def test_credentials_are_never_sent_once_the_deadline_has_passed(
    sink, trusting, monkeypatch
) -> None:
    _expire_after(monkeypatch, "ehlo")
    result = send(sink, trusting, config=cfg(sink, username="mailer"), password=GOOD_PW)
    assert (result.status, result.payload_sent) == ("failed", False)
    assert "AUTH" not in sink.commands and not sink.auth_seen


def test_the_payload_never_leaves_once_the_deadline_has_passed(
    sink, trusting, monkeypatch
) -> None:
    _expire_after(monkeypatch, "rcpt")
    result = send(sink, trusting)
    assert (result.status, result.payload_sent) == ("failed", False)
    assert "DATA" not in sink.commands and sink.messages == []
