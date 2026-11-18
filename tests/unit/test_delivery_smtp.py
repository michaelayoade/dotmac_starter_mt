"""dotmac-delivery-smtp: classification, STARTTLS, MIME, hook, header allowlist.

A stdlib-only scripted SMTP server (no aiosmtpd dependency) on loopback with a
throwaway self-signed certificate generated per session (documentation-range
free: loopback only, no estate addresses).
"""

from __future__ import annotations

import datetime
import email
import ipaddress
import socket
import ssl
import threading
from collections.abc import Iterator
from email import policy
from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from dotmac_delivery_smtp import (
    SmtpConfig,
    SmtpConfigError,
    SmtpDeliveryProvider,
    SmtpOutcomeUnknown,
    classify_reply,
)
from dotmac_delivery_smtp.manifest import module
from dotmac_delivery_smtp.provider import (
    CODE_CONNECTION_ERROR,
    CODE_STARTTLS_UNAVAILABLE,
    CODE_TLS_VERIFICATION_FAILED,
    SmtpAdmissionRefused,
    SmtpAttachmentError,
    SmtpAuthenticationError,
    SmtpOperatorFault,
)
from dotmac_kernel import secret_sources
from dotmac_kernel.delivery_providers import (
    AttachmentRef,
    DeliveryProvider,
    OutboundMessage,
)

DISPATCH = UUID("11111111-2222-3333-4444-555555555555")


