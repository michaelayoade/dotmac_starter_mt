"""`SmtpTransport`: one SMTP submission, with required STARTTLS.

MOVED from `dotmac_delivery_smtp.provider` (itself extracted product-first from
Academy's SMTP send, `dotmac_academy_app:app/services/email.py`). The send
sequence, MIME builder, STARTTLS handling, timeout budget and reply handling are
unchanged. What changed is only what the connector seam forces:

* no kernel types: the message, the result and the errors are local (a
  connector distribution depends on `dotmac-integration` only);
* the kernel `SecretSource` and the product admission hook / mail-gate lease are
  gone: the password arrives materialised in the dispatch request and the
  Integrator pump owns admission and leasing;
* NEW, before any I/O: the destination is an operator-bound SPI 1.7 network
  target (`require_network_target`), and the TCP connection goes to the pinned
  address that was checked, never to a name resolved afterwards. The TLS
  server name is still the configured host, so the certificate hostname is
  verified against it;
* NEW: `SendResult.payload_sent` says whether the DATA payload left, so the
  handler can make every post-DATA failure AMBIGUOUS.

Outcome contract with `plugin.SmtpDeliveryHandler`:

* A definite verdict is RETURNED as a `SendResult` (see `classify`).
* A failure BEFORE the DATA payload was committed (connect, STARTTLS, EHLO,
  MAIL, RCPT, DATA command, a TLS verification failure) is `failed` with
  `payload_sent=False`: nothing was accepted, so a retry cannot double-send.
* Once the DATA payload has been sent, a lost reply (timeout, disconnect) is an
  UNKNOWN outcome: the transport RAISES `SmtpOutcomeUnknown`. SMTP does not
  honour an idempotency key. An explicit non-250 final reply is returned with
  `payload_sent=True`; the handler treats that as ambiguous too and never
  retries it.
* Operator-side faults (unusable configuration, plaintext, missing password,
  authentication rejected, a MAIL-stage or sender-address refusal, attachment
  resolver failure, an endpoint outside the bound targets) RAISE; they are not
  verdicts on the recipient.
* Never logs or raises with the password, the message body or the recipient.
"""

from __future__ import annotations

import math
import smtplib
import socket
import ssl
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import parseaddr

from dotmac_integration.spi import NetworkTarget, require_network_target

from dotmac_connector_smtp.classify import (
    OPERATOR_FAULT,
    STAGE_DATA,
    STAGE_MAIL,
    STAGE_RCPT,
    STAGE_SESSION,
    classify_reply,
)
from dotmac_connector_smtp.config import SmtpConfig, SmtpConfigError

DELIVERY_ACCEPTED = "accepted"
DELIVERY_FAILED = "failed"
DELIVERY_REJECTED = "rejected"

PROVIDER_NAME = "smtp"
#: The only protocol/port family the manifest ceiling declares for this
#: transport; the bound target must say it explicitly.
TARGET_PROTOCOL = "tcp"
DEFAULT_TIMEOUT_SECONDS = 15.0
#: `data_timeout` is in force for THREE waits inside smtplib `data()`: the 354
#: go-ahead (the DATA command's reply), the payload send and the final reply.
#: Up to 11 other sequential waits each get `timeout` (TCP connect, greeting,
#: EHLO, STARTTLS reply, TLS handshake, EHLO, AUTH up to 2 replies, MAIL, RCPT,
#: QUIT). The NOMINAL budget is therefore 11 x timeout + 3 x data_timeout: a sum
#: of per-operation budgets, NOT a wall-clock limit (a server trickling bytes
#: resets each one). Whatever leases one attempt (the kernel relay's
#: `stale_lease_seconds` is 300; the Integrator pump's lease must be longer than
#: this budget, work package B5) may hand it to a second worker that double-sends
#: if one attempt outlasts it. The DEFAULTS satisfy 11 x 15 + 3 x 40 = 285 < 300.
#: A caller passing explicit timeouts must keep inside that budget; nothing here
#: enforces it. Hard enforcement (a construction-time refusal, or lease renewal /
#: an attempt watchdog) is a follow-up.
DEFAULT_DATA_TIMEOUT_SECONDS = 40.0
#: HARD wall-clock cap on one whole send, from the first connect to QUIT. The
#: per-operation timeouts above are not a wall-clock limit (a server trickling
#: one byte per interval resets each of them), so a watchdog shuts the socket
#: down at this deadline: before DATA that surfaces as a retryable connection
#: failure, after DATA as `SmtpOutcomeUnknown`. It MUST stay below the lease that
#: guards one attempt (300 s): **the Integrator pump must assert, at boot, that
#: its lease is greater than `SEND_DEADLINE_SECONDS`.** The nominal budget of
#: 11 x timeout + 3 x data_timeout (285 s) is NOT the number to check against.
SEND_DEADLINE_SECONDS = 240.0
#: Per attachment and in total across one message (raised only by a reviewed
#: change; the total keeps a message under common 25-35 MiB relay limits).
DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MIN_TLS = ssl.TLSVersion.TLSv1_2
#: The five headers this adapter writes: a literal, deliberately NOT a copy of
#: any caller-side allowlist, so an addition is never written unreviewed.
SUPPORTED_HEADERS: frozenset[str] = frozenset(
    {
        "In-Reply-To",
        "List-Unsubscribe",
        "List-Unsubscribe-Post",
        "References",
        "Reply-To",
    }
)
#: The order they are written in. The sending application's canonical order
#: (alphabetical), applied here because a JSON round trip through the outbox does
#: not preserve key order and the MIME bytes must not depend on it.
_HEADER_ORDER: tuple[str, ...] = (
    "In-Reply-To",
    "List-Unsubscribe",
    "List-Unsubscribe-Post",
    "References",
    "Reply-To",
)
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
RESPONSE_BODY_LIMIT = 200

