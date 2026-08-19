"""Stateless ingress translation for the Meta WhatsApp Cloud API."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
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
    VerificationResult,
)

CONNECTOR_KEY: Final = "meta_whatsapp"
CAPABILITY_ID: Final = "messaging.receive.v1"
PROVIDER: Final = "meta_cloud_api"
CHANNEL: Final = "whatsapp"
VERSION: Final = "0.1.0a2"
SIGNATURE_HEADER: Final = "x-hub-signature-256"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
WEBHOOK_VERIFY_TOKEN: Final = "webhook_verify_token"  # nosec B105
SIGNATURE_RE: Final[re.Pattern[str]] = re.compile(r"sha256=[0-9a-f]{64}")
SUPPORTED_MESSAGE_TYPES: Final[frozenset[str]] = frozenset(
    {"text", "image", "document", "audio", "video", "sticker", "location"}
)
MEDIA_MESSAGE_TYPES: Final[frozenset[str]] = frozenset(
    {"image", "document", "audio", "video", "sticker"}
)
ACKNOWLEDGEMENT: Final = Acknowledgement(
    body=b'{"status":"ok"}', media_type="application/json"
)

LEGACY_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["signing_slots", "handshake_slot"],
    "properties": {
        "signing_slots": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "handshake_slot": {"type": "string", "minLength": 1, "maxLength": 80},
    },
}

# SPI 1.3 makes the logical secret names part of the installed package contract.
# A current installation therefore has no operator-chosen slot aliases: its
# configuration is empty and its references are keyed by the declarations
# below. The a1 schema remains in HISTORICAL_MANIFEST so a persisted a1 digest
# can still be identified and deliberately adopted.
CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}

HISTORICAL_MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version="0.1.0a1",
    spi_range=SpiRange.parse(">=1.2,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=LEGACY_CONFIG_SCHEMA,
        ),
    ),
)

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=CONFIG_SCHEMA,
        ),
    ),
    secret_bindings=(
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_SECRET,
            description="Primary exact-byte webhook signature key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous webhook signature key during a bounded rotation.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_VERIFY_TOKEN,
            description="Subscription challenge comparison token.",
        ),
    ),
    egress=EgressDeclaration(),
)


class PayloadInvalid(ValueError):
    """A verified body is not a JSON object the connector can traverse."""


def _slot_names(config: Mapping[str, object]) -> tuple[str, ...] | None:
    if "signing_slots" not in config and "handshake_slot" not in config:
        return (WEBHOOK_SIGNING_SECRET, WEBHOOK_SIGNING_PREVIOUS_SECRET)
    raw = config.get("signing_slots")
    if not isinstance(raw, list | tuple) or not raw:
        return None
    if any(not isinstance(item, str) or not item or len(item) > 80 for item in raw):
        return None
    slots = tuple(raw)
    return slots if len(set(slots)) == len(slots) else None


def _handshake_slot(config: Mapping[str, object]) -> str | None:
    if "signing_slots" not in config and "handshake_slot" not in config:
        return WEBHOOK_VERIFY_TOKEN
    raw = config.get("handshake_slot")
    return raw if isinstance(raw, str) and 0 < len(raw) <= 80 else None


def _material(secrets: Mapping[str, object], slot: str) -> str | None:
    value = secrets.get(slot)
    return value if isinstance(value, str) and value else None


def _one_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.casefold() == name]
    return matches[0] if len(matches) == 1 else None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _derived_identity(kind: str, scope: str, item: object) -> str:
    digest = hashlib.sha256(_canonical(item)).hexdigest()[:32]
    return f"{kind}:{scope}:{digest}"


def _scope(entry: Mapping[str, object], value: Mapping[str, object]) -> str:
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        account = metadata.get("phone_number_id")
        if isinstance(account, str) and account:
            return account
    entry_id = entry.get("id")
    return str(entry_id) if entry_id is not None else "unknown"


def _instant(value: object) -> str | None:
    text = value if isinstance(value, str) else ""
    if not text.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(text), tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _profile_name(value: Mapping[str, object], sender: str) -> str | None:
    contacts = value.get("contacts")
    if not isinstance(contacts, list):
        return None
    for contact in contacts:
        if (
            not isinstance(contact, Mapping)
            or str(contact.get("wa_id") or "") != sender
        ):
            continue
        profile = contact.get("profile")
        if isinstance(profile, Mapping):
            name = profile.get("name")
            return name if isinstance(name, str) and name else None
    return None


def _message_body(message: Mapping[str, object], message_type: str) -> str:
    if message_type == "text":
        text = message.get("text")
        if isinstance(text, Mapping) and isinstance(text.get("body"), str):
            return str(text["body"])
        return ""
    if message_type in MEDIA_MESSAGE_TYPES:
        media = message.get(message_type)
        if isinstance(media, Mapping) and isinstance(media.get("caption"), str):
            return str(media["caption"])
    return ""


def _attachments(
    message: Mapping[str, object], message_type: str
) -> list[dict[str, object]]:
    if message_type in MEDIA_MESSAGE_TYPES:
        source = message.get(message_type)
        media = source if isinstance(source, Mapping) else {}
        provider_media_id = media.get("id")
        if not isinstance(provider_media_id, str) or not provider_media_id:
            return []
        attachment: dict[str, object] = {"asset_type": message_type}
        for provider_key, contract_key in (
            ("id", "provider_media_id"),
            ("mime_type", "mime_type"),
            ("caption", "caption"),
            ("filename", "file_name"),
        ):
            value = media.get(provider_key)
            if isinstance(value, str) and value:
                attachment[contract_key] = value
        return [attachment]
    if message_type == "location":
        source = message.get("location")
        location = source if isinstance(source, Mapping) else {}
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if not isinstance(latitude, int | float) or isinstance(latitude, bool):
            return []
        if not isinstance(longitude, int | float) or isinstance(longitude, bool):
            return []
        if not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
            return []
        normalized: dict[str, object] = {
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        for name in ("name", "address"):
            value = location.get(name)
            if isinstance(value, str) and value:
                normalized[name] = value
        return [{"asset_type": "location", "location": normalized}]
    return []


def _evidence(
    locator: str,
    identity_source: str,
    *,
    reason_code: str | None = None,
    provider_message_type: str | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "locator": locator,
        "identity_source": identity_source,
    }
    if reason_code is not None:
        evidence["reason_code"] = reason_code
    if provider_message_type is not None:
        evidence["provider_message_type"] = provider_message_type
    return evidence


def _malformed_message(
    *,
    message: object,
    scope: str,
    locator: str,
    reason_code: str,
) -> InboundEvent:
    message_id = message.get("id") if isinstance(message, Mapping) else None
    provider_id = (
        str(message_id)
        if isinstance(message_id, str) and message_id
        else _derived_identity("message", scope, message)
    )
    identity_source = "provider" if provider_id == message_id else "derived"
    payload: dict[str, object] = {
        "provider": PROVIDER,
        "provider_account_scope": scope,
        "provider_event_id": provider_id,
        "channel": CHANNEL,
        "transport_evidence": _evidence(
            locator, identity_source, reason_code=reason_code
        ),
    }
    if isinstance(message, Mapping):
        observed_at = _instant(message.get("timestamp"))
        if observed_at is not None:
            payload["observed_at"] = observed_at
    return InboundEvent(
        provider_event_id=provider_id,
        event_type="whatsapp.entry.malformed.v1",
        payload=payload,
    )


def _malformed_structure(
    *, item: object, scope: str, locator: str, reason_code: str
) -> InboundEvent:
    """Turn a bad container into evidence instead of silently dropping it."""
    identity = _derived_identity("malformed", scope, {"item": item, "locator": locator})
    return InboundEvent(
        provider_event_id=identity,
        event_type="whatsapp.entry.malformed.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": CHANNEL,
            "transport_evidence": _evidence(
                locator, "derived", reason_code=reason_code
            ),
        },
    )


def _message_event(
    *,
    message: object,
    value: Mapping[str, object],
    entry: Mapping[str, object],
    locator: str,
) -> InboundEvent:
    scope = _scope(entry, value)
    if not isinstance(message, Mapping):
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_object_invalid",
        )
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_id_missing",
        )
    sender = message.get("from")
    if not isinstance(sender, str) or not sender:
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_sender_missing",
        )
    observed_at = _instant(message.get("timestamp"))
    if observed_at is None:
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_timestamp_invalid",
        )
    message_type = str(message.get("type") or "").lower()
    evidence = _evidence(locator, "provider", provider_message_type=message_type)
    common: dict[str, object] = {
        "provider": PROVIDER,
        "provider_account_scope": scope,
        "provider_event_id": message_id,
        "channel": CHANNEL,
        "observed_at": observed_at,
        "transport_evidence": evidence,
    }
    if message_type not in SUPPORTED_MESSAGE_TYPES:
        evidence["reason_code"] = "message_type_unsupported"
        return InboundEvent(
            provider_event_id=message_id,
            event_type="whatsapp.message.received.v1",
            payload=common,
        )
    attachments = _attachments(message, message_type)
    body = _message_body(message, message_type)
    if message_type in MEDIA_MESSAGE_TYPES | {"location"} and not attachments:
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_content_invalid",
        )
    if not body and not attachments:
        return _malformed_message(
            message=message,
            scope=scope,
            locator=locator,
            reason_code="message_content_invalid",
        )
    common["message"] = {
        "contact_address": sender,
        "body": body,
        "contact_name": _profile_name(value, sender),
        "external_message_id": message_id,
        "attachments": attachments,
    }
    return InboundEvent(
        provider_event_id=message_id,
        event_type="whatsapp.message.received.v1",
        payload=common,
    )


def _status_event(
    *,
    status: object,
    value: Mapping[str, object],
    entry: Mapping[str, object],
    locator: str,
) -> InboundEvent:
    scope = _scope(entry, value)
    if not isinstance(status, Mapping):
        identity = _derived_identity("status", scope, status)
        return InboundEvent(
            provider_event_id=identity,
            event_type="whatsapp.entry.malformed.v1",
            payload={
                "provider": PROVIDER,
                "provider_account_scope": scope,
                "provider_event_id": identity,
                "channel": CHANNEL,
                "transport_evidence": _evidence(
                    locator, "derived", reason_code="status_object_invalid"
                ),
            },
        )
    message_id = status.get("id")
    state = status.get("status")
    timestamp = status.get("timestamp")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(state, str)
        or not state
        or not isinstance(timestamp, str)
        or not timestamp
    ):
        identity = _derived_identity("status", scope, status)
        return InboundEvent(
            provider_event_id=identity,
            event_type="whatsapp.entry.malformed.v1",
            payload={
                "provider": PROVIDER,
                "provider_account_scope": scope,
                "provider_event_id": identity,
                "channel": CHANNEL,
                "transport_evidence": _evidence(
                    locator, "derived", reason_code="status_identity_incomplete"
                ),
            },
        )
    observed_at = _instant(timestamp)
    if observed_at is None:
        identity = _derived_identity("status", scope, status)
        return InboundEvent(
            provider_event_id=identity,
            event_type="whatsapp.entry.malformed.v1",
            payload={
                "provider": PROVIDER,
                "provider_account_scope": scope,
                "provider_event_id": identity,
                "channel": CHANNEL,
                "transport_evidence": _evidence(
                    locator, "derived", reason_code="status_timestamp_invalid"
                ),
            },
        )
    identity = f"{message_id}:{state}:{timestamp}"
    errors = status.get("errors")
    codes = (
        [
            str(item["code"])
            for item in errors
            if isinstance(item, Mapping) and item.get("code") is not None
        ]
        if isinstance(errors, list)
        else []
    )
    return InboundEvent(
        provider_event_id=identity,
        event_type="whatsapp.message.status.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": CHANNEL,
            "observed_at": observed_at,
            "delivery_receipt": {
                "external_message_id": message_id,
                "status": state,
                "recipient_id": status.get("recipient_id"),
                "error_codes": codes,
            },
            "transport_evidence": _evidence(locator, "provider"),
        },
    )


def _error_event(
    *,
    error: object,
    value: Mapping[str, object],
    entry: Mapping[str, object],
    locator: str,
) -> InboundEvent:
    scope = _scope(entry, value)
    identity = _derived_identity("error", scope, error)
    return InboundEvent(
        provider_event_id=identity,
        event_type="whatsapp.error.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": CHANNEL,
            "transport_evidence": _evidence(locator, "derived"),
        },
    )


class WhatsAppIngressHandler:
    """One immutable, stateless handler shared by every installation."""

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None:
        if request.params.get("hub.mode") != "subscribe":
            return None
        slot = _handshake_slot(config)
        expected = _material(secrets, slot) if slot is not None else None
        presented = request.params.get("hub.verify_token")
        challenge = request.params.get("hub.challenge")
        if expected is None or presented is None or challenge is None:
            return None
        if not hmac.compare_digest(presented, expected):
            return None
        return Acknowledgement(body=challenge.encode(), media_type="text/plain")

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> VerificationResult:
        presented = _one_header(request.headers, SIGNATURE_HEADER)
        if presented is None or SIGNATURE_RE.fullmatch(presented) is None:
            return VerificationResult(accepted=False)
        slots = _slot_names(config)
        if slots is None:
            return VerificationResult(accepted=False)
        matched: list[int] = []
        for position, slot in enumerate(slots):
            material = _material(secrets, slot)
            if material is None:
                continue
            expected = (
                "sha256="
                + hmac.new(
                    material.encode(), request.raw_body, hashlib.sha256
                ).hexdigest()
            )
            if hmac.compare_digest(presented, expected):
                matched.append(position)
        return VerificationResult(
            accepted=bool(matched), matched_secret_positions=tuple(matched)
        )

    def normalize(
        self, request: IngressRequest, *, config: dict[str, object]
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
        del config
        try:
            document = json.loads(request.raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PayloadInvalid("verified body is not valid JSON") from None
        if not isinstance(document, Mapping):
            raise PayloadInvalid("verified body is not a JSON object")

        events: list[InboundEvent] = []
        entries = document.get("entry")
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise PayloadInvalid("entry is not a list")
        for entry_index, raw_entry in enumerate(entries):
            entry_locator = f"/entry/{entry_index}"
            if not isinstance(raw_entry, Mapping):
                events.append(
                    _malformed_structure(
                        item=raw_entry,
                        scope="unknown",
                        locator=entry_locator,
                        reason_code="entry_object_invalid",
                    )
                )
                continue
            changes = raw_entry.get("changes")
            if not isinstance(changes, list):
                events.append(
                    _malformed_structure(
                        item=changes,
                        scope=str(raw_entry.get("id") or "unknown"),
                        locator=f"{entry_locator}/changes",
                        reason_code="changes_list_invalid",
                    )
                )
                continue
            for change_index, raw_change in enumerate(changes):
                change_locator = f"{entry_locator}/changes/{change_index}"
                if not isinstance(raw_change, Mapping):
                    events.append(
                        _malformed_structure(
                            item=raw_change,
                            scope=str(raw_entry.get("id") or "unknown"),
                            locator=change_locator,
                            reason_code="change_object_invalid",
                        )
                    )
                    continue
                raw_value = raw_change.get("value")
                if not isinstance(raw_value, Mapping):
                    events.append(
                        _malformed_structure(
                            item=raw_value,
                            scope=str(raw_entry.get("id") or "unknown"),
                            locator=f"{change_locator}/value",
                            reason_code="value_object_invalid",
                        )
                    )
                    continue
                base = f"{change_locator}/value"
                messages = raw_value.get("messages")
                if isinstance(messages, list):
                    for item_index, message in enumerate(messages):
                        events.append(
                            _message_event(
                                message=message,
                                value=raw_value,
                                entry=raw_entry,
                                locator=f"{base}/messages/{item_index}",
                            )
                        )
                statuses = raw_value.get("statuses")
                if isinstance(statuses, list):
                    for item_index, status in enumerate(statuses):
                        events.append(
                            _status_event(
                                status=status,
                                value=raw_value,
                                entry=raw_entry,
                                locator=f"{base}/statuses/{item_index}",
                            )
                        )
                errors = raw_value.get("errors")
                if isinstance(errors, list):
                    for item_index, error in enumerate(errors):
                        events.append(
                            _error_event(
                                error=error,
                                value=raw_value,
                                entry=raw_entry,
                                locator=f"{base}/errors/{item_index}",
                            )
                        )
        identities = [event.provider_event_id for event in events]
        if len(identities) != len(set(identities)):
            raise PayloadInvalid(
                "one provider batch contains duplicate event identities"
            )
        return tuple(events), ACKNOWLEDGEMENT


HANDLER: Final[IngressHandler] = WhatsAppIngressHandler()


class WhatsAppConnector:
    """SPI plugin object discovered from package metadata."""

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return (HISTORICAL_MANIFEST,)

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.INGRESS})

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        MANIFEST.require_declares(capability_id)
        return HANDLER

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        slots = _slot_names(config)
        handshake = _handshake_slot(config)
        if slots is None:
            return (Diagnostic(ok=False, code="signing_slots_invalid"),)
        if handshake is None:
            return (Diagnostic(ok=False, code="handshake_slot_invalid"),)
        if "signing_slots" in config or "handshake_slot" in config:
            # Published a1 treated every configured slot as required. Keep that
            # executable behaviour while a persisted a1 manifest is adopted.
            required = (*slots, handshake)
        else:
            required = (WEBHOOK_SIGNING_SECRET, WEBHOOK_VERIFY_TOKEN)
        if any(_material(secrets, slot) is None for slot in required):
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = WhatsAppConnector()

__all__ = ["MANIFEST", "PLUGIN", "WhatsAppConnector"]