# --------------------------------------------------------------------- server
class FakeSmtp:
    def __init__(self, certfile: str, keyfile: str) -> None:
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(certfile, keyfile)
        self.advertise_starttls = True
        self.greeting = "220 fake ESMTP"
        self.replies: dict[str, str] = {}
        self.drop_after_data = False
        self.quit_stall = 0.0
        self.valid_password = "pw-ok"
        self.messages: list[bytes] = []
        self.commands: list[str] = []
        self.auth_seen = False
        self.plaintext_auth_seen = False
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        tls = False
        try:
            f = conn.makefile("rwb", buffering=0)
            f.write((self.greeting + "\r\n").encode())
            if not self.greeting.startswith("220"):
                return
            while True:
                line = f.readline()
                if not line:
                    return
                cmd = line.decode().rstrip("\r\n")
                verb = cmd.split(" ", 1)[0].upper()
                self.commands.append(verb)
                if verb == "EHLO":
                    lines = ["250-fake", "250-AUTH PLAIN"]
                    if self.advertise_starttls and not tls:
                        lines.append("250-STARTTLS")
                    lines.append("250 8BITMIME")
                    f.write(("\r\n".join(lines) + "\r\n").encode())
                elif verb == "STARTTLS":
                    f.write(b"220 go ahead\r\n")
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    f = conn.makefile("rwb", buffering=0)
                    tls = True
                elif verb == "AUTH":
                    self.auth_seen = True
                    if not tls:
                        self.plaintext_auth_seen = True
                    import base64

                    parts = cmd.split()
                    cred = base64.b64decode(parts[2]).split(b"\0")[-1].decode()
                    ok = cred == self.valid_password
                    f.write(b"235 ok\r\n" if ok else b"535 5.7.8 bad credentials\r\n")
                elif verb == "MAIL":
                    f.write((self.replies.get("mail", "250 ok") + "\r\n").encode())
                elif verb == "RCPT":
                    f.write((self.replies.get("rcpt", "250 ok") + "\r\n").encode())
                elif verb == "DATA":
                    reply = self.replies.get("data_cmd", "354 go")
                    f.write((reply + "\r\n").encode())
                    if not reply.startswith("354"):
                        continue
                    buf = b""
                    while not buf.endswith(b"\r\n.\r\n"):
                        buf += f.readline()
                    if self.drop_after_data:
                        conn.close()
                        return
                    self.messages.append(buf[:-5])
                    f.write(
                        (self.replies.get("data", "250 2.0.0 queued") + "\r\n").encode()
                    )
                elif verb == "QUIT":
                    if self.quit_stall:
                        import time

                        time.sleep(self.quit_stall)
                    f.write(b"221 bye\r\n")
                    return
                else:
                    f.write(b"500 what\r\n")
        except (OSError, ssl.SSLError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop = True
        self.sock.close()


@pytest.fixture(scope="session")
def certpair(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-smtp")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    d = tmp_path_factory.mktemp("cert")
    c, k = d / "c.pem", d / "k.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(c), str(k)


@pytest.fixture
def server(certpair: tuple[str, str]) -> Iterator[FakeSmtp]:
    s = FakeSmtp(*certpair)
    yield s
    s.close()


@pytest.fixture
def trusting(certpair: tuple[str, str]) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certpair[0])


@pytest.fixture(autouse=True)
def _secrets() -> Iterator[None]:
    secret_sources.install_secret_source(_Src({"smtp_password": "pw-ok"}))
    yield
    secret_sources.clear_secret_source()


class _Src:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data

    def load(self) -> dict[str, str]:
        return self.data


def cfg(server: FakeSmtp, **kw: object) -> SmtpConfig:
    base: dict[str, object] = dict(  # noqa: C408
        host="127.0.0.1",
        port=server.port,
        starttls=True,
        username="",
        from_address="Academy <academy@mail.example.test>",
        production=True,
    )
    base.update(kw)
    return SmtpConfig(**base)  # type: ignore[arg-type]


def provider(
    server: FakeSmtp, ctx: ssl.SSLContext, **kw: object
) -> SmtpDeliveryProvider:
    config = kw.pop("config", None) or cfg(server)
    return SmtpDeliveryProvider(config=lambda: config, ssl_context=ctx, timeout=3, **kw)  # type: ignore[arg-type]


def msg(**kw: object) -> OutboundMessage:
    base: dict[str, object] = dict(  # noqa: C408
        dispatch_id=DISPATCH,
        channel="email",
        address="learner@recipient.example.test",
        body=None,
        category="academy.account",
        subject="Hello",
        text="plain body",
        html="<p>html body</p>",
    )
    base.update(kw)
    return OutboundMessage(**base)  # type: ignore[arg-type]


# ----------------------------------------------------------------- protocol
def test_is_a_delivery_provider_and_manifest() -> None:
    p = SmtpDeliveryProvider(config=lambda: None)  # type: ignore[arg-type,return-value]
    assert isinstance(p, DeliveryProvider)
    assert p.name == "smtp"
    assert p.message_features == {"html", "attachments"}
    assert module.code == "delivery_smtp"


# ------------------------------------------------------------- happy + MIME
def test_accepted_builds_alternative_with_message_id(server, trusting) -> None:
    result = provider(server, trusting).send(
        msg(
            headers={
                "Reply-To": "help@mail.example.test",
                "List-Unsubscribe": "<https://mail.example.test/u>",
            }
        )
    )
    assert result.status == "accepted" and result.response_code == "250"
    parsed = email.message_from_bytes(server.messages[0], policy=policy.default)
    assert parsed["Message-ID"] == f"<{DISPATCH}@mail.example.test>"
    assert parsed["Reply-To"] == "help@mail.example.test"
    assert parsed["List-Unsubscribe"] == "<https://mail.example.test/u>"
    assert parsed["Subject"] == "Hello"
    assert parsed.get_body(("plain",)).get_content().strip() == "plain body"
    assert parsed.get_body(("html",)).get_content().strip() == "<p>html body</p>"
    assert "STARTTLS" in server.commands


def test_html_only_and_text_only(server, trusting) -> None:
    p = provider(server, trusting)
    assert p.send(msg(text=None, html="<b>x</b>")).status == "accepted"
    assert p.send(msg(html=None)).status == "accepted"
    first = email.message_from_bytes(server.messages[0], policy=policy.default)
    assert first.get_content_type() == "text/html"
    assert (
        email.message_from_bytes(
            server.messages[1], policy=policy.default
        ).get_content_type()
        == "text/plain"
    )


def test_attachment_via_injected_resolver(server, trusting) -> None:
    seen: list[AttachmentRef] = []

    def resolver(ref: AttachmentRef) -> bytes:
        seen.append(ref)
        return b"%PDF-1.4 fake"

    ref = AttachmentRef(
        kind="document",
        ref="doc-1",
        filename="cert.pdf",
        content_type="application/pdf",
    )
    result = provider(server, trusting, attachment_resolver=resolver).send(
        msg(attachments=[ref])
    )
    assert result.status == "accepted" and seen == [ref]
    parsed = email.message_from_bytes(server.messages[0], policy=policy.default)
    att = next(parsed.iter_attachments())
    assert att.get_filename() == "cert.pdf" and att.get_content() == b"%PDF-1.4 fake"


def test_attachment_without_resolver_or_failing_resolver_raises(
    server, trusting
) -> None:
    ref = AttachmentRef(
        kind="document",
        ref="doc-1",
        filename="cert.pdf",
        content_type="application/pdf",
    )
    with pytest.raises(SmtpAttachmentError):
        provider(server, trusting).send(msg(attachments=[ref]))

    def boom(_: AttachmentRef) -> bytes:
        raise RuntimeError("secret-path-detail")

    with pytest.raises(SmtpAttachmentError) as exc:
        provider(server, trusting, attachment_resolver=boom).send(
            msg(attachments=[ref])
        )
    assert "secret-path-detail" not in str(exc.value)
    assert server.messages == []


# -------------------------------------------------------------- header list
def test_header_allowlist_enforced_by_adapter(server, trusting) -> None:
    m = msg()
    object.__setattr__(m, "headers", {"X-Evil": "1"})  # bypass kernel validation
    with pytest.raises(SmtpConfigError):
        provider(server, trusting).send(m)
    assert server.messages == []


def test_kernel_refuses_disallowed_header_at_construction() -> None:
    with pytest.raises(ValueError):
        msg(headers={"Bcc": "x@y.example.test"})


# ----------------------------------------------------------------- STARTTLS
def test_production_refuses_plain_configuration(server, trusting) -> None:
    p = provider(server, trusting, config=cfg(server, starttls=False, production=True))
    with pytest.raises(SmtpConfigError):
        p.send(msg())
    assert server.commands == []  # never connected


def test_refuses_to_send_when_starttls_unavailable(server, trusting) -> None:
    server.advertise_starttls = False
    result = provider(server, trusting).send(msg())
    assert (result.status, result.response_code) == (
        "failed",
        CODE_STARTTLS_UNAVAILABLE,
    )
    assert server.messages == [] and "MAIL" not in server.commands


def test_certificate_verification_is_on(server) -> None:
    p = SmtpDeliveryProvider(config=lambda: cfg(server), timeout=3)  # default context
    result = p.send(msg())
    assert (result.status, result.response_code) == (
        "failed",
        CODE_TLS_VERIFICATION_FAILED,
    )
    assert server.messages == []


def test_non_verifying_context_is_refused() -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(SmtpConfigError):
        SmtpDeliveryProvider(config=lambda: None, ssl_context=ctx)  # type: ignore[arg-type,return-value]


def test_non_production_plaintext_allowed_but_never_with_credentials(
    server, trusting
) -> None:
    plain = cfg(server, starttls=False, production=False)
    assert provider(server, trusting, config=plain).send(msg()).status == "accepted"
    with pytest.raises(SmtpConfigError):
        provider(
            server,
            trusting,
            config=cfg(server, starttls=False, production=False, username="u"),
        ).send(msg())
    assert not server.plaintext_auth_seen


# ---------------------------------------------------------------- password
def test_password_from_secret_source_only(server, trusting) -> None:
    p = provider(server, trusting, config=cfg(server, username="mailer"))
    assert p.send(msg()).status == "accepted" and server.auth_seen
    assert not server.plaintext_auth_seen


def test_missing_password_and_bad_password_raise_without_value(
    server, trusting
) -> None:
    secret_sources.install_secret_source(_Src({}))
    with pytest.raises(SmtpConfigError) as missing:
        provider(server, trusting, config=cfg(server, username="mailer")).send(msg())
    assert "pw-ok" not in str(missing.value)
    secret_sources.install_secret_source(_Src({"smtp_password": "wrong-pw-value"}))
    with pytest.raises(SmtpAuthenticationError) as bad:
        provider(server, trusting, config=cfg(server, username="mailer")).send(msg())
    assert "wrong-pw-value" not in str(bad.value)


# ------------------------------------------------------------ classification
@pytest.mark.parametrize(
    ("stage", "code", "text", "expected"),
    [
        ("rcpt", 550, "5.1.1 user unknown", "bounced"),
        ("rcpt", 553, "5.1.3 bad address syntax", "bounced"),
        ("rcpt", 550, "5.1.2 bad domain", "bounced"),
        ("rcpt", 550, "5.1.6 mailbox moved", "bounced"),
        ("rcpt", 550, "5.1.10 null mx", "bounced"),
        ("rcpt", 550, "5.1.7 bad sender mailbox syntax", "operator_fault"),
        ("rcpt", 550, "5.1.8 bad sender system address", "operator_fault"),
        ("rcpt", 550, "5.1.0 other address status", "rejected"),
        ("rcpt", 550, "5.1.4 ambiguous", "rejected"),
        ("rcpt", 550, "5.7.1 relay denied", "rejected"),
        ("rcpt", 554, "5.7.1 policy", "rejected"),
        ("rcpt", 552, "5.2.2 mailbox full", "failed"),
        ("rcpt", 450, "4.2.0 greylisted", "failed"),
        ("rcpt", 451, "4.4.1 try later", "failed"),
        ("rcpt", 554, "5.4.4 unable to route", "failed"),
        ("rcpt", 550, "no enhanced code", "rejected"),
        ("rcpt", 550, "4.1.1 class mismatch is not trusted", "rejected"),
        ("rcpt", 554, "5.3.0 other", "rejected"),
        ("mail", 550, "5.1.8 bad sender", "operator_fault"),
        ("mail", 550, "5.7.1 sender policy", "operator_fault"),
        ("mail", 451, "4.3.0 local error", "failed"),
        ("data", 554, "5.6.0 content refused", "rejected"),
        ("data", 552, "5.3.4 too large", "rejected"),
        ("data", 421, "4.7.0 slow down", "failed"),
        ("session", 554, "5.3.2 no service", "failed"),
        ("session", 421, "4.3.2 shutting down", "failed"),
        ("rcpt", 300, "odd", "failed"),
    ],
)
def test_classification_table(stage: str, code: int, text: str, expected: str) -> None:
    assert classify_reply(stage, code, text) == expected


@pytest.mark.parametrize(
    ("key", "reply", "status"),
    [
        ("rcpt", "550 5.1.1 no such user", "bounced"),
        ("rcpt", "550 5.7.1 denied", "rejected"),
        ("rcpt", "451 4.7.1 later", "failed"),
        ("mail", "550 5.7.1 sender denied", "OPERATOR"),
        ("rcpt", "550 5.1.8 bad sender", "OPERATOR"),
        ("data_cmd", "554 5.3.0 refused", "rejected"),
        ("data", "554 5.7.1 spam", "rejected"),
        ("data", "451 4.3.0 local error", "failed"),
    ],
)
def test_wire_classification(server, trusting, key, reply, status) -> None:
    server.replies[key] = reply
    if status == "OPERATOR":
        with pytest.raises(SmtpOperatorFault):
            provider(server, trusting).send(msg())
        assert server.messages == []
        return
    result = provider(server, trusting).send(msg())
    assert result.status == status and result.response_code == reply[:3]
    if key != "data":  # the fake stores the payload before its final reply
        assert server.messages == []


def test_connection_refused_and_greeting_failure_are_retryable(
    server, trusting
) -> None:
    server.greeting = "421 4.3.2 closing"
    assert provider(server, trusting).send(msg()).status == "failed"
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    p = SmtpDeliveryProvider(
        config=lambda: cfg(server, port=port), ssl_context=trusting, timeout=2
    )
    result = p.send(msg())
    assert (result.status, result.response_code) == ("failed", CODE_CONNECTION_ERROR)


def test_connect_timeout_is_retryable() -> None:
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)  # accepts into backlog, never greets
    try:
        c = SmtpConfig(
            "127.0.0.1", silent.getsockname()[1], True, "", "a@mail.example.test", True
        )
        result = SmtpDeliveryProvider(config=lambda: c, timeout=0.5).send(msg())
        assert (result.status, result.response_code, result.response_body) == (
            "failed",
            CODE_CONNECTION_ERROR,
            "timeout",
        )
    finally:
        silent.close()


