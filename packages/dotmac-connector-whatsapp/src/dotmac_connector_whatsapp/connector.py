"""Ingress-only Meta WhatsApp adapter for ``messaging.receive.v1``.

The module owns receipt identity, persistence, delivery and retry. This package
knows only Meta's wire: handshake fields, the HMAC header, webhook traversal and
the provider-neutral observation shape Sub declared. It imports no ORM, opens
no network connection and retains no credential material.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from dotmac_integration import (
    Acknowledgement,
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    InboundDisposition,
    InboundEvent,
    IngressRequest,
    SpiRange,
)

CONNECTOR_KEY: Final = "meta_whatsapp"
CAPABILITY_ID: Final = "messaging.receive.v1"
PROVIDER: Final = "meta_cloud_api"
CHANNEL: Final = "whatsapp"
MESSAGE_EVENT_TYPE: Final = "whatsapp.message.received.v1"
STATUS_EVENT_TYPE: Final = "whatsapp.message.status.v1"
MALFORMED_EVENT_TYPE: Final = "whatsapp.entry.malformed.v1"
ERROR_EVENT_TYPE: Final = "whatsapp.error.v1"
_ACK = Acknowledgement(body=b'{"status":"ok"}', media_type="application/json")
_SIGNATURE_RE = re.compile(r"^sha256=[0-9a-f]{64}$")
_EPOCH = "1970-01-01T00:00:00+00:00"
_MESSAGE_TYPES = frozenset(
    {
        "text",
        "image",
        "document",
        "audio",
        "video",
        "sticker",
        "location",
        "button",
        "interactive",
    }
)

MANIFEST = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version="0.1.0a1",
    spi_range=SpiRange.parse(">=1.1,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["signing_slots", "challenge_slot"],
                "properties": {
                    "signing_slots": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "challenge_slot": {"type": "string", "minLength": 1},
                },
            },
        ),
    ),
)


def _slot_names(config: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = config.get(field)
    if field == "challenge_slot":
        values = (value,)
    elif isinstance(value, list | tuple):
        values = tuple(value)
    else:
        return ()
    names = tuple(item.strip() for item in values if isinstance(item, str))
    if len(names) != len(values) or not names or any(not name for name in names):
        return ()
    if len(names) != len(set(names)):
        return ()
    return names


def _one_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.lower() == name.lower()]
    return matches[0] if len(matches) == 1 else None


def _observed_at(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    timestamp = int(raw)
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class WhatsAppPayloadInvalid(ValueError):
    """The signed body has no stable provider-event identity to record.

    The engine reports only this type name. Request content is deliberately not
    carried in the exception, its traceback, or a synthetic receipt identity.
    """


def _derived_identity(*, account_scope: str, node: object) -> str:
    """Derive an identity from ONE provider item, never its carrier request."""
    material = json.dumps(
        node,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:32]
    return f"wa:error:{account_scope}:{digest}"


def _record_only(
    locator: str,
    reason: str | None,
    *,
    node: object,
    account_scope: str | None = None,
    provider_event_id: str | None = None,
    event_type: str = MALFORMED_EVENT_TYPE,
) -> InboundEvent:
    scope = account_scope or "unknown"
    identity_source = "provider" if provider_event_id is not None else "derived"
    return InboundEvent(
        provider_event_id=provider_event_id
        or _derived_identity(account_scope=scope, node=node),
        event_type=event_type,
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "channel": CHANNEL,
            "observed_at": _EPOCH,
            "transport_evidence": {
                "locator": locator,
                "identity_source": identity_source,
                "reason_code": reason,
            },
        },
        disposition=InboundDisposition.RECORD_ONLY,
    )


def _contact_names(value: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    contacts = value.get("contacts")
    if not isinstance(contacts, list):
        return result
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        address = _text(contact.get("wa_id"))
        profile = contact.get("profile")
        name = _text(profile.get("name")) if isinstance(profile, dict) else None
        if address and name:
            result[address] = name
    return result


def _message_body(message: Mapping[str, object], message_type: str) -> str | None:
    value = message.get(message_type)
    payload = value if isinstance(value, dict) else {}
    if message_type == "text":
        return _text(payload.get("body"))
    if message_type == "button":
        return _text(payload.get("text"))
    if message_type == "interactive":
        for key in ("button_reply", "list_reply"):
            reply = payload.get(key)
            if isinstance(reply, dict):
                return _text(reply.get("title"))
    return _text(payload.get("caption"))


def _attachment(
    message: Mapping[str, object], message_type: str
) -> dict[str, object] | None:
    if message_type in {"text", "button", "interactive"}:
        return None
    raw = message.get(message_type)
    payload = raw if isinstance(raw, dict) else {}
    if message_type == "location":
        latitude, longitude = payload.get("latitude"), payload.get("longitude")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, int | float)
            or not isinstance(longitude, int | float)
            or not -90 <= float(latitude) <= 90
            or not -180 <= float(longitude) <= 180
        ):
            return None
        return {
            "asset_type": "location",
            "file_name": None,
            "mime_type": None,
            "provider_media_id": None,
            "source_url": None,
            "caption": None,
            "file_size": None,
            "download_status": "metadata_only",
            "location": {
                "latitude": float(latitude),
                "longitude": float(longitude),
                "name": _text(payload.get("name")),
                "address": _text(payload.get("address")),
            },
        }
    media_id = _text(payload.get("id"))
    if media_id is None:
        return None
    return {
        "asset_type": message_type,
        "file_name": _text(payload.get("filename")),
        "mime_type": _text(payload.get("mime_type")),
        "provider_media_id": media_id,
        "source_url": None,
        "caption": _text(payload.get("caption")),
        "file_size": None,
        "download_status": "metadata_only",
        "location": None,
    }


def _message_event(
    message: object,
    *,
    locator: str,
    account_scope: str | None,
    contact_names: Mapping[str, str],
) -> InboundEvent:
    if not isinstance(message, dict):
        return _record_only(
            locator,
            "message_not_object",
            node=message,
            account_scope=account_scope,
        )
    message_id = _text(message.get("id"))
    sender = _text(message.get("from"))
    message_type = (_text(message.get("type")) or "").lower()
    message_identity = f"wa:msg:{message_id}" if message_id else None
    if bool(message.get("is_echo")):
        return _record_only(
            locator,
            "echo_message",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    if message_type not in _MESSAGE_TYPES:
        return _record_only(
            locator,
            "message_type_unsupported",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
            event_type=MESSAGE_EVENT_TYPE,
        )
    observed_at = _observed_at(message.get("timestamp"))
    if not message_id:
        return _record_only(
            locator,
            "message_id_missing",
            node=message,
            account_scope=account_scope,
        )
    if not sender:
        return _record_only(
            locator,
            "message_sender_missing",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    if not account_scope:
        return _record_only(
            locator,
            "message_account_scope_missing",
            node=message,
            provider_event_id=message_identity,
        )
    if observed_at is None:
        return _record_only(
            locator,
            "message_timestamp_invalid",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    body = _message_body(message, message_type)
    attachment = _attachment(message, message_type)
    if message_type == "text" and body is None:
        return _record_only(
            locator,
            "text_body_missing",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    if message_type not in {"text", "button", "interactive"} and attachment is None:
        return _record_only(
            locator,
            "attachment_metadata_invalid",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    if message_type in {"button", "interactive"} and body is None:
        return _record_only(
            locator,
            "interactive_body_missing",
            node=message,
            account_scope=account_scope,
            provider_event_id=message_identity,
        )
    return InboundEvent(
        provider_event_id=f"wa:msg:{message_id}",
        event_type=MESSAGE_EVENT_TYPE,
        payload={
            "provider": PROVIDER,
            "provider_account_scope": account_scope,
            "channel": CHANNEL,
            "observed_at": observed_at,
            "message": {
                "contact_address": sender,
                "body": body,
                "contact_name": contact_names.get(sender),
                "subject": None,
                "external_message_id": message_id,
                "external_thread_id": None,
                "provider_account_id": account_scope,
                "external_account_id": None,
                "page_id": None,
                "instagram_account_id": None,
                "surface": None,
                "permalink_url": None,
                "media_url": None,
                "contact_profile": None,
                "attachments": [attachment] if attachment is not None else [],
            },
            "transport_evidence": {
                "locator": locator,
                "identity_source": "provider",
                "reason_code": None,
            },
        },
    )


def _status_event(
    status: object, *, locator: str, account_scope: str | None
) -> InboundEvent:
    if not isinstance(status, dict):
        return _record_only(
            locator,
            "status_not_object",
            node=status,
            account_scope=account_scope,
        )
    message_id = _text(status.get("id"))
    status_name = _text(status.get("status"))
    timestamp = _text(status.get("timestamp"))
    observed_at = _observed_at(timestamp)
    status_identity = (
        f"wa:status:{message_id}:{status_name}:{timestamp}"
        if message_id and status_name and timestamp
        else None
    )
    if not account_scope or not message_id or not status_name or observed_at is None:
        return _record_only(
            locator,
            "status_identity_incomplete",
            node=status,
            account_scope=account_scope,
            provider_event_id=status_identity,
        )
    errors = status.get("errors")
    error_codes = (
        [
            str(item["code"])
            for item in errors
            if isinstance(item, dict) and item.get("code") is not None
        ]
        if isinstance(errors, list)
        else []
    )
    return InboundEvent(
        provider_event_id=f"wa:status:{message_id}:{status_name}:{timestamp}",
        event_type=STATUS_EVENT_TYPE,
        payload={
            "provider": PROVIDER,
            "provider_account_scope": account_scope,
            "channel": CHANNEL,
            "observed_at": observed_at,
            "delivery_receipt": {
                "external_message_id": message_id,
                "status": status_name,
                "recipient_id": _text(status.get("recipient_id")),
                "error_codes": error_codes,
            },
            "transport_evidence": {
                "locator": locator,
                "identity_source": "provider",
                "reason_code": None,
            },
        },
    )


def _normalize_payload(raw_body: bytes) -> tuple[InboundEvent, ...]:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, ValueError):
        raise WhatsAppPayloadInvalid() from None
    if not isinstance(payload, dict):
        raise WhatsAppPayloadInvalid()
    if "object" in payload and payload.get("object") != "whatsapp_business_account":
        raise WhatsAppPayloadInvalid()
    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise WhatsAppPayloadInvalid()
    if not entries:
        return ()

    events: list[InboundEvent] = []
    for entry_index, entry in enumerate(entries):
        entry_path = f"/entry/{entry_index}"
        if not isinstance(entry, dict):
            events.append(_record_only(entry_path, "entry_not_object", node=entry))
            continue
        entry_scope = _text(entry.get("id"))
        changes = entry.get("changes")
        if not isinstance(changes, list) or not changes:
            events.append(
                _record_only(
                    f"{entry_path}/changes",
                    "changes_missing_or_empty",
                    node=entry,
                    account_scope=entry_scope,
                )
            )
            continue
        for change_index, change in enumerate(changes):
            change_path = f"{entry_path}/changes/{change_index}"
            if not isinstance(change, dict):
                events.append(
                    _record_only(
                        change_path,
                        "change_not_object",
                        node=change,
                        account_scope=entry_scope,
                    )
                )
                continue
            if change.get("field") != "messages":
                events.append(
                    _record_only(
                        f"{change_path}/field",
                        "unsupported_change_field",
                        node=change,
                        account_scope=entry_scope,
                    )
                )
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                events.append(
                    _record_only(
                        f"{change_path}/value",
                        "value_not_object",
                        node=value,
                        account_scope=entry_scope,
                    )
                )
                continue
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            account_scope = (
                _text(metadata.get("phone_number_id"))
                or _text(metadata.get("display_phone_number"))
                or entry_scope
            )
            names = _contact_names(value)
            handled = False
            messages = value.get("messages")
            if "messages" in value:
                handled = True
                if isinstance(messages, list):
                    events.extend(
                        _message_event(
                            message,
                            locator=f"{change_path}/value/messages/{index}",
                            account_scope=account_scope,
                            contact_names=names,
                        )
                        for index, message in enumerate(messages)
                    )
                else:
                    events.append(
                        _record_only(
                            f"{change_path}/value/messages",
                            "messages_not_array",
                            node=messages,
                            account_scope=account_scope,
                        )
                    )
            statuses = value.get("statuses")
            if "statuses" in value:
                handled = True
                if isinstance(statuses, list):
                    events.extend(
                        _status_event(
                            status,
                            locator=f"{change_path}/value/statuses/{index}",
                            account_scope=account_scope,
                        )
                        for index, status in enumerate(statuses)
                    )
                else:
                    events.append(
                        _record_only(
                            f"{change_path}/value/statuses",
                            "statuses_not_array",
                            node=statuses,
                            account_scope=account_scope,
                        )
                    )
            errors = value.get("errors")
            if "errors" in value:
                handled = True
                if isinstance(errors, list):
                    events.extend(
                        _record_only(
                            f"{change_path}/value/errors/{index}",
                            None,
                            node=error,
                            account_scope=account_scope,
                            event_type=ERROR_EVENT_TYPE,
                        )
                        for index, error in enumerate(errors)
                    )
                else:
                    events.append(
                        _record_only(
                            f"{change_path}/value/errors",
                            "errors_not_array",
                            node=errors,
                            account_scope=account_scope,
                        )
                    )
            if not handled:
                events.append(
                    _record_only(
                        f"{change_path}/value",
                        "no_message_or_status",
                        node=value,
                        account_scope=account_scope,
                    )
                )
    return tuple(events)


class _WhatsAppIngressHandler:
    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None:
        slots = _slot_names(config, "challenge_slot")
        mode = request.params.get("hub.mode")
        presented = request.params.get("hub.verify_token")
        challenge = request.params.get("hub.challenge")
        if (
            len(slots) != 1
            or mode != "subscribe"
            or presented is None
            or challenge is None
        ):
            return None
        expected = secrets.get(slots[0])
        if not isinstance(expected, str) or not hmac.compare_digest(
            presented, expected
        ):
            return None
        return Acknowledgement(body=challenge.encode("utf-8"), media_type="text/plain")

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> bool:
        presented = _one_header(request.headers, "X-Hub-Signature-256")
        slots = _slot_names(config, "signing_slots")
        if presented is None or not _SIGNATURE_RE.fullmatch(presented) or not slots:
            return False
        matched = False
        for slot in slots:
            material = secrets.get(slot)
            usable = material if isinstance(material, str) else ""
            expected = (
                "sha256="
                + hmac.new(
                    usable.encode("utf-8"), request.raw_body, hashlib.sha256
                ).hexdigest()
            )
            matched |= hmac.compare_digest(presented, expected)
        return matched

    def normalize(
        self, request: IngressRequest, *, config: dict[str, object]
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement | None]:
        return _normalize_payload(request.raw_body), _ACK


class WhatsAppPlugin:
    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.INGRESS})

    def ingress_handler_for(self, capability_id: str) -> _WhatsAppIngressHandler:
        MANIFEST.require_declares(capability_id)
        return _WhatsAppIngressHandler()

    def validate_connection(
        self, *, config: dict[str, object], secrets: dict[str, object]
    ) -> tuple[Diagnostic, ...]:
        signing_slots = _slot_names(config, "signing_slots")
        challenge_slots = _slot_names(config, "challenge_slot")
        if not signing_slots or len(challenge_slots) != 1:
            return (Diagnostic(ok=False, code="whatsapp_config_invalid"),)
        missing = tuple(
            slot
            for slot in (*signing_slots, *challenge_slots)
            if not isinstance(secrets.get(slot), str) or not secrets[slot]
        )
        if missing:
            return (
                Diagnostic(
                    ok=False,
                    code="whatsapp_material_missing",
                    detail=f"{len(set(missing))} configured slot(s) are unavailable",
                ),
            )
        return (Diagnostic(ok=True, code="whatsapp_ingress_ready"),)


PLUGIN = WhatsAppPlugin()

__all__ = [
    "CAPABILITY_ID",
    "CONNECTOR_KEY",
    "MANIFEST",
    "PLUGIN",
    "WhatsAppPayloadInvalid",
    "WhatsAppPlugin",
]
