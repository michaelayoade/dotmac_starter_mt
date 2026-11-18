"""`SmtpDeliveryProvider`: the kernel `DeliveryProvider` for channel `email`.

Standard library only (`smtplib`, `ssl`, `email`). Extracted product-first from
Academy's SMTP send (`dotmac_academy_app:app/services/email.py`).

Outcome contract with `dotmac_kernel.delivery_providers.send`:

* A definite verdict is RETURNED as a `ProviderResult` (see `classify`).
* A failure BEFORE the DATA payload was committed (connect, STARTTLS, EHLO,
  MAIL, RCPT, DATA command, a TLS verification failure) is `failed`: nothing
  was accepted, so a retry cannot double-send.
* Once the DATA payload has been sent, a lost reply (timeout, disconnect) is an
  UNKNOWN outcome. SMTP does not honour the dispatch idempotency key, so the
  kernel's rule applies: the adapter RAISES `SmtpOutcomeUnknown` rather than
  return `failed`.
* Operator-side faults (unusable configuration, plaintext in production,
  missing password, authentication rejected, a MAIL-stage or sender-address
  refusal, attachment resolver failure) RAISE; they are not verdicts on the
  recipient and must not become receipts.
* An admission refusal RAISES `SmtpAdmissionRefused` and writes no receipt. S8
  follow-up: it becomes kernel `DeliveryDeferred` (K-4); likewise
  `SmtpOutcomeUnknown` becomes the kernel unknown-outcome type (S8/S10).
* Today the kernel relay retries EVERY raised exception, including the ones
  above that are operator faults. A raise therefore means "no receipt, retried
  by the relay", not "terminal"; a terminal type is a kernel follow-up.
* Never logs or raises with the password, the message body or the recipient.
"""

from __future__ import annotations

import logging
import math
import smtplib
import socket
import ssl
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import parseaddr
from typing import Any

from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_FAILED,
    DELIVERY_REJECTED,
)
from dotmac_kernel.delivery_providers import (
    FEATURE_ATTACHMENTS,
    FEATURE_HTML,
    AttachmentRef,
    OutboundMessage,
    ProviderResult,
)
from dotmac_kernel.secret_sources import get_secret

from dotmac_delivery_smtp.classify import (
    OPERATOR_FAULT,
    STAGE_DATA,
    STAGE_MAIL,
    STAGE_RCPT,
    STAGE_SESSION,
    classify_reply,
)
from dotmac_delivery_smtp.config import SmtpConfig, SmtpConfigError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "smtp"
DEFAULT_TIMEOUT_SECONDS = 15.0
#: `data_timeout` is in force for THREE waits inside smtplib `data()`: the 354
#: go-ahead (the DATA command's reply), the payload send and the final reply.
#: Up to 11 other sequential waits each get `timeout` (TCP connect, greeting,
#: EHLO, STARTTLS reply, TLS handshake, EHLO, AUTH up to 2 replies, MAIL, RCPT,
#: QUIT). The NOMINAL budget is therefore 11 x timeout + 3 x data_timeout: a sum
#: of per-operation budgets, NOT a wall-clock limit (a server trickling bytes
#: resets each one). The kernel relay's `stale_lease_seconds` is 300 and a
#: second worker may reclaim the lease and double-send if one attempt outlasts
#: it. The DEFAULTS satisfy 11 x 15 + 3 x 40 = 285 < 300. A caller passing explicit
#: timeouts must keep inside that budget; nothing here enforces it. Hard
#: enforcement (a construction-time refusal, or K-8 lease renewal / an attempt
#: watchdog) is a follow-up.
DEFAULT_DATA_TIMEOUT_SECONDS = 40.0
#: Per attachment and in total across one message (raised only by a reviewed
#: change; the total keeps a message under common 25-35 MiB relay limits).
DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MIN_TLS = ssl.TLSVersion.TLSv1_2
#: The five headers this adapter writes: a literal, deliberately NOT a copy of
#: the kernel allowlist, so a kernel addition is never written unreviewed.
SUPPORTED_HEADERS: frozenset[str] = frozenset(
    {
        "In-Reply-To",
        "List-Unsubscribe",
        "List-Unsubscribe-Post",
        "References",
        "Reply-To",
    }
)
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
RESPONSE_BODY_LIMIT = 200