def test_reply_lost_after_data_is_unknown_not_failed(server, trusting) -> None:
    server.drop_after_data = True
    with pytest.raises(SmtpOutcomeUnknown):
        provider(server, trusting).send(msg())


# ---------------------------------------------------------- admission hook
def test_admission_refusal_raises_without_connecting(server, trusting) -> None:
    calls: list[UUID] = []

    def gate(m: OutboundMessage) -> str:
        calls.append(m.dispatch_id)
        return "gate closed"

    with pytest.raises(SmtpAdmissionRefused, match="gate closed"):
        provider(server, trusting, admission=gate).send(msg())
    assert calls == [DISPATCH] and server.commands == []


def test_admission_false_and_self_returning_lease_fail_closed(server, trusting) -> None:
    with pytest.raises(SmtpAdmissionRefused):
        provider(server, trusting, admission=lambda m: False).send(msg())

    class SelfLease:
        def __enter__(self) -> SelfLease:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    with pytest.raises(SmtpAdmissionRefused):
        provider(server, trusting, admission=lambda m: SelfLease()).send(msg())
    assert server.commands == []


def test_lease_context_manager_admits_and_releases(server, trusting) -> None:
    events: list[str] = []

    class Lease:
        def __enter__(self) -> bool:
            events.append("enter")
            return True

        def __exit__(self, *a: object) -> None:
            events.append("exit")

    assert (
        provider(server, trusting, admission=lambda m: Lease()).send(msg()).status
        == "accepted"
    )
    assert events == ["enter", "exit"]


