"""Stateless Meta Social webhook authentication and wire translation."""

from __future__ import annotations

import hashlib
import hmac
import json
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
    InboundDisposition,
    InboundEvent,
    IngressHandler,
    IngressRequest,
    SecretBindingDeclaration,
    SpiRange,
    VerificationResult,
)

CONNECTOR_KEY: Final = "meta_social"
CAPABILITY_ID: Final = "messaging.receive.v1"
PROVIDER: Final = "meta_cloud_api"
VERSION: Final = "0.1.0a1"
SIGNATURE_HEADER: Final = "x-hub-signature-256"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
WEBHOOK_VERIFY_TOKEN: Final = "webhook_verify_token"  # nosec B105
ACKNOWLEDGEMENT: Final = Acknowledgement(
    body=b'{"status":"ok"}', media_type="application/json"
)
CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}

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
            description="Primary exact-byte Meta app signature key.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_SIGNING_PREVIOUS_SECRET,
            required=False,
            description="Previous Meta app signature key during bounded rotation.",
        ),
        SecretBindingDeclaration(
            name=WEBHOOK_VERIFY_TOKEN,
            description="Subscription challenge comparison token.",
        ),
    ),
    egress=EgressDeclaration(),
)


class PayloadInvalid(ValueError):
    """A verified Meta body cannot be traversed without guessing."""


def _material(secrets: Mapping[str, object], name: str) -> str | None:
    value = secrets.get(name)
    return value if isinstance(value, str) and value else None


def _one_header(headers: Mapping[str, str], name: str) -> str | None:
    matches = [value for key, value in headers.items() if key.casefold() == name]
    return matches[0] if len(matches) == 1 else None


def _text(value: object) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _derived_identity(kind: str, scope: str, item: object) -> str:
    digest = hashlib.sha256(_canonical(item)).hexdigest()[:32]
    return f"{kind}:{scope}:{digest}"


def _instant(value: object, *, milliseconds: bool) -> str | None:
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    divisor = 1000 if milliseconds else 1
    try:
        return datetime.fromtimestamp(timestamp / divisor, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _evidence(
    locator: str,
    identity_source: str,
    *,
    reason_code: str | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "locator": locator,
        "identity_source": identity_source,
    }
    if reason_code is not None:
        evidence["reason_code"] = reason_code
    return evidence


def _record_only(
    *,
    item: object,
    scope: str,
    locator: str,
    reason_code: str,
    provider_event_id: str | None = None,
) -> InboundEvent:
    identity = provider_event_id or _derived_identity("meta", scope, item)
    return InboundEvent(
        provider_event_id=identity,
        event_type="meta.transport.recorded.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": "meta_social",
            "transport_evidence": _evidence(
                locator,
                "provider" if provider_event_id else "derived",
                reason_code=reason_code,
            ),
        },
        disposition=InboundDisposition.RECORD_ONLY,
    )


def _attachments(message: Mapping[str, object]) -> list[dict[str, object]]:
    raw_attachments = message.get("attachments")
    if not isinstance(raw_attachments, list):
        return []
    normalized: list[dict[str, object]] = []
    for raw in raw_attachments:
        if not isinstance(raw, Mapping):
            continue
        payload = raw.get("payload")
        material = payload if isinstance(payload, Mapping) else {}
        asset_type = _text(raw.get("type")) or "attachment"
        attachment: dict[str, object] = {"asset_type": asset_type}
        for source, target in (
            ("id", "provider_media_id"),
            ("url", "source_url"),
            ("filename", "file_name"),
            ("mime_type", "mime_type"),
            ("caption", "caption"),
        ):
            value = _text(material.get(source))
            if value is not None:
                attachment[target] = value
        size = material.get("size")
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            attachment["file_size"] = size
        attachment["download_status"] = (
            "remote_available" if "source_url" in attachment else "metadata_only"
        )
        normalized.append(attachment)
    return normalized


