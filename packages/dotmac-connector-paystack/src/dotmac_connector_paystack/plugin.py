"""Stateless authenticated ingress translation for Paystack webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
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

from dotmac_connector_paystack.polling import PaystackPollHandler

CONNECTOR_KEY: Final = "paystack"
CAPABILITY_ID: Final = "payments.settlement.observation.v1"
VERSION: Final = "0.1.0a1"
CURRENT_VERSION: Final = "0.1.0a2"

SIGNATURE_HEADER: Final = "x-paystack-signature"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
API_SECRET_KEY: Final = "api_secret_key"
SIGNATURE_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{128}")

# Paystack's wire contract represents every supported currency by multiplying
# the base-unit amount by 100. This remains true for XOF even though XOF has no
# ordinary fractional subunit. The constant is therefore provider protocol,
# not a product currency default or an ISO-4217 policy decision.
PAYSTACK_WIRE_SCALE: Final = 2

ACKNOWLEDGEMENT: Final = Acknowledgement(body=b"")

CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reconcile_from": {"type": "string", "format": "date-time"},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
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
            description="Primary HMAC-SHA512 exact-byte webhook signing key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous webhook signing key during bounded rotation.",
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
            description="Primary HMAC-SHA512 exact-byte webhook signing key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous webhook signing key during bounded rotation.",
        ),
        SecretBindingDeclaration(
            name=API_SECRET_KEY,
            required=False,
            description="Paystack server secret for authenticated reconciliation I/O.",
        ),
    ),
    egress=EgressDeclaration(hosts=("api.paystack.co",)),
)


class PayloadInvalid(ValueError):
    """A verified body is not a JSON event this connector can traverse."""


def _material(secrets: Mapping[str, object], name: str) -> str | None:
    value = secrets.get(name)
    return value if isinstance(value, str) and value else None


def _one_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.casefold() == name]
    return matches[0] if len(matches) == 1 else None


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
        value = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PayloadInvalid("verified webhook body is not a JSON object") from None
    if not isinstance(value, Mapping):
        raise PayloadInvalid("verified webhook body is not a JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _identity(
    event_type: str,
    data: Mapping[str, object],
    event: Mapping[str, object],
) -> tuple[str, str]:
    provider_identity = _text(data.get("id")) or _text(data.get("reference"))
    if provider_identity is not None:
        return (
            f"{event_type}:{provider_identity}",
            "derived_from_provider_fields",
        )
    digest = hashlib.sha256(_canonical(event)).hexdigest()[:32]
    return f"{event_type}:{digest}", "derived_from_event"


def _provider_status(data: Mapping[str, object]) -> str | None:
    return _text(data.get("status"))


def _transport_evidence(
    *,
    event_type: str,
    data: Mapping[str, object],
    identity_source: str,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "provider_event_type": event_type,
        "identity_source": identity_source,
    }
    transaction_id = _text(data.get("id"))
    if transaction_id is not None:
        evidence["provider_transaction_id"] = transaction_id
    return evidence


def _record_only(
    *,
    identity: str,
    event_type: str,
    data: Mapping[str, object],
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


def _minor_amount(value: object, *, allow_zero: bool) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < 0 or (value == 0 and not allow_zero):
        return None
    amount = Decimal(value).scaleb(-PAYSTACK_WIRE_SCALE)
    return format(amount, f".{PAYSTACK_WIRE_SCALE}f")


def _currency(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) is not None else None


def _settlement_event(
    *,
    identity: str,
    event_type: str,
    data: Mapping[str, object],
    identity_source: str,
) -> InboundEvent:
    status = _provider_status(data)
    currency = _currency(data.get("currency"))
    amount = _minor_amount(data.get("amount"), allow_zero=False)
    fee = _minor_amount(data.get("fees", 0), allow_zero=True)
    occurred_at = (
        _text(data.get("paid_at"))
        or _text(data.get("transaction_date"))
        or _text(data.get("created_at"))
    )
    if (
        status is None
        or currency is None
        or amount is None
        or fee is None
        or occurred_at is None
    ):
        return _record_only(
            identity=identity,
            event_type=event_type,
            data=data,
            identity_source=identity_source,
            reason_code="settlement_shape_invalid",
            normalized_type="payments.settlement.malformed.v1",
        )

    payload: dict[str, object] = {
        "capability_id": CAPABILITY_ID,
        "observation_kind": "capture",
        # Verbatim. A connector translates the wire event to an observation; it
        # does not map the provider's status into a billing lifecycle.
        "provider_status": status,
        "amount": {"amount": amount, "currency": currency},
        "provider_fee": {"amount": fee, "currency": currency},
        "occurred_at": occurred_at,
        "arrival_mode": "ingress",
        "confirmation_evidence": "connector_verified",
        "transport_evidence": _transport_evidence(
            event_type=event_type,
            data=data,
            identity_source=identity_source,
        ),
    }
    reference = _text(data.get("reference"))
    if reference is not None:
        payload["merchant_reference"] = reference
    return InboundEvent(
        provider_event_id=identity,
        event_type=CAPABILITY_ID,
        payload=payload,
    )


class PaystackIngressHandler:
    """Exact-byte authentication and provider-neutral event translation."""

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
        supplied = _one_header(request.headers, SIGNATURE_HEADER)
        if supplied is None or SIGNATURE_RE.fullmatch(supplied) is None:
            return VerificationResult(accepted=False)
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
        matched: list[int] = []
        for position, material in enumerate(active):
            expected = hmac.new(
                material.encode(), request.raw_body, hashlib.sha512
            ).hexdigest()
            if hmac.compare_digest(expected, supplied):
                matched.append(position)
        return VerificationResult(
            accepted=bool(matched),
            matched_secret_positions=tuple(matched),
        )

    def normalize(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
        del config
        event = _parse(request.raw_body)
        event_type = _text(event.get("event")) or "unknown"
        data = _mapping(event.get("data")) or {}
        identity, identity_source = _identity(event_type, data, event)
        if event_type == "charge.success":
            normalized = _settlement_event(
                identity=identity,
                event_type=event_type,
                data=data,
                identity_source=identity_source,
            )
        else:
            normalized = _record_only(
                identity=identity,
                event_type=event_type,
                data=data,
                identity_source=identity_source,
                reason_code="event_not_in_ingress_slice",
                normalized_type="payments.provider_event.recorded.v1",
            )
        return (normalized,), ACKNOWLEDGEMENT


def _poll_event(data: Mapping[str, object]) -> InboundEvent:
    event_type = "transaction.reconciled"
    identity, source = _identity(event_type, data, data)
    return _settlement_event(
        identity=identity,
        event_type=event_type,
        data=data,
        identity_source=source,
    )


@dataclass(frozen=True, slots=True)
class PaystackConnector:
    """One independently released Paystack ingress and poll plugin."""

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = (LEGACY_MANIFEST,)
    modes: frozenset[ConnectorMode] = frozenset(
        {ConnectorMode.INGRESS, ConnectorMode.POLL}
    )

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        self.manifest.require_declares(capability_id)
        return PaystackIngressHandler()

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        self.manifest.require_declares(capability_id)
        return PaystackPollHandler(_poll_event, self.transport, self.timeout_seconds)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        if "reconcile_from" in config and _material(secrets, API_SECRET_KEY) is None:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        if _material(secrets, WEBHOOK_SIGNING_SECRET) is None:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = PaystackConnector()