def test_admission_exception_propagates_without_connecting(server, trusting) -> None:
    def gate(m: OutboundMessage) -> bool:
        raise RuntimeError("gate down")

    with pytest.raises(RuntimeError):
        provider(server, trusting, admission=gate).send(msg())
    assert server.commands == []


def test_non_email_channel_refused(server, trusting) -> None:
    sms = OutboundMessage(
        dispatch_id=DISPATCH,
        channel="sms",
        address="+15550100",
        body="hi",
        category="x",
    )
    with pytest.raises(SmtpConfigError):
        provider(server, trusting).send(sms)


# ------------------------------------------------------------------- F4/F8
def test_data_reply_uses_the_long_timeout(server, trusting) -> None:
    seen: list[float | None] = []
    import smtplib

    original = smtplib.SMTP.data

    def spy(self, msg):  # type: ignore[no-untyped-def]
        seen.append(self.sock.gettimeout())
        return original(self, msg)

    smtplib.SMTP.data = spy  # type: ignore[method-assign]
    try:
        p = SmtpDeliveryProvider(
            config=lambda: cfg(server),
            ssl_context=trusting,
            timeout=3,
            data_timeout=321,
        )
        assert p.send(msg()).status == "accepted"
    finally:
        smtplib.SMTP.data = original  # type: ignore[method-assign]
    assert seen == [321]