CODE_CONNECTION_ERROR = "connection_error"
CODE_STARTTLS_UNAVAILABLE = "starttls_unavailable"
CODE_TLS_VERIFICATION_FAILED = "tls_verification_failed"
CODE_TLS_ERROR = "tls_error"
CODE_NON_ASCII_ADDRESS = "non_ascii_address"

#: Resolves an attachment reference to bytes. The PRODUCT supplies it (tenant
#: scoped, immutable refs, kernel K-8 contract); this package invents no storage.
AttachmentResolver = Callable[[AttachmentRef], bytes]

#: Called before any network connection. Return None/True to admit, False or a
#: short reason string to refuse. It may instead return a context manager (a
#: lease): it is entered before connecting, its entered value is the decision
#: above, and it is exited after the send. An exception it raises propagates
#: (fail closed, nothing is connected).
AdmissionHook = Callable[[OutboundMessage], Any]


class SmtpAdapterError(RuntimeError):
    """Base for adapter faults that are raised rather than returned."""


class SmtpOutcomeUnknown(SmtpAdapterError):
    """The message may or may not have been accepted (reply lost after DATA)."""


class SmtpAuthenticationError(SmtpAdapterError):
    """The server rejected the credentials. An operator fault, not a verdict."""


class SmtpAttachmentError(SmtpAdapterError):
    """An attachment could not be resolved or is unacceptable.

    Not retryable in intent, but the kernel relay retries any raise today, so it
    is retried until a terminal type exists (S8/S10 follow-up)."""


class SmtpOperatorFault(SmtpAdapterError):
    """The server refused OUR side (MAIL FROM, or a sender-address 5.1.7/5.1.8).

    An operator misconfiguration, not a verdict on the recipient: no receipt,
    and above all no suppression of the recipient."""


class SmtpAdmissionRefused(SmtpAdapterError):
    """The admission hook declined; nothing was connected and no receipt is
    written. S8 follow-up: becomes kernel `DeliveryDeferred` (K-4)."""


def _result(status: str, code: str, body: str = "") -> ProviderResult:
    return ProviderResult(
        status=status,
        response_code=code,
        response_body=body[:RESPONSE_BODY_LIMIT] or None,
    )


def _failed(code: str, body: str = "") -> ProviderResult:
    return _result(DELIVERY_FAILED, code, body)


def _reply_text(raw: bytes | str) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return " ".join(text.split())[:RESPONSE_BODY_LIMIT]