def _message_event(
    *,
    item: object,
    scope: str,
    channel: str,
    locator: str,
) -> InboundEvent:
    if not isinstance(item, Mapping):
        return _record_only(
            item=item,
            scope=scope,
            locator=locator,
            reason_code="message_object_invalid",
        )
    raw_message = item.get("message")
    message = raw_message if isinstance(raw_message, Mapping) else None
    provider_id = _text(message.get("mid")) if message is not None else None
    if message is None:
        return _record_only(
            item=item,
            scope=scope,
            locator=locator,
            reason_code="message_missing",
            provider_event_id=provider_id,
        )
    sender = item.get("sender")
    sender_id = _text(sender.get("id")) if isinstance(sender, Mapping) else None
    identity = provider_id or _derived_identity("message", scope, item)
    if message.get("is_echo") is True:
        return _record_only(
            item=item,
            scope=scope,
            locator=locator,
            reason_code="message_echo",
            provider_event_id=identity,
        )
    if any(key in message for key in ("reaction", "is_deleted", "deleted")):
        return _record_only(
            item=item,
            scope=scope,
            locator=locator,
            reason_code="message_non_delivery_event",
            provider_event_id=identity,
        )
    observed_at = _instant(item.get("timestamp"), milliseconds=True)
    attachments = _attachments(message)
    body = _text(message.get("text")) or ""
    if sender_id is None or observed_at is None or (not body and not attachments):
        return _record_only(
            item=item,
            scope=scope,
            locator=locator,
            reason_code="message_incomplete",
            provider_event_id=identity,
        )
    normalized_message: dict[str, object] = {
        "contact_address": sender_id,
        "body": body,
        "external_message_id": identity,
        "provider_account_id": scope,
        "external_account_id": scope,
        "attachments": attachments,
    }
    if channel == "facebook_messenger":
        normalized_message["page_id"] = scope
    else:
        normalized_message["instagram_account_id"] = scope
    return InboundEvent(
        provider_event_id=identity,
        event_type="meta.message.received.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": channel,
            "observed_at": observed_at,
            "message": normalized_message,
            "transport_evidence": _evidence(
                locator,
                "provider" if provider_id is not None else "derived",
            ),
        },
    )


def _comment_author(value: Mapping[str, object]) -> tuple[str | None, str | None]:
    author = value.get("from")
    if isinstance(author, Mapping):
        return (
            _text(author.get("id")) or _text(author.get("username")),
            _text(author.get("name")) or _text(author.get("username")),
        )
    return (
        _text(value.get("sender_id")) or _text(value.get("user_id")),
        _text(value.get("username")) or _text(value.get("sender_name")),
    )


def _comment_event(
    *,
    change: object,
    scope: str,
    channel: str,
    locator: str,
) -> InboundEvent:
    if not isinstance(change, Mapping):
        return _record_only(
            item=change,
            scope=scope,
            locator=locator,
            reason_code="change_object_invalid",
        )
    field = str(change.get("field") or "").strip().lower()
    raw_value = change.get("value")
    if not isinstance(raw_value, Mapping):
        return _record_only(
            item=change,
            scope=scope,
            locator=locator,
            reason_code="change_value_invalid",
        )
    item_type = str(raw_value.get("item") or raw_value.get("object") or "").lower()
    if field not in {"feed", "comments", "live_comments"} and (
        "comment" not in item_type
    ):
        return _record_only(
            item=change,
            scope=scope,
            locator=locator,
            reason_code="change_unsupported",
        )
    comment_id = (
        _text(raw_value.get("comment_id"))
        or _text(raw_value.get("id"))
        or _text(raw_value.get("instagram_comment_id"))
    )
    media = raw_value.get("media")
    media_id = _text(media.get("id")) if isinstance(media, Mapping) else None
    post_id = (
        _text(raw_value.get("post_id"))
        or _text(raw_value.get("media_id"))
        or media_id
        or _text(raw_value.get("parent_id"))
        or _text(raw_value.get("video_id"))
    )
    author_id, author_name = _comment_author(raw_value)
    body = _text(raw_value.get("message")) or _text(raw_value.get("text")) or ""
    media_url = _text(raw_value.get("media_url")) or _text(raw_value.get("photo"))
    attachments: list[dict[str, object]] = []
    if media_url is not None:
        attachments.append(
            {
                "asset_type": "image",
                "source_url": media_url,
                "download_status": "remote_available",
            }
        )
    identity = comment_id or _derived_identity("comment", scope, change)
    observed_at = _instant(
        raw_value.get("created_time"), milliseconds=False
    ) or _instant(change.get("time"), milliseconds=False)
    if (
        post_id is None
        or author_id is None
        or observed_at is None
        or (not body and not attachments)
    ):
        return _record_only(
            item=change,
            scope=scope,
            locator=locator,
            reason_code="comment_incomplete",
            provider_event_id=identity,
        )
    parent_id = _text(raw_value.get("parent_id"))
    if parent_id == post_id:
        parent_id = None
    message: dict[str, object] = {
        "contact_address": author_id,
        "body": body,
        "contact_name": author_name,
        "subject": f"{channel.replace('_', ' ').title()} post {post_id}",
        "external_message_id": identity,
        "external_thread_id": f"{channel}:{post_id}",
        "provider_account_id": scope,
        "external_account_id": scope,
        "surface": "post_comment",
        "attachments": attachments,
    }
    if channel == "facebook_comment":
        message["page_id"] = scope
    else:
        message["instagram_account_id"] = scope
    return InboundEvent(
        provider_event_id=identity,
        event_type="meta.comment.received.v1",
        payload={
            "provider": PROVIDER,
            "provider_account_scope": scope,
            "provider_event_id": identity,
            "channel": channel,
            "observed_at": observed_at,
            "message": message,
            "transport_evidence": {
                **_evidence(
                    locator,
                    "provider" if comment_id is not None else "derived",
                ),
                "post_id": post_id,
                "parent_provider_comment_id": parent_id,
            },
        },
    )


