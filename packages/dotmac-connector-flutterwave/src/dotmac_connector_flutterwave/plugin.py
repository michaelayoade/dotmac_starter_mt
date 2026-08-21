"""Stateless authenticated ingress translation for Flutterwave webhooks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final

import httpx
from dotmac_integration.spi import (
    Acknowledgement,
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    EgressDeclaration,
    InboundDisposition,
    InboundEvent,
    IngressHandler,
    IngressRequest,
    PollHandler,
    SecretBindingDeclaration,
    SpiRange,
    VerificationResult,
)

from dotmac_connector_flutterwave.polling import (
    IDENTITY_HOST,
    LIVE_HOST,
    SANDBOX_HOST,
    FlutterwavePollHandler,
)

CONNECTOR_KEY: Final = "flutterwave"
CAPABILITY_ID: Final = "payments.settlement.observation.v1"
VERSION: Final = "0.1.0a1"
CURRENT_VERSION: Final = "0.1.0a2"

HMAC_SHA256: Final = "hmac_sha256"
SIGNATURE_HEADER: Final = "flutterwave-signature"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
API_CLIENT_ID: Final = "api_client_id"
API_CLIENT_SECRET: Final = "api_client_secret"

ACKNOWLEDGEMENT: Final = Acknowledgement(body=b"")

CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "environment": {"type": "string", "enum": ["sandbox", "live"]},
        "reconcile_from": {"type": "string", "format": "date-time"},
        "page_size": {"type": "integer", "minimum": 10, "maximum": 50},
    },
}
LEGACY_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}

LEGACY_MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=LEGACY_CONFIG_SCHEMA,
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_SECRET,
            description="Primary v4 HMAC-SHA256 exact-byte webhook signing key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous webhook authentication material during rotation.",
        ),
    ),
    egress=EgressDeclaration(),
)

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=CURRENT_VERSION,
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(CapabilityDeclaration(CAPABILITY_ID, CONFIG_SCHEMA),),
    secret_bindings=(
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_SECRET,
            description="Primary v4 HMAC-SHA256 exact-byte webhook signing key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous webhook authentication material during rotation.",
        ),
        SecretBindingDeclaration(
            name=API_CLIENT_ID,
            required=False,
            description="Flutterwave v4 OAuth client identifier for reconciliation.",
        ),
        SecretBindingDeclaration(
            name=API_CLIENT_SECRET,
            required=False,
            description="Flutterwave v4 OAuth client secret for reconciliation.",
        ),
    ),
    egress=EgressDeclaration(hosts=(SANDBOX_HOST, LIVE_HOST, IDENTITY_HOST)),
)


class PayloadInvalid(ValueError):
    """An authenticated body is not a JSON event this connector can traverse."""


def _material(secrets: Mapping[str, object], name: str) -> str | None:
    value = secrets.get(name)
    return value if isinstance(value, str) and value else None


def _one_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.casefold() == name]
    return matches[0] if len(matches) == 1 and matches[0] else None


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str | None:
    if isinstance(value, str):
        # Provider identifiers, timestamps and status tokens are evidence.
        # Check emptiness without rewriting the value an operator must inspect.
        return value if value.strip() else None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _parse(raw_body: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw_body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PayloadInvalid(
            "authenticated webhook body is not a JSON object"
        ) from None
    if not isinstance(value, Mapping):
        raise PayloadInvalid("authenticated webhook body is not a JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


def _event_type(event: Mapping[str, object]) -> str:
    return _text(event.get("type")) or "unknown"


def _identity(
    event_type: str,
    data: Mapping[str, object],
    event: Mapping[str, object],
) -> tuple[str, str]:
    provider_identity = (
        _text(event.get("id"))
        or _text(event.get("webhook_id"))
        or _text(data.get("id"))
        or _text(data.get("reference"))
    )
    if provider_identity is not None:
        return f"{event_type}:{provider_identity}", "derived_from_provider_fields"
    digest = hashlib.sha256(_canonical(event)).hexdigest()[:32]
    return f"{event_type}:{digest}", "derived_from_event"


def _provider_status(data: Mapping[str, object]) -> str | None:
    return _text(data.get("status"))


def _transport_evidence(
    *,
    event_type: str,
    data: Mapping[str, object],
    event: Mapping[str, object],
    identity_source: str,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "provider_event_type": event_type,
        "identity_source": identity_source,
        "authentication_scheme": HMAC_SHA256,
        "payload_integrity": HMAC_SHA256,
    }
    webhook_id = _text(event.get("id")) or _text(event.get("webhook_id"))
    if webhook_id is not None:
        evidence["provider_webhook_id"] = webhook_id
    transaction_id = _text(data.get("id"))
    if transaction_id is not None:
        evidence["provider_transaction_id"] = transaction_id
    return evidence


def _record_only(
    *,
    identity: str,
    event_type: str,
    data: Mapping[str, object],
    event: Mapping[str, object],
    identity_source: str,
    reason_code: str,
    normalized_type: str,
) -> InboundEvent:
    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "reason_code": reason_code,
        "transport_evidence": _transport_evidence(
            event_type=event_type,
            data=data,
            event=event,
            identity_source=identity_source,
        ),
    }
    status = _provider_status(data)
    if status is not None:
        payload["provider_status"] = status
    return InboundEvent(
        provider_event_id=identity,
        event_type=normalized_type,
        payload=payload,
        disposition=InboundDisposition.RECORD_ONLY,
    )


def _amount(value: object) -> str | None:
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if not isinstance(value, str | int | Decimal):
        return None
    if (
        isinstance(value, str)
        and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value) is None
    ):
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return format(amount, "f")


def _currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) is not None else None


def _wire_timestamp(value: object, *, milliseconds: bool) -> str | None:
    text = _text(value)
    if isinstance(value, str):
        return text
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    seconds = Decimal(value)
    if milliseconds:
        seconds /= 1000
    if not seconds.is_finite() or seconds < 0:
        return None
    whole_seconds = int(seconds)
    microseconds = int((seconds - whole_seconds) * 1_000_000)
    try:
        occurred_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=whole_seconds,
            microseconds=microseconds,
        )
    except OverflowError:
        return None
    return occurred_at.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _occurred_at(data: Mapping[str, object], event: Mapping[str, object]) -> str | None:
    return _wire_timestamp(
        data.get("created_datetime"), milliseconds=False
    ) or _wire_timestamp(event.get("timestamp"), milliseconds=True)


def _merchant_reference(data: Mapping[str, object]) -> str | None:
    return _text(data.get("reference"))


def _settlement_event(
    *,
    identity: str,
    event_type: str,
    data: Mapping[str, object],
    event: Mapping[str, object],
    identity_source: str,
) -> InboundEvent:
    status = _provider_status(data)
    currency = _currency(data.get("currency"))
    amount = _amount(data.get("amount"))
    occurred_at = _occurred_at(data, event)
    if status is None or currency is None or amount is None or occurred_at is None:
        return _record_only(
            identity=identity,
            event_type=event_type,
            data=data,
            event=event,
            identity_source=identity_source,
            reason_code="settlement_shape_invalid",
            normalized_type="payments.settlement.malformed.v1",
        )
    normalized_status = status.casefold() if status is not None else None
    if normalized_status == "succeeded":
        observation_kind = "capture"
    elif normalized_status == "failed":
        observation_kind = "capture_failed"
    else:
        return _record_only(
            identity=identity,
            event_type=event_type,
            data=data,
            event=event,
            identity_source=identity_source,
            reason_code="settlement_status_not_terminal",
            normalized_type="payments.provider_event.recorded.v1",
        )
    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "observation_kind": observation_kind,
        # Verbatim. A connector translates the wire event to an observation; it
        # does not map the provider's status into a billing lifecycle.
        "provider_status": status,
        "amount": {"amount": amount, "currency": currency},
        "occurred_at": occurred_at,
        "arrival_mode": "ingress",
        "confirmation_evidence": "connector_verified",
        "transport_evidence": _transport_evidence(
            event_type=event_type,
            data=data,
            event=event,
            identity_source=identity_source,
        ),
    }
    reference = _merchant_reference(data)
    if reference is not None:
        payload["merchant_reference"] = reference
    return InboundEvent(
        provider_event_id=identity,
        event_type=CAPABILITY_ID,
        payload=payload,
    )


class FlutterwaveIngressHandler:
    """Flutterwave v4 authentication and provider-neutral event translation."""

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None:
        del request, config, secrets
        return None

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> VerificationResult:
        del config
        primary = _material(secrets, WEBHOOK_SIGNING_SECRET)
        if primary is None:
            return VerificationResult(accepted=False)
        active = tuple(
            material
            for material in (
                primary,
                _material(secrets, WEBHOOK_SIGNING_PREVIOUS_SECRET),
            )
            if material is not None
        )
        supplied = _one_header(request.headers, SIGNATURE_HEADER)
        if supplied is None:
            return VerificationResult(accepted=False)
        try:
            supplied_digest = base64.b64decode(supplied, validate=True)
        except (binascii.Error, ValueError):
            return VerificationResult(accepted=False)
        if len(supplied_digest) != hashlib.sha256().digest_size:
            return VerificationResult(accepted=False)
        comparisons = (
            hmac.compare_digest(
                hmac.new(material.encode(), request.raw_body, hashlib.sha256).digest(),
                supplied_digest,
            )
            for material in active
        )
        matched = tuple(
            position for position, accepted in enumerate(comparisons) if accepted
        )
        return VerificationResult(
            accepted=bool(matched),
            matched_secret_positions=matched,
        )

    def normalize(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
        del config
        event = _parse(request.raw_body)
        event_type = _event_type(event)
        data = _mapping(event.get("data")) or {}
        identity, identity_source = _identity(event_type, data, event)
        if event_type == "charge.completed":
            normalized = _settlement_event(
                identity=identity,
                event_type=event_type,
                data=data,
                event=event,
                identity_source=identity_source,
            )
        else:
            normalized = _record_only(
                identity=identity,
                event_type=event_type,
                data=data,
                event=event,
                identity_source=identity_source,
                reason_code="event_not_in_ingress_slice",
                normalized_type="payments.provider_event.recorded.v1",
            )
        return (normalized,), ACKNOWLEDGEMENT


def _poll_event(data: Mapping[str, object]) -> InboundEvent:
    event = {"type": "charge.reconciled", "data": data}
    identity, source = _identity("charge.reconciled", data, event)
    return _settlement_event(
        identity=identity,
        event_type="charge.reconciled",
        data=data,
        event=event,
        identity_source=source,
    )


@dataclass(frozen=True, slots=True)
class FlutterwaveConnector:
    """One independently released Flutterwave v4 ingress and poll plugin."""

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = (LEGACY_MANIFEST,)
    modes: frozenset[ConnectorMode] = frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL}
    )

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        self.manifest.require_declares(capability_id)
        return FlutterwaveIngressHandler()

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        return FlutterwavePollHandler(_poll_event, self.transport, self.timeout_seconds)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        if "reconcile_from" in config and (
            _material(secrets, API_CLIENT_ID) is None
            or _material(secrets, API_CLIENT_SECRET) is None
        ):
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        if _material(secrets, WEBHOOK_SIGNING_SECRET) is None:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = FlutterwaveConnector()