CODE_CONNECTION_ERROR = "connection_error"
CODE_STARTTLS_UNAVAILABLE = "starttls_unavailable"
CODE_TLS_VERIFICATION_FAILED = "tls_verification_failed"
CODE_TLS_ERROR = "tls_error"
CODE_NON_ASCII_ADDRESS = "non_ascii_address"


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """A REFERENCE to an attachment, never its bytes (the shape the kernel's
    `AttachmentRef` has, restated because a connector imports no kernel)."""

    kind: str
    ref: str
    filename: str
    content_type: str


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What one send is asked to carry: already rendered, already addressed.

    `dispatch_id` forms the `Message-ID`; the handler validates its charset
    before it gets here."""

    dispatch_id: str
    address: str
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    headers: Mapping[str, str] | None = None
    attachments: Sequence[AttachmentRef] = ()


@dataclass(frozen=True, slots=True)
class SendResult:
    """A definite verdict. `payload_sent` is True once the DATA payload left
    the process: from then on the message may be accepted."""

    status: str
    response_code: str | None
    response_body: str | None
    payload_sent: bool = False


#: Resolves an attachment reference to bytes. The ASSEMBLY supplies it (tenant
#: scoped, immutable refs); this package invents no storage. The shipped plugin
#: binds none, so a message with attachments is refused.
AttachmentResolver = Callable[[AttachmentRef], bytes]


class SmtpAdapterError(RuntimeError):
    """Base for adapter faults that are raised rather than returned."""


class SmtpOutcomeUnknown(SmtpAdapterError):
    """The message may or may not have been accepted (reply lost after DATA).

    The handler reports it as RECONCILIATION_REQUIRED, which the engine never
    retries (SMTP ignores the idempotency key, so a retry can double-send)."""


class SmtpAuthenticationError(SmtpAdapterError):
    """The server rejected the credentials. An operator fault, not a verdict."""


class SmtpAttachmentError(SmtpAdapterError):
    """An attachment could not be resolved or is unacceptable.

    Nothing was sent. The handler reports it as TERMINAL: the message as
    submitted cannot succeed on a retry."""


class SmtpOperatorFault(SmtpAdapterError):
    """The server refused OUR side (MAIL FROM, or a sender-address 5.1.7/5.1.8).

    An operator misconfiguration, not a verdict on the recipient: no receipt,
    and above all no suppression of the recipient."""


class SmtpPayloadError(SmtpAdapterError):
    """The message cannot be built (for example a header with a line break).
    Raised before any connection; the message cannot succeed as submitted."""


class SmtpTargetRefused(SmtpAdapterError):
    """The endpoint is outside the installation's bound network targets (raised
    by `require_network_target` as `NetworkTargetError`; re-typed by the handler
    so no address reaches an error detail). Nothing was connected."""


def _result(status: str, code: str, body: str = "") -> SendResult:
    return SendResult(
        status=status,
        response_code=code,
        response_body=body[:RESPONSE_BODY_LIMIT] or None,
    )


def _failed(code: str, body: str = "") -> SendResult:
    return _result(DELIVERY_FAILED, code, body)


def _reply_text(raw: bytes | str) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return " ".join(text.split())[:RESPONSE_BODY_LIMIT]


class _Watchdog:
    """Shuts the live socket down once the send deadline passes.

    A thread, not a signal: it works off the main thread, and `shutdown` wakes a
    blocked `recv`/`send` in the sending thread. After the deadline it keeps
    retrying every half second, so a socket replaced by the TLS upgrade is
    caught too."""

    def __init__(self, deadline: float) -> None:
        self._deadline = deadline
        self._done = threading.Event()
        self._socks: list[socket.socket] = []
        self.smtp: smtplib.SMTP | None = None
        self.fired = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def expired(self) -> bool:
        return self.fired

    def start(self) -> None:
        self._thread.start()

    def track(self, sock: socket.socket) -> None:
        self._socks.append(sock)

    def stop(self) -> None:
        self._done.set()

    def _run(self) -> None:
        if self._done.wait(self._deadline):
            return
        self.fired = True
        while True:
            candidates = list(self._socks)
            if self.smtp is not None and self.smtp.sock is not None:
                candidates.append(self.smtp.sock)
            for sock in candidates:
                with suppress(Exception):
                    # The RAW socket's shutdown, never `SSLSocket.shutdown`: the
                    # TLS object's own method is not what stops the bytes, and
                    # nothing may be written to the wire in plaintext.
                    socket.socket.shutdown(sock, socket.SHUT_RDWR)
            if self._done.wait(0.5):
                return


class _PinnedSMTP(smtplib.SMTP):
    """`smtplib.SMTP` that connects to an ALREADY CHECKED address.

    The constructor receives the configured host, so `smtplib` keeps it as the
    TLS `server_name` (certificate hostname verification), but the socket goes to
    the pinned address that passed `require_network_target`. No name is resolved
    after the check, so DNS cannot redirect a checked destination."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned: str,
        timeout: float,
        local_hostname: str,
        watchdog: _Watchdog,
    ) -> None:
        self._pinned = pinned
        self._watchdog = watchdog
        # `local_hostname` is always given: smtplib would otherwise call
        # `socket.getfqdn()` (a resolver call outside the budget) and EHLO would
        # disclose this host's own name.
        super().__init__(host, port, local_hostname=local_hostname, timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        sock = socket.create_connection(
            (self._pinned, port), timeout, self.source_address
        )
        self._watchdog.track(sock)
        return sock


class SmtpTransport:
    """Sends one `OutboundMessage` over SMTP with required STARTTLS, to a bound
    network target only."""

    name = PROVIDER_NAME
    message_headers = SUPPORTED_HEADERS

    def __init__(
        self,
        *,
        attachment_resolver: AttachmentResolver | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        data_timeout: float = DEFAULT_DATA_TIMEOUT_SECONDS,
        deadline: float = SEND_DEADLINE_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
        max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
        max_total_attachment_bytes: int = DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
    ) -> None:
        for label, value in (
            ("timeout", timeout),
            ("data_timeout", data_timeout),
            ("deadline", deadline),
        ):
            if (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise SmtpConfigError(f"{label} must be a positive number of seconds")
        self._resolver = attachment_resolver
        self._timeout = timeout
        self._max_attachment_bytes = max_attachment_bytes
        self._max_total_attachment_bytes = max_total_attachment_bytes
        self._data_timeout = data_timeout
        if deadline > SEND_DEADLINE_SECONDS:
            raise SmtpConfigError(
                "deadline must not exceed SEND_DEADLINE_SECONDS: it is what keeps "
                "one send inside the attempt lease"
            )
        self._deadline = deadline
        if ssl_context is not None and (
            ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not ssl_context.check_hostname
            or ssl_context.minimum_version < _MIN_TLS
        ):
            raise SmtpConfigError(
                "ssl_context must verify certificates and hostnames and "
                "require TLS 1.2 or newer"
            )
        self._ssl_context = ssl_context

    def send(
        self,
        message: OutboundMessage,
        *,
        config: SmtpConfig,
        address: str,
        password: str | None,
        targets: Iterable[NetworkTarget],
    ) -> SendResult:
        """`address` is the pinned IP literal of `config.host`; `password` is the
        materialised secret (None when `config.username` is empty)."""
        config.validate()
        if not config.starttls:
            # STARTTLS is not optional in this connector, whatever the caller
            # built: there is no plaintext path to reach.
            raise SmtpConfigError("STARTTLS is required")
        if config.username and not password:
            raise SmtpConfigError("SMTP password is not held")
        if not message.address.isascii():
            return _result(DELIVERY_REJECTED, CODE_NON_ASCII_ADDRESS)
        # BEFORE any I/O, including name resolution (there is none) and the
        # MIME build's attachment resolver: the destination must be a target the
        # operator bound. A refusal propagates; there is no allow-when-unbound.
        try:
            require_network_target(
                targets, address=address, port=config.port, protocol=TARGET_PROTOCOL
            )
        except ValueError:
            raise SmtpTargetRefused("endpoint is outside the bound targets") from None
        try:
            built = self._build(message, config)
        except ValueError:
            # ONLY the MIME build is covered: nothing has been sent yet.
            raise SmtpPayloadError("the message cannot be built") from None
        return self._transmit(config, address, password, message.address, built)

    @staticmethod
    def _default_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = _MIN_TLS
        return context

    def _build(self, message: OutboundMessage, cfg: SmtpConfig) -> bytes:
        msg = EmailMessage(policy=SMTP_POLICY)
        msg["From"] = cfg.from_address
        msg["To"] = message.address
        msg["Subject"] = message.subject or ""
        msg["Message-ID"] = f"<{message.dispatch_id}@{cfg.from_domain}>"
        allowed = {name.lower(): name for name in self.message_headers}
        chosen: dict[str, str] = {}
        for name, value in (message.headers or {}).items():
            # The caller validated these; the adapter enforces its own declared
            # set again rather than trust it.
            if name.lower() not in allowed:
                raise SmtpConfigError(f"header {name[:40]!r} is not supported")
            chosen[allowed[name.lower()]] = value
        for name in _HEADER_ORDER:
            if name in chosen:
                msg[name] = chosen[name]
        text, html = message.text, message.html
        if text is not None and html is not None:
            msg.set_content(text)
            msg.add_alternative(html, subtype="html")
        elif html is not None:
            msg.set_content(html, subtype="html")
        else:
            msg.set_content(text or "")
        total = 0
        for ref in message.attachments:
            data = self._resolve(ref)
            total += len(data)
            if total > self._max_total_attachment_bytes:
                raise SmtpAttachmentError("attachments exceed the total size limit")
            maintype, _, subtype = ref.content_type.partition("/")
            msg.add_attachment(
                data, maintype=maintype, subtype=subtype, filename=ref.filename
            )
        return msg.as_bytes()

    def _resolve(self, ref: AttachmentRef) -> bytes:
        if self._resolver is None:
            raise SmtpAttachmentError("no attachment resolver is configured")
        try:
            data = self._resolver(ref)
        except Exception as exc:
            raise SmtpAttachmentError(
                f"attachment resolver failed: {type(exc).__name__}"
            ) from None
        if not isinstance(data, bytes | bytearray) or not data:
            raise SmtpAttachmentError("attachment resolver returned no bytes")
        if len(data) > self._max_attachment_bytes:
            raise SmtpAttachmentError("attachment exceeds the size limit")
        return bytes(data)

    def _transmit(
        self,
        cfg: SmtpConfig,
        address: str,
        password: str | None,
        rcpt: str,
        payload: bytes,
    ) -> SendResult:
        smtp: smtplib.SMTP | None = None
        watchdog = _Watchdog(self._deadline)
        watchdog.start()
        try:
            # Host goes to the constructor so smtplib sets the TLS server_hostname
            # (certificate hostname verification) from it; the socket goes to the
            # pinned, already-checked address.
            smtp = _PinnedSMTP(
                cfg.host,
                cfg.port,
                pinned=address,
                timeout=self._timeout,
                local_hostname=cfg.from_domain,
                watchdog=watchdog,
            )
            watchdog.smtp = smtp
            return self._session(smtp, cfg, password, rcpt, payload)
        except smtplib.SMTPConnectError as exc:
            return self._reply_failure(STAGE_SESSION, exc.smtp_code, exc.smtp_error)
        except (OSError, smtplib.SMTPException) as exc:
            return self._connection_failure(exc)
        finally:
            watchdog.stop()
            if smtp is not None:
                # Best effort: the outcome is already decided.
                with suppress(Exception):
                    smtp.close()

    def _session(
        self,
        smtp: smtplib.SMTP,
        cfg: SmtpConfig,
        password: str | None,
        rcpt: str,
        payload: bytes,
    ) -> SendResult:
        try:
            code, text = smtp.ehlo()
            if code != 250:
                return self._reply_failure(STAGE_SESSION, code, text)
            if not smtp.has_extn("starttls"):
                return _failed(CODE_STARTTLS_UNAVAILABLE)
            context = self._ssl_context or self._default_context()
            code, text = smtp.starttls(context=context)
            if code != 220:
                return self._reply_failure(STAGE_SESSION, code, text)
            code, text = smtp.ehlo()
            if code != 250:
                return self._reply_failure(STAGE_SESSION, code, text)
            if cfg.username:
                if self._expired(smtp):
                    # Never present credentials once the deadline has passed.
                    return _failed(CODE_CONNECTION_ERROR, "timeout")
                try:
                    smtp.login(cfg.username, password or "")
                except smtplib.SMTPAuthenticationError as exc:
                    raise SmtpAuthenticationError(
                        f"SMTP server rejected the credentials ({exc.smtp_code})"
                    ) from None
                except UnicodeError:
                    # A non-ASCII username or password cannot be sent: a
                    # configuration fault, never a payload fault.
                    raise SmtpConfigError(
                        "SMTP credentials cannot be encoded"
                    ) from None
            code, text = smtp.mail(parseaddr(cfg.from_address)[1])
            if code != 250:
                return self._reply_failure(STAGE_MAIL, code, text)
            code, text = smtp.rcpt(rcpt)
            if code not in (250, 251):
                return self._reply_failure(STAGE_RCPT, code, text)
        except ssl.SSLCertVerificationError:
            return _failed(CODE_TLS_VERIFICATION_FAILED)
        except ssl.SSLError:
            return _failed(CODE_TLS_ERROR)
        except (OSError, smtplib.SMTPException) as exc:
            return self._connection_failure(exc)
        if self._expired(smtp):
            # Checked immediately before DATA: after the deadline the payload
            # must not leave at all (a retry then cannot double-send).
            return _failed(CODE_CONNECTION_ERROR, "timeout")
        # The payload is about to leave: from here a lost reply is UNKNOWN.
        try:
            if smtp.sock is not None:
                smtp.sock.settimeout(self._data_timeout)
            try:
                code, text = smtp.data(payload)
            finally:
                # The long budget covers ONLY the final DATA reply; everything
                # after it (QUIT) runs under the short per-operation timeout.
                if smtp.sock is not None:
                    smtp.sock.settimeout(self._timeout)
        except smtplib.SMTPDataError as exc:
            # smtplib raises this ONLY for a refused DATA command (not 354):
            # the payload never left, so a retry cannot double-send.
            return self._reply_failure(STAGE_DATA, exc.smtp_code, exc.smtp_error)
        except Exception as exc:
            # ANY failure once the payload may have left is unknown, not just
            # the socket and SMTP errors smtplib is documented to raise.
            raise SmtpOutcomeUnknown(
                f"reply lost after DATA ({type(exc).__name__}); the message may "
                "have been accepted"
            ) from None
        if code != 250:
            return self._reply_failure(STAGE_DATA, code, text, payload_sent=True)
        # Best effort: the message is already accepted.
        with suppress(Exception):
            smtp.quit()
        return SendResult(
            status=DELIVERY_ACCEPTED,
            response_code=str(code),
            response_body=_reply_text(text) or None,
            payload_sent=True,
        )

    @staticmethod
    def _expired(smtp: smtplib.SMTP) -> bool:
        watchdog = getattr(smtp, "_watchdog", None)
        return bool(watchdog is not None and watchdog.expired)

    @staticmethod
    def _reply_failure(
        stage: str, code: int, text: bytes | str, *, payload_sent: bool = False
    ) -> SendResult:
        decoded = _reply_text(text)
        status = classify_reply(stage, code, decoded)
        if status == OPERATOR_FAULT:
            raise SmtpOperatorFault(f"server refused the sender side ({code})")
        return SendResult(
            status=status,
            response_code=str(code),
            response_body=decoded or None,
            payload_sent=payload_sent,
        )

    @staticmethod
    def _connection_failure(exc: BaseException) -> SendResult:
        # smtplib re-raises a socket timeout as SMTPServerDisconnected, so the
        # original is read from the exception context.
        timed_out = isinstance(exc, socket.timeout | TimeoutError) or isinstance(
            exc.__context__, socket.timeout | TimeoutError
        )
        reason = "timeout" if timed_out else type(exc).__name__
        return _failed(CODE_CONNECTION_ERROR, reason)


__all__ = [
    "CODE_CONNECTION_ERROR",
    "CODE_NON_ASCII_ADDRESS",
    "CODE_STARTTLS_UNAVAILABLE",
    "CODE_TLS_ERROR",
    "CODE_TLS_VERIFICATION_FAILED",
    "DEFAULT_DATA_TIMEOUT_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "SEND_DEADLINE_SECONDS",
    "PROVIDER_NAME",
    "SUPPORTED_HEADERS",
    "TARGET_PROTOCOL",
    "AttachmentRef",
    "AttachmentResolver",
    "OutboundMessage",
    "SendResult",
    "SmtpAdapterError",
    "SmtpAttachmentError",
    "SmtpAuthenticationError",
    "SmtpOperatorFault",
    "SmtpOutcomeUnknown",
    "SmtpPayloadError",
    "SmtpTargetRefused",
    "SmtpTransport",
]