class MetaSocialIngressHandler:
    """One immutable handler shared by every Meta Social installation."""

    def challenge(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> Acknowledgement | None:
        del config
        mode = request.params.get("hub.mode")
        presented = request.params.get("hub.verify_token")
        challenge = request.params.get("hub.challenge")
        expected = _material(secrets, WEBHOOK_VERIFY_TOKEN)
        if (
            mode != "subscribe"
            or expected is None
            or presented is None
            or challenge is None
            or not hmac.compare_digest(presented, expected)
        ):
            return None
        return Acknowledgement(body=challenge.encode(), media_type="text/plain")

    def verify(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> VerificationResult:
        del config
        presented = _one_header(request.headers, SIGNATURE_HEADER)
        if presented is None:
            return VerificationResult(accepted=False)
        matches: list[int] = []
        for position, name in enumerate(
            (WEBHOOK_SIGNING_SECRET, WEBHOOK_SIGNING_PREVIOUS_SECRET)
        ):
            secret = _material(secrets, name)
            if secret is None:
                continue
            expected = (
                "sha256="
                + hmac.new(
                    secret.encode(), request.raw_body, hashlib.sha256
                ).hexdigest()
            )
            if hmac.compare_digest(presented, expected):
                matches.append(position)
        return VerificationResult(
            accepted=bool(matches),
            matched_secret_positions=tuple(matches),
        )

    def normalize(
        self,
        request: IngressRequest,
        *,
        config: dict[str, object],
    ) -> tuple[tuple[InboundEvent, ...], Acknowledgement]:
        del config
        try:
            payload = json.loads(request.raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PayloadInvalid("verified payload must be a JSON object") from None
        if not isinstance(payload, dict):
            raise PayloadInvalid("verified payload must be a JSON object")
        object_name = str(payload.get("object") or "").strip().lower()
        if object_name not in {"page", "instagram"}:
            raise PayloadInvalid("Meta webhook object unsupported")
        raw_entries = payload.get("entry")
        if not isinstance(raw_entries, list):
            raise PayloadInvalid("Meta webhook entry must be a list")
        message_channel = (
            "facebook_messenger" if object_name == "page" else "instagram_dm"
        )
        comment_channel = (
            "facebook_comment" if object_name == "page" else "instagram_comment"
        )
        events: list[InboundEvent] = []
        for entry_index, raw_entry in enumerate(raw_entries):
            entry_locator = f"/entry/{entry_index}"
            if not isinstance(raw_entry, Mapping):
                events.append(
                    _record_only(
                        item=raw_entry,
                        scope="unknown",
                        locator=entry_locator,
                        reason_code="entry_object_invalid",
                    )
                )
                continue
            scope = _text(raw_entry.get("id")) or "unknown"
            raw_messages = raw_entry.get("messaging")
            if isinstance(raw_messages, list):
                for item_index, item in enumerate(raw_messages):
                    events.append(
                        _message_event(
                            item=item,
                            scope=scope,
                            channel=message_channel,
                            locator=f"{entry_locator}/messaging/{item_index}",
                        )
                    )
            raw_changes = raw_entry.get("changes")
            if isinstance(raw_changes, list):
                for change_index, change in enumerate(raw_changes):
                    events.append(
                        _comment_event(
                            change=change,
                            scope=scope,
                            channel=comment_channel,
                            locator=f"{entry_locator}/changes/{change_index}",
                        )
                    )
        if not events:
            events.append(
                _record_only(
                    item=payload,
                    scope="unknown",
                    locator="",
                    reason_code="batch_has_no_supported_events",
                )
            )
        identities = [event.provider_event_id for event in events]
        if len(identities) != len(set(identities)):
            raise PayloadInvalid(
                "one provider batch contains duplicate event identities"
            )
        return tuple(events), ACKNOWLEDGEMENT


HANDLER: Final[IngressHandler] = MetaSocialIngressHandler()


class MetaSocialConnector:
    """SPI plugin discovered from this distribution's entry point."""

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.INGRESS})

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        MANIFEST.require_declares(capability_id)
        return HANDLER

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        del config
        required = (WEBHOOK_SIGNING_SECRET, WEBHOOK_VERIFY_TOKEN)
        if any(_material(secrets, name) is None for name in required):
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = MetaSocialConnector()

__all__ = ["MANIFEST", "PLUGIN", "MetaSocialConnector"]
