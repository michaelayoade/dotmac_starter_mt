"""Manifest, handler and plugin for the SMTP delivery connector (SPI >=1.7).

One capability, `email.send.v1`, in DELIVERY mode: the Integrator hands the
handler one already-rendered message and the connector submits it to the one
operator-bound SMTP destination, then reports one classified `Outcome`.

Egress declares NO provider hosts (deny-all): the destination is a fleet-style
target, bound per installation by the operator as a CIDR, and the manifest only
declares the ceiling (TCP 587). `require_network_target` refuses any endpoint
outside the bound targets before a socket exists.

## Outcome mapping

* 250 after DATA: `SUCCEEDED`.
* Any failure after the DATA payload was sent (a lost reply, or an explicit
  non-250 final reply): `RECONCILIATION_REQUIRED`. AMBIGUOUS, never retried.
* A recipient bounce (RCPT 5.1.1/2/3/6/10) or another permanent refusal before
  DATA: `TERMINAL`.
* Connection, TLS, greeting, 4xx or soft 5.2/5.4 before DATA: `RETRYABLE`
  (nothing was accepted).
* Unusable configuration, an endpoint outside the bound targets, a missing or
  rejected credential, a MAIL-stage refusal: `RETRYABLE` (operator-fixable;
  nothing was sent; bounded by the engine's attempt cap).
* A malformed payload, or an attachment the connector cannot resolve:
  `TERMINAL`.
* Any exception the handler did not anticipate: raised, and the engine records
  `RECONCILIATION_REQUIRED`.

No outcome carries a reply body, an address, a message part or the password;
`error_detail` is a fixed code. The SMTP reply code travels in `error_code`,
never in the HTTP-typed `provider_status_code`, which the engine acts on.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import (
    CapabilityDeclaration,
    CapabilityHandler,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    DispatchRequest,
    EgressDeclaration,
    FleetPortRange,
    FleetTargetDeclaration,
    NetworkTargetError,
    SecretBindingDeclaration,
    SpiRange,
    bound_network_targets,
)

from dotmac_connector_smtp.config import SmtpConfigError
from dotmac_connector_smtp.connection import (
    CONFIG_SCHEMA,
    SECRET_PASSWORD,
    endpoint_config,
)
from dotmac_connector_smtp.provider import (
    SUPPORTED_HEADERS,
    AttachmentRef,
    AttachmentResolver,
    OutboundMessage,
    SendResult,
    SmtpAttachmentError,
    SmtpAuthenticationError,
    SmtpOperatorFault,
    SmtpOutcomeUnknown,
    SmtpPayloadError,
    SmtpTargetRefused,
    SmtpTransport,
)

CONNECTOR_KEY: Final = "smtp"
SEND_CAPABILITY: Final = "email.send.v1"

_DISPATCH_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ADDRESS_RE: Final = re.compile(
    r"[^\s<>\"@,;:()\[\]\\]{1,64}@[^\s<>\"@,;:()\[\]\\]{1,255}"
)
_MAX_PART_BYTES: Final = 1024 * 1024
_MAX_ATTACHMENTS: Final = 10

MANIFEST = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version="0.1.0a1",
    spi_range=SpiRange.parse(">=1.7,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=SEND_CAPABILITY,
            config_schema=CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.DELIVERY}),
            # No domain has published an `email.send.v1` contract yet, so there
            # is nothing to claim (ADR-0024 10.3); the claim is added with it.
            claims_contract_digest=None,
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=SECRET_PASSWORD,
            required=False,
            description=(
                "SMTP login password. Required whenever the configuration names "
                "a username; never sent unless STARTTLS has verified the server."
            ),
        ),
    ),
    egress=EgressDeclaration(hosts=()),
    fleet_targets=FleetTargetDeclaration(
        port_ranges=(FleetPortRange("tcp", 587, 587),)
    ),
)


class PayloadError(ValueError):
    """The dispatch payload cannot be turned into a message."""


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_PART_BYTES:
        raise PayloadError(key)
    return value


def message_from_payload(payload: Mapping[str, object]) -> OutboundMessage:
    """Parse the command payload, or raise `PayloadError` naming the key only."""
    dispatch_id = payload.get("dispatch_id")
    if not isinstance(dispatch_id, str) or not _DISPATCH_ID_RE.fullmatch(dispatch_id):
        raise PayloadError("dispatch_id")
    address = payload.get("address")
    if not isinstance(address, str) or not _ADDRESS_RE.fullmatch(address):
        raise PayloadError("address")
    subject = _optional_text(payload, "subject")
    text, html = _optional_text(payload, "text"), _optional_text(payload, "html")
    if text is None and html is None:
        raise PayloadError("text")
    raw_headers = payload.get("headers") or {}
    if not isinstance(raw_headers, Mapping):
        raise PayloadError("headers")
    headers: dict[str, str] = {}
    for name, value in raw_headers.items():
        if name not in SUPPORTED_HEADERS or not isinstance(value, str):
            raise PayloadError("headers")
        headers[name] = value
    raw_attachments = payload.get("attachments") or []
    if not isinstance(raw_attachments, list) or len(raw_attachments) > _MAX_ATTACHMENTS:
        raise PayloadError("attachments")
    attachments: list[AttachmentRef] = []
    for item in raw_attachments:
        if not isinstance(item, Mapping) or set(item) != {
            "kind",
            "ref",
            "filename",
            "content_type",
        }:
            raise PayloadError("attachments")
        values = [item[k] for k in ("kind", "ref", "filename", "content_type")]
        if not all(isinstance(v, str) and v for v in values):
            raise PayloadError("attachments")
        attachments.append(AttachmentRef(*values))
    return OutboundMessage(
        dispatch_id=dispatch_id,
        address=address,
        subject=subject,
        text=text,
        html=html,
        headers=headers,
        attachments=tuple(attachments),
    )


#: Minimum delay before a rejected credential is tried again. One bad password
#: otherwise costs every queued message up to the engine's attempt cap in AUTH
#: attempts, which can lock the account out (legacy senders share it).
AUTH_REJECTED_RETRY_AFTER_SECONDS: Final = 900


def _outcome(
    status: OutcomeStatus,
    code: str,
    *,
    retry_after: int | None = None,
) -> Outcome:
    # `provider_status_code` is deliberately NEVER set: the SPI defines it as an
    # HTTP status, and the engine acts on it (500/502/503/504 become retryable,
    # a 503 pauses the whole installation). An SMTP reply code is not that, so it
    # travels in `error_code`, the connector's own vocabulary.
    return Outcome(
        status=status,
        error_code=code,
        error_detail=code,
        retry_after_seconds=retry_after,
    )


def _reply_code(result: SendResult) -> int | None:
    value = result.response_code
    if value is not None and value.isdigit() and 100 <= int(value) <= 599:
        return int(value)
    return None


def outcome_for(result: SendResult, *, message_id: str) -> Outcome:
    """Map a definite `SendResult` to an `Outcome` (the ambiguity rule lives here)."""
    reply = _reply_code(result)
    suffix = f"_{reply}" if reply is not None else ""
    if result.status == "accepted":
        return Outcome(status=OutcomeStatus.SUCCEEDED, provider_reference=message_id)
    if result.payload_sent:
        # The payload left; whatever came back, the message may be on its way.
        # Never retried, never dead-lettered silently: reconcile.
        return _outcome(
            OutcomeStatus.RECONCILIATION_REQUIRED,
            f"smtp_ambiguous_after_data{suffix}",
        )
    if result.status == "bounced":
        return _outcome(OutcomeStatus.TERMINAL, f"smtp_bounced{suffix}")
    if result.status == "rejected":
        return _outcome(OutcomeStatus.TERMINAL, f"smtp_rejected{suffix}")
    if reply is not None:
        return _outcome(OutcomeStatus.RETRYABLE, f"smtp_reply{suffix}")
    safe = re.sub(r"[^a-z0-9_]", "", (result.response_code or "failed").lower())
    return _outcome(OutcomeStatus.RETRYABLE, f"smtp_{safe or 'failed'}")


class SmtpDeliveryHandler:
    """MODE: DELIVERY handler for `email.send.v1`."""

    def __init__(self, transport: SmtpTransport) -> None:
        self._transport = transport

    def __call__(self, request: DispatchRequest) -> Outcome:
        try:
            message = message_from_payload(request.payload)
        except PayloadError:
            return _outcome(OutcomeStatus.TERMINAL, "invalid_payload")
        try:
            endpoint = endpoint_config(request.config)
            targets = bound_network_targets(request.config)
        except SmtpConfigError:
            return _outcome(OutcomeStatus.RETRYABLE, "config_invalid")
        except NetworkTargetError:
            return _outcome(OutcomeStatus.RETRYABLE, "network_target_refused")
        secret = request.secrets.get(SECRET_PASSWORD)
        password = secret if isinstance(secret, str) and secret else None
        try:
            result = self._transport.send(
                message,
                config=endpoint.config,
                address=endpoint.address,
                password=password,
                targets=targets,
            )
        except SmtpTargetRefused:
            return _outcome(OutcomeStatus.RETRYABLE, "network_target_refused")
        except SmtpOutcomeUnknown:
            return _outcome(
                OutcomeStatus.RECONCILIATION_REQUIRED, "smtp_ambiguous_after_data"
            )
        except SmtpAttachmentError:
            return _outcome(OutcomeStatus.TERMINAL, "attachment_refused")
        except SmtpAuthenticationError:
            return _outcome(
                OutcomeStatus.RETRYABLE,
                "smtp_auth_rejected",
                retry_after=AUTH_REJECTED_RETRY_AFTER_SECONDS,
            )
        except SmtpOperatorFault:
            return _outcome(OutcomeStatus.RETRYABLE, "smtp_operator_fault")
        except SmtpConfigError:
            return _outcome(OutcomeStatus.RETRYABLE, "config_invalid")
        except SmtpPayloadError:
            return _outcome(OutcomeStatus.TERMINAL, "invalid_payload")
        cfg = endpoint.config
        return outcome_for(
            result, message_id=f"<{message.dispatch_id}@{cfg.from_domain}>"
        )


class SmtpConnector:
    """Process-global plugin; the transport is an assembly-bindable seam."""

    def __init__(
        self,
        *,
        transport: SmtpTransport | None = None,
        attachment_resolver: AttachmentResolver | None = None,
    ) -> None:
        self._transport = transport or SmtpTransport(
            attachment_resolver=attachment_resolver
        )

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.DELIVERY})

    def handler_for(self, capability_id: str) -> CapabilityHandler:
        self.manifest.require_declares(capability_id)
        return SmtpDeliveryHandler(self._transport)

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        """Shape and credential-presence checks only: NO I/O. A live check needs
        the bound targets, which validation does not receive; the operator
        test-send is the connectivity proof."""
        try:
            endpoint = endpoint_config(config)
        except SmtpConfigError:
            return (Diagnostic(ok=False, code="config_invalid"),)
        if endpoint.config.username and not (
            isinstance(secrets.get(SECRET_PASSWORD), str)
            and secrets.get(SECRET_PASSWORD)
        ):
            return (Diagnostic(ok=False, code="password_missing"),)
        return (Diagnostic(ok=True, code="config_valid"),)


PLUGIN = SmtpConnector()

__all__ = [
    "CONNECTOR_KEY",
    "MANIFEST",
    "PLUGIN",
    "SEND_CAPABILITY",
    "PayloadError",
    "SmtpConnector",
    "SmtpDeliveryHandler",
    "message_from_payload",
    "outcome_for",
]