class SmtpDeliveryProvider:
    """Sends one `OutboundMessage` over SMTP with required STARTTLS."""

    name = PROVIDER_NAME
    message_features = frozenset({FEATURE_HTML, FEATURE_ATTACHMENTS})
    message_headers = SUPPORTED_HEADERS

    def __init__(
        self,
        *,
        config: Callable[[], SmtpConfig],
        password_secret_name: str = "smtp_password",  # noqa: S107 - a NAME, not a value
        attachment_resolver: AttachmentResolver | None = None,
        admission: AdmissionHook | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        data_timeout: float = DEFAULT_DATA_TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
        max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
        max_total_attachment_bytes: int = DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
    ) -> None:
        for label, value in (("timeout", timeout), ("data_timeout", data_timeout)):
            if (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise SmtpConfigError(f"{label} must be a positive number of seconds")
        self._config = config
        self._password_secret_name = password_secret_name
        self._resolver = attachment_resolver
        self._admission = admission
        self._timeout = timeout
        self._max_attachment_bytes = max_attachment_bytes
        self._max_total_attachment_bytes = max_total_attachment_bytes
        self._data_timeout = data_timeout
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

    def send(self, message: OutboundMessage) -> ProviderResult:
        cfg = self._config()
        cfg.validate()
        password = self._password(cfg)
        if message.channel.strip().lower() != "email":
            raise SmtpConfigError("SMTP delivers the email channel only")
        if not message.address.isascii():
            return _result(DELIVERY_REJECTED, CODE_NON_ASCII_ADDRESS)
        lease = self._admission(message) if self._admission is not None else None
        if isinstance(lease, AbstractContextManager):
            with lease as decision:
                return self._admitted_send(message, cfg, password, decision)
        return self._admitted_send(message, cfg, password, lease)

    def _admitted_send(
        self,
        message: OutboundMessage,
        cfg: SmtpConfig,
        password: str | None,
        decision: object,
    ) -> ProviderResult:
        if decision is not None and decision is not True:
            # Not attempted, and NO receipt: a permanent `failed` row for a
            # deferral would be a lie. Anything but None/True refuses, so a
            # lease whose __enter__ returns the lease itself fails closed.
            reason = decision if isinstance(decision, str) else "not admitted"
            raise SmtpAdmissionRefused(reason[:RESPONSE_BODY_LIMIT])
        built = self._build(message, cfg)
        return self._transmit(cfg, password, message.address, built)

    @staticmethod
    def _default_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = _MIN_TLS
        return context

    def _password(self, cfg: SmtpConfig) -> str | None:
        if not cfg.username:
            return None
        value = get_secret(self._password_secret_name)
        if not value:
            raise SmtpConfigError(
                f"SMTP password secret {self._password_secret_name!r} is not held"
            )
        return value

    def _build(self, message: OutboundMessage, cfg: SmtpConfig) -> bytes:
        msg = EmailMessage(policy=SMTP_POLICY)
        msg["From"] = cfg.from_address
        msg["To"] = message.address
        msg["Subject"] = message.subject or ""
        msg["Message-ID"] = f"<{message.dispatch_id}@{cfg.from_domain}>"
        allowed = {name.lower(): name for name in self.message_headers}
        for name, value in (message.headers or {}).items():
            # The kernel validated these at construction; the adapter enforces
            # its own declared set again rather than trust the caller.
            if name.lower() not in allowed:
                raise SmtpConfigError(f"header {name[:40]!r} is not supported")
            msg[allowed[name.lower()]] = value
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
        self, cfg: SmtpConfig, password: str | None, rcpt: str, payload: bytes
    ) -> ProviderResult:
        smtp: smtplib.SMTP | None = None
        try:
            # Host goes to the constructor so smtplib sets the TLS server_hostname
            # (certificate hostname verification) from it.
            smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=self._timeout)
            return self._session(smtp, cfg, password, rcpt, payload)
        except smtplib.SMTPConnectError as exc:
            return self._reply_failure(STAGE_SESSION, exc.smtp_code, exc.smtp_error)
        except (OSError, smtplib.SMTPException) as exc:
            return self._connection_failure(exc)
        finally:
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
    ) -> ProviderResult:
        try:
            code, text = smtp.ehlo()
            if code != 250:
                return self._reply_failure(STAGE_SESSION, code, text)
            if cfg.starttls:
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
                try:
                    smtp.login(cfg.username, password or "")
                except smtplib.SMTPAuthenticationError as exc:
                    raise SmtpAuthenticationError(
                        f"SMTP server rejected the credentials ({exc.smtp_code})"
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
            return self._reply_failure(STAGE_DATA, exc.smtp_code, exc.smtp_error)
        except (OSError, smtplib.SMTPException) as exc:
            raise SmtpOutcomeUnknown(
                f"reply lost after DATA ({type(exc).__name__}); the message may "
                "have been accepted"
            ) from None
        if code != 250:
            return self._reply_failure(STAGE_DATA, code, text)
        # Best effort: the message is already accepted.
        with suppress(Exception):
            smtp.quit()
        return ProviderResult(
            status=DELIVERY_ACCEPTED,
            response_code=str(code),
            response_body=_reply_text(text) or None,
        )

    @staticmethod
    def _reply_failure(stage: str, code: int, text: bytes | str) -> ProviderResult:
        decoded = _reply_text(text)
        status = classify_reply(stage, code, decoded)
        if status == OPERATOR_FAULT:
            raise SmtpOperatorFault(f"server refused the sender side ({code})")
        return ProviderResult(
            status=status,
            response_code=str(code),
            response_body=decoded or None,
        )

    @staticmethod
    def _connection_failure(exc: BaseException) -> ProviderResult:
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
    "PROVIDER_NAME",
    "AdmissionHook",
    "AttachmentResolver",
    "SUPPORTED_HEADERS",
    "SmtpAdapterError",
    "SmtpAdmissionRefused",
    "SmtpOperatorFault",
    "SmtpAttachmentError",
    "SmtpAuthenticationError",
    "SmtpDeliveryProvider",
    "SmtpOutcomeUnknown",
]
