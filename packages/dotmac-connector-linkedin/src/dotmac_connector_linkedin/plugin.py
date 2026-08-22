"""LinkedIn webhook authentication and provider-neutral normalization."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dotmac_integration.spi import (
    Acknowledgement,
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    EgressDeclaration,
    InboundEvent,
    IngressHandler,
    IngressRequest,
    SecretBindingDeclaration,
    SpiRange,
)

CONNECTOR_KEY: Final = "linkedin"
SOCIAL_CAPABILITY: Final = "social.activity.observation.v1"
LEAD_CAPABILITY: Final = "marketing.lead.observation.v1"
VERSION: Final = "0.1.0a1"
CLIENT_SECRET: Final = "client_secret"
SIGNATURE_HEADER: Final = "x-li-signature"
_SIGNATURE: Final = re.compile(r"[0-9a-f]{64}")
_EMPTY_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(
        CapabilityDeclaration(SOCIAL_CAPABILITY, _EMPTY_SCHEMA),
        CapabilityDeclaration(LEAD_CAPABILITY, _EMPTY_SCHEMA),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=CLIENT_SECRET,
            description="LinkedIn client secret for challenge and webhook HMAC.",
        ),
    ),
    egress=EgressDeclaration(),
)


class LinkedInPayloadInvalid(ValueError):
    """A verified payload cannot be represented as the declared capability."""


def _material(secrets: Mapping[str, object]) -> str | None:
    value = secrets.get(CLIENT_SECRET)
    return value if isinstance(value, str) and value else None


def _one_header(headers: Mapping[str, str]) -> str | None:
    values = [
        value for key, value in headers.items() if key.casefold() == SIGNATURE_HEADER
    ]
    return values[0] if len(values) == 1 else None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LinkedInPayloadInvalid("verified webhook item is not an object")
    return value


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _parse(raw: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LinkedInPayloadInvalid("verified webhook body is not JSON") from None
    return _mapping(value)


def _items(
    body: Mapping[str, object], capability_id: str
) -> tuple[Mapping[str, object], ...]:
    if capability_id == LEAD_CAPABILITY:
        if body.get("type") != "LEAD_ACTION":
            raise LinkedInPayloadInvalid("lead notification type is invalid")
        return (body,)
    if body.get("type") != "ORGANIZATION_SOCIAL_ACTION_NOTIFICATIONS":
        raise LinkedInPayloadInvalid("social notification type is invalid")
    notifications = body.get("notifications")
    if not isinstance(notifications, list) or not notifications:
        raise LinkedInPayloadInvalid("social notifications are empty or invalid")
    return tuple(_mapping(item) for item in notifications)


def _identity(item: Mapping[str, object]) -> str:
    notification = _text(item.get("notificationId"))
    if notification is not None:
        return notification
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _social(item: Mapping[str, object]) -> InboundEvent:
    action = _text(item.get("action"))
    if action is None:
        raise LinkedInPayloadInvalid("social notification action is missing")
    payload: dict[str, object] = {
        "capability_id": SOCIAL_CAPABILITY,
        "provider_action": action,
        "arrival_mode": "ingress",
    }
    for source, target in (
        ("organizationalEntity", "provider_organization"),
        ("sourcePost", "provider_source_post"),
        ("generatedActivity", "provider_generated_activity"),
        ("lastModifiedAt", "provider_modified_at"),
    ):
        value = _text(item.get(source))
        if value is not None:
            payload[target] = value
    return InboundEvent(_identity(item), SOCIAL_CAPABILITY, payload)


def _lead(item: Mapping[str, object]) -> InboundEvent:
    action = _text(item.get("leadAction"))
    response = _text(item.get("leadGenFormResponse"))
    occurred = _text(item.get("occurredAt"))
    if action is None or response is None or occurred is None:
        raise LinkedInPayloadInvalid("lead notification identity is incomplete")
    payload: dict[str, object] = {
        "capability_id": LEAD_CAPABILITY,
        "provider_action": action,
        "provider_form_response": response,
        "provider_occurred_at": occurred,
        "arrival_mode": "ingress",
    }
    return InboundEvent(f"{response}:{occurred}", LEAD_CAPABILITY, payload)


@dataclass(frozen=True, slots=True)
class LinkedInIngressHandler:
    capability_id: str

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None:
        del config
        code = request.params.get("challengeCode")
        secret = _material(secrets)
        if code is None or secret is None:
            return None
        response = hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()
        body = json.dumps(
            {"challengeCode": code, "challengeResponse": response},
            separators=(",", ":"),
        ).encode()
        return Acknowledgement(body=body, media_type="application/json")

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> bool:
        del config
        supplied = _one_header(request.headers)
        secret = _material(secrets)
        if supplied is None or secret is None or _SIGNATURE.fullmatch(supplied) is None:
            return False
        digest = hmac.new(
            secret.encode(), b"hmacsha256=" + request.raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(supplied, digest)

    def normalize(
        self, request: IngressRequest, *, config: dict[str, object]
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
        del config
        translate = _social if self.capability_id == SOCIAL_CAPABILITY else _lead
        events = tuple(
            translate(item)
            for item in _items(_parse(request.raw_body), self.capability_id)
        )
        return events, Acknowledgement(body=b"")


@dataclass(frozen=True, slots=True)
class LinkedInPlugin:
    manifest: ConnectorManifest = MANIFEST
    historical_manifests: tuple[ConnectorManifest, ...] = ()
    modes: frozenset[ConnectorMode] = frozenset({ConnectorMode.INGRESS})

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        self.manifest.require_declares(capability_id)
        return LinkedInIngressHandler(capability_id)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        del config
        if _material(secrets) is None:
            return (Diagnostic(ok=False, code="configuration_invalid"),)
        return ()


PLUGIN: Final = LinkedInPlugin()