def test_header_set_is_a_literal_of_five_names() -> None:
    assert SmtpDeliveryProvider.message_headers == frozenset(
        {
            "In-Reply-To",
            "List-Unsubscribe",
            "List-Unsubscribe-Post",
            "References",
            "Reply-To",
        }
    )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_old_tls_context_is_refused() -> None:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    with pytest.raises(SmtpConfigError):
        SmtpDeliveryProvider(config=lambda: None, ssl_context=ctx)  # type: ignore[arg-type,return-value]


def test_total_attachment_size_is_capped(server, trusting) -> None:
    refs = [
        AttachmentRef(
            kind="document",
            ref=f"d{i}",
            filename=f"f{i}.pdf",
            content_type="application/pdf",
        )
        for i in range(3)
    ]
    p = provider(
        server,
        trusting,
        attachment_resolver=lambda r: b"x" * 600,
        max_attachment_bytes=1000,
        max_total_attachment_bytes=1500,
    )
    with pytest.raises(SmtpAttachmentError):
        p.send(msg(attachments=refs))
    assert server.messages == []


@pytest.mark.parametrize(
    "bad", [0, -1, True, "5", None, float("nan"), float("inf"), float("-inf")]
)
@pytest.mark.parametrize("name", ["timeout", "data_timeout"])
def test_non_positive_timeouts_are_refused(name: str, bad: object) -> None:
    with pytest.raises(SmtpConfigError):
        SmtpDeliveryProvider(config=lambda: None, **{name: bad})  # type: ignore[arg-type,return-value]


def test_default_budget_stays_below_the_relay_lease() -> None:
    from dotmac_delivery_smtp.provider import (
        DEFAULT_DATA_TIMEOUT_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
    )
    from dotmac_kernel.messaging.relay import RelayPolicy

    steps = 9  # greeting, EHLO, TLS, EHLO, AUTH, MAIL, RCPT, DATA go-ahead, QUIT
    budget = DEFAULT_DATA_TIMEOUT_SECONDS + steps * DEFAULT_TIMEOUT_SECONDS
    assert budget < RelayPolicy().stale_lease_seconds


def test_quit_stall_after_acceptance_runs_under_the_short_timeout(
    server, trusting
) -> None:
    import time

    server.quit_stall = 6.0
    p = SmtpDeliveryProvider(
        config=lambda: cfg(server), ssl_context=trusting, timeout=0.5, data_timeout=30
    )
    started = time.monotonic()
    result = p.send(msg())
    elapsed = time.monotonic() - started
    assert result.status == "accepted"
    assert elapsed < 4.0  # not held for the 30 s DATA budget by a stalled QUIT
