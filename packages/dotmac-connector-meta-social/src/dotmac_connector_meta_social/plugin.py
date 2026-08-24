"""Stateless Meta Social webhook authentication and Graph wire translation.

Two capabilities, two modes, one provider edge:

* ``messaging.receive.v1`` (INGRESS) verifies the exact request bytes and
  normalizes Facebook Messenger, Instagram DM, Facebook comment and Instagram
  comment batches into provider-neutral observations.
* ``messaging.send.v1`` (DELIVERY) translates a product-decided outbound
  command into the Graph call that carries it, performs that one call, and
  classifies what came back.

The DELIVERY half decides NOTHING about whether a reply may be sent. Whether a
messaging window is open, whether this recipient may be answered, which draft
wins and what the reply says are the product's decisions, taken before the
command reaches this package. The connector translates, calls, and classifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import (
    Acknowledgement,
    CapabilityDeclaration,
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    DispatchRequest,
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
# The outbound name is NOT minted here. `messaging.send.v1` is the declaring
# application's existing vocabulary: Sub binds it for exactly these Meta
# accounts (`META_SOCIAL_SEND_CAPABILITY`), and the sibling `meta_whatsapp`
# connector already implements the same id in DELIVERY mode. A second name for
# the same meaning would fork the capability registry rather than extend it.
SEND_CAPABILITY_ID: Final = "messaging.send.v1"
PROVIDER: Final = "meta_cloud_api"
VERSION: Final = "0.1.0a1"
SIGNATURE_HEADER: Final = "x-hub-signature-256"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
WEBHOOK_VERIFY_TOKEN: Final = "webhook_verify_token"  # nosec B105
# Sub's exact binding names, kept so a cutover rebinds references rather than
# renaming them. Each names a PURPOSE; none is or contains a value.
FACEBOOK_PAGE_ACCESS_TOKEN: Final = "facebook_page_access_token"  # nosec B105
INSTAGRAM_LOGIN_ACCESS_TOKEN: Final = "instagram_login_access_token"  # nosec B105
META_OAUTH_ACCESS_TOKEN: Final = "meta_oauth_access_token"  # nosec B105
GRAPH_HOST: Final = "graph.facebook.com"
INSTAGRAM_GRAPH_HOST: Final = "graph.instagram.com"
AUTH_MODE_OAUTH: Final = "oauth"
AUTH_MODE_INDIVIDUAL: Final = "individual"
FACEBOOK_MESSENGER: Final = "facebook_messenger"
INSTAGRAM_DM: Final = "instagram_dm"
FACEBOOK_COMMENT: Final = "facebook_comment"
INSTAGRAM_COMMENT: Final = "instagram_comment"
#: The same four channel names the INGRESS half emits, so an outbound command
#: and the observation it answers speak one vocabulary.
DIRECT_CHANNELS: Final[frozenset[str]] = frozenset({FACEBOOK_MESSENGER, INSTAGRAM_DM})
COMMENT_CHANNELS: Final[frozenset[str]] = frozenset(
    {FACEBOOK_COMMENT, INSTAGRAM_COMMENT}
)
GRAPH_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"v[0-9]{1,2}\.[0-9]+")
PROVIDER_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.:-]{1,120}")
ACKNOWLEDGEMENT: Final = Acknowledgement(
    body=b'{"status":"ok"}', media_type="application/json"
)
CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}
DELIVERY_CONFIG_PROPERTIES: Final[dict[str, object]] = {
    # Exact and explicit, as on the WhatsApp connector: an API version is a
    # compatibility decision, not a connector default that silently ages into
    # an unsupported endpoint. Sub's `v21.0` fallback is deliberately dropped.
    "graph_api_version": {"type": "string", "pattern": r"^v[0-9]{1,2}\.[0-9]+$"},
    "auth_mode": {"type": "string", "enum": [AUTH_MODE_OAUTH, AUTH_MODE_INDIVIDUAL]},
    "facebook_page_id": {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,120}$"},
    "instagram_account_id": {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,120}$"},
    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 60},
}
RECEIVE_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": DELIVERY_CONFIG_PROPERTIES,
}
SEND_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["graph_api_version", "auth_mode", "timeout_seconds"],
    "properties": DELIVERY_CONFIG_PROPERTIES,
}

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
    # SPI 1.4 is the floor the DELIVERY slice actually needs: only from 1.4
    # does `CapabilityDeclaration.modes` exist, and without it an engine would
    # ask this plugin for an INGRESS handler for the send capability.
    spi_range=SpiRange.parse(">=1.4,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=RECEIVE_CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.INGRESS}),
        ),
        CapabilityDeclaration(
            capability_id=SEND_CAPABILITY_ID,
            config_schema=SEND_CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.DELIVERY}),
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
        SecretBindingDeclaration(
            name=FACEBOOK_PAGE_ACCESS_TOKEN,
            required=False,
            description=(
                "Page Graph access material; required when messaging.send.v1 "
                "is bound in individual auth mode."
            ),
        ),
        SecretBindingDeclaration(
            name=INSTAGRAM_LOGIN_ACCESS_TOKEN,
            required=False,
            description=(
                "Instagram Login Graph access material; required when "
                "messaging.send.v1 is bound in individual auth mode."
            ),
        ),
        SecretBindingDeclaration(
            name=META_OAUTH_ACCESS_TOKEN,
            required=False,
            description=(
                "Shared Meta OAuth Graph access material; required when "
                "messaging.send.v1 is bound in oauth auth mode."
            ),
        ),
    ),
    egress=EgressDeclaration(hosts=(GRAPH_HOST, INSTAGRAM_GRAPH_HOST)),
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


# ── DELIVERY: one Graph call per product-decided command ────────────────────
#
# Everything below translates a command the PRODUCT already decided to send.
# The connector never asks whether a reply is allowed: no messaging-window
# arithmetic, no last-inbound lookup, no draft selection, no recipient policy.
# Those answers live in the product that owns the exchange, and they have been
# given before a command reaches here. What the connector owns is the wire.


class DeliveryContractError(ValueError):
    """A product command cannot be translated into the provider contract."""

    def __init__(self, code: str) -> None:
        self.code = code if code.isidentifier() else "delivery_contract_invalid"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class _DeliveryConfig:
    graph_api_version: str
    auth_mode: str
    facebook_page_id: str | None
    instagram_account_id: str | None
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class _GraphCall:
    """One fully translated provider call, before any I/O happens."""

    channel: str
    host: str
    path: str
    payload: dict[str, object]


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryContractError(code)
    return value.strip()


def _provider_id(value: object, code: str) -> str:
    candidate = _required_text(value, code)
    if PROVIDER_ID_RE.fullmatch(candidate) is None:
        raise DeliveryContractError(code)
    return candidate


def _optional_provider_id(value: object, code: str) -> str | None:
    if value is None:
        return None
    return _provider_id(value, code)


def _delivery_config(config: Mapping[str, object]) -> _DeliveryConfig:
    graph_api_version = config.get("graph_api_version")
    auth_mode = config.get("auth_mode")
    timeout_seconds = config.get("timeout_seconds")
    if (
        not isinstance(graph_api_version, str)
        or GRAPH_VERSION_RE.fullmatch(graph_api_version) is None
    ):
        raise DeliveryContractError("graph_api_version_invalid")
    # Sub normalized an unknown auth mode silently to `individual` on the send
    # path while its own `validate` reported `auth_mode_invalid`. The loud half
    # is the one that is right: a mistyped mode otherwise picks a different
    # credential and a different host without anyone being told.
    if auth_mode not in (AUTH_MODE_OAUTH, AUTH_MODE_INDIVIDUAL):
        raise DeliveryContractError("auth_mode_invalid")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not 1 <= float(timeout_seconds) <= 60
    ):
        raise DeliveryContractError("timeout_seconds_invalid")
    return _DeliveryConfig(
        graph_api_version=graph_api_version,
        auth_mode=auth_mode,
        facebook_page_id=_optional_provider_id(
            config.get("facebook_page_id"), "facebook_page_id_invalid"
        ),
        instagram_account_id=_optional_provider_id(
            config.get("instagram_account_id"), "instagram_account_id_invalid"
        ),
        timeout_seconds=float(timeout_seconds),
    )


def _credential_binding(config: _DeliveryConfig, channel: str) -> str:
    if config.auth_mode == AUTH_MODE_OAUTH:
        return META_OAUTH_ACCESS_TOKEN
    if channel in {FACEBOOK_MESSENGER, FACEBOOK_COMMENT}:
        return FACEBOOK_PAGE_ACCESS_TOKEN
    return INSTAGRAM_LOGIN_ACCESS_TOKEN


def _access_material(
    secrets: Mapping[str, object], config: _DeliveryConfig, channel: str
) -> str:
    value = _material(secrets, _credential_binding(config, channel))
    if value is None:
        raise DeliveryContractError("access_material_unavailable")
    return value


def _bound_account(config: _DeliveryConfig, channel: str) -> str:
    """The account this installation binds for `channel`.

    A binding check, not a product decision: the installation says which Page
    and which Instagram account it speaks for, and a command naming a different
    one is addressed to another installation.
    """
    account = (
        config.facebook_page_id
        if channel in {FACEBOOK_MESSENGER, FACEBOOK_COMMENT}
        else config.instagram_account_id
    )
    if account is None:
        raise DeliveryContractError("provider_account_not_bound")
    return account


def _require_bound_account(
    config: _DeliveryConfig, params: Mapping[str, object], channel: str
) -> str:
    bound = _bound_account(config, channel)
    requested = _provider_id(
        params.get("provider_account_id"), "provider_account_id_required"
    )
    if requested != bound:
        raise DeliveryContractError("provider_account_not_bound")
    return bound


def _channel(params: Mapping[str, object], allowed: frozenset[str]) -> str:
    channel = _required_text(params.get("channel"), "channel_required")
    if channel not in allowed:
        raise DeliveryContractError("channel_unsupported")
    return channel


def _direct_message_call(
    config: _DeliveryConfig, params: Mapping[str, object]
) -> _GraphCall:
    """Sub's exact per-channel Messenger/Instagram Direct wire shapes."""
    channel = _channel(params, DIRECT_CHANNELS)
    account = _require_bound_account(config, params, channel)
    recipient = _provider_id(params.get("recipient_id"), "recipient_required")
    body = _required_text(params.get("body"), "body_required")
    version = config.graph_api_version
    if channel == FACEBOOK_MESSENGER:
        return _GraphCall(
            channel=channel,
            host=GRAPH_HOST,
            path=f"/{version}/{account}/messages",
            payload={
                "recipient": {"id": recipient},
                "message": {"text": body},
                "messaging_type": "RESPONSE",
            },
        )
    if config.auth_mode == AUTH_MODE_OAUTH:
        return _GraphCall(
            channel=channel,
            host=GRAPH_HOST,
            path=f"/{version}/{account}/messages",
            payload={"recipient": {"id": recipient}, "message": {"text": body}},
        )
    # Instagram Login speaks for `me` and takes both objects as compact JSON
    # STRINGS. Sub's production runtime encodes them exactly this way; a
    # structured object here is silently rejected by the endpoint.
    return _GraphCall(
        channel=channel,
        host=INSTAGRAM_GRAPH_HOST,
        path=f"/{version}/me/messages",
        payload={
            "recipient": json.dumps({"id": recipient}, separators=(",", ":")),
            "message": json.dumps({"text": body}, separators=(",", ":")),
        },
    )


def _comment_reply_call(
    config: _DeliveryConfig, params: Mapping[str, object]
) -> _GraphCall:
    """Sub's exact public-reply wire shapes: `/comments` and `/replies`.

    The two edges differ and are not interchangeable — Facebook nests a reply
    under the parent comment's `comments` edge, Instagram under its `replies`
    edge. Both are addressed by the PARENT comment id the product supplies from
    the observation it is answering; the connector neither discovers nor
    validates which comment deserves an answer.
    """
    channel = _channel(params, COMMENT_CHANNELS)
    _require_bound_account(config, params, channel)
    parent = _provider_id(params.get("parent_comment_id"), "parent_comment_id_required")
    body = _required_text(params.get("body"), "body_required")
    edge = "comments" if channel == FACEBOOK_COMMENT else "replies"
    return _GraphCall(
        channel=channel,
        host=GRAPH_HOST,
        path=f"/{config.graph_api_version}/{parent}/{edge}",
        payload={"message": body},
    )


# ── Provider outcome classification ─────────────────────────────────────────
#
# Graph reports the SAME HTTP status for causes that need opposite handling: a
# rate limit and a policy refusal both arrive as 4xx. Classifying on status
# alone therefore dead-letters throttled traffic and retries refusals forever,
# so the numeric Graph error code is consulted FIRST and the HTTP status is
# only the fallback for codes this table does not name.

#: Documented Graph throttles. All are 4xx on the wire and all mean "later".
RATE_LIMIT_GRAPH_CODES: Final[frozenset[int]] = frozenset({4, 17, 32, 341, 613})
#: Graph's own transient failures ("unknown error", "service unavailable").
TRANSIENT_GRAPH_CODES: Final[frozenset[int]] = frozenset({1, 2})
#: Expired or invalid access material. Retrying the same call cannot fix it;
#: the installation needs new material, which is an operator's move.
CREDENTIAL_GRAPH_CODES: Final[frozenset[int]] = frozenset({190, 102})
#: Messaging-policy refusals. The provider is stating that this send is not
#: allowed — `2018278` is the closed messaging window, `2018108` the recipient
#: who is not accepting messages, `551` the recipient who is unavailable. All
#: are TERMINAL: a repeat of the identical call is refused identically. Whether
#: the product may try again in another form is the PRODUCT's decision.
POLICY_GRAPH_SUBCODES: Final[frozenset[int]] = frozenset({2018278, 2018108})
POLICY_GRAPH_CODES: Final[frozenset[int]] = frozenset({10, 551})
#: A parent object that is gone or was never addressable — the shape an invalid
#: parent comment id takes on the wire.
MISSING_OBJECT_GRAPH_CODES: Final[frozenset[int]] = frozenset({803})
MISSING_OBJECT_GRAPH_SUBCODES: Final[frozenset[int]] = frozenset({33})
#: A malformed request the provider will refuse identically every time.
INVALID_REQUEST_GRAPH_CODES: Final[frozenset[int]] = frozenset({100})


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value is None or not value.isdigit():
        return None
    return int(value)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _graph_error(body: object) -> tuple[int | None, int | None]:
    """The `(code, error_subcode)` pair, or `(None, None)` when absent.

    Only the two numbers cross the boundary. `error.message` is provider prose
    that can quote the outbound content back at us, and nothing downstream may
    branch on it, so it is never read.
    """
    if not isinstance(body, Mapping):
        return (None, None)
    error = body.get("error")
    if not isinstance(error, Mapping):
        return (None, None)
    return (_integer(error.get("code")), _integer(error.get("error_subcode")))


def _body(response: httpx.Response) -> object:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _error_outcome(
    *,
    status: int,
    code: int | None,
    subcode: int | None,
    retry_after: int | None,
) -> Outcome:
    if (subcode is not None and subcode in POLICY_GRAPH_SUBCODES) or (
        code is not None and code in POLICY_GRAPH_CODES
    ):
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_refused_by_messaging_policy",
            provider_status_code=status,
        )
    if code is not None and code in RATE_LIMIT_GRAPH_CODES:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_rate_limited",
            retry_after_seconds=retry_after,
            provider_status_code=status,
        )
    if code is not None and code in TRANSIENT_GRAPH_CODES:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_transient_error",
            retry_after_seconds=retry_after,
            provider_status_code=status,
        )
    if code is not None and (code in CREDENTIAL_GRAPH_CODES or 200 <= code <= 299):
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_authorization_rejected",
            provider_status_code=status,
        )
    if (code is not None and code in MISSING_OBJECT_GRAPH_CODES) or (
        subcode is not None and subcode in MISSING_OBJECT_GRAPH_SUBCODES
    ):
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_object_not_found",
            provider_status_code=status,
        )
    if code is not None and code in INVALID_REQUEST_GRAPH_CODES:
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_request_invalid",
            provider_status_code=status,
        )
    if status == 429:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_rate_limited",
            retry_after_seconds=retry_after,
            provider_status_code=status,
        )
    if status >= 500:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_retryable_response",
            retry_after_seconds=retry_after,
            provider_status_code=status,
        )
    return Outcome(
        status=OutcomeStatus.TERMINAL,
        error_code="provider_rejected_message",
        provider_status_code=status,
    )


def _provider_reference(body: object) -> str | None:
    """Sub's `_safe_receipt` identity, reduced to the one bounded field.

    Direct messages answer with `message_id`, comment replies with `id`. Both
    are the reference a later provider callback correlates against, which is
    exactly what `Outcome.provider_reference` is for. `recipient_id` is echoed
    input rather than new evidence and has no typed home, so it is not carried.
    """
    if not isinstance(body, Mapping):
        return None
    for key in ("message_id", "id"):
        candidate = body.get(key)
        if isinstance(candidate, str) and candidate.strip():
            reference = candidate.strip()
            return reference if len(reference) <= 500 else None
    return None


def _response_outcome(response: httpx.Response) -> Outcome:
    status = response.status_code
    body = _body(response)
    code, subcode = _graph_error(body)
    if code is not None or subcode is not None or not 200 <= status < 300:
        return _error_outcome(
            status=status,
            code=code,
            subcode=subcode,
            retry_after=_retry_after(response),
        )
    reference = _provider_reference(body)
    if reference is None:
        # A 2xx with no reference means the send may well have landed while we
        # cannot name what landed. That is neither a success to record nor a
        # failure to repeat.
        return Outcome(
            status=OutcomeStatus.RECONCILIATION_REQUIRED,
            error_code="provider_receipt_missing",
            provider_status_code=status,
        )
    return Outcome(
        status=OutcomeStatus.SUCCEEDED,
        provider_reference=reference,
        provider_status_code=status,
    )


def _request_failure(exc: httpx.RequestError) -> Outcome:
    """Whether the request could have landed decides the classification.

    A refused or timed-out CONNECTION never reached Meta, so repeating it is
    safe. Anything after the connection opened — a read timeout above all —
    may have been accepted with the answer lost on the way back. Sub mapped
    that whole family to `retryable`, which duplicates the send; here it is
    ambiguous, and the engine escalates instead of re-sending.
    """
    if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError):
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_connect_failed",
        )
    return Outcome(
        status=OutcomeStatus.RECONCILIATION_REQUIRED,
        error_code="provider_outcome_ambiguous",
    )


@dataclass(frozen=True, slots=True)
class MetaSocialDeliveryHandler:
    """Provider I/O only; the engine owns claims, retries and persistence."""

    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __call__(self, request: DispatchRequest) -> Outcome:
        if request.capability_id != SEND_CAPABILITY_ID:
            return Outcome(
                status=OutcomeStatus.TERMINAL,
                error_code="capability_unsupported",
            )
        try:
            config = _delivery_config(request.config)
            action = _required_text(request.payload.get("action"), "action_required")
            params = request.payload.get("params")
            if not isinstance(params, Mapping):
                raise DeliveryContractError("params_invalid")
            if action == "send_direct_message":
                call = _direct_message_call(config, params)
            elif action == "reply_to_comment":
                call = _comment_reply_call(config, params)
            else:
                raise DeliveryContractError("action_unsupported")
            token = _access_material(request.secrets, config, call.channel)
        except DeliveryContractError as exc:
            return Outcome(status=OutcomeStatus.TERMINAL, error_code=exc.code)

        try:
            with httpx.Client(
                base_url=f"https://{call.host}",
                timeout=config.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    call.path,
                    json=call.payload,
                    headers={"authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            return _request_failure(exc)
        return _response_outcome(response)


@dataclass(frozen=True, slots=True)
class MetaSocialConnector:
    """SPI plugin discovered from this distribution's entry point."""

    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.INGRESS, ConnectorMode.DELIVERY})

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        MANIFEST.require_declares(capability_id)
        if capability_id != CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not an ingress capability")
        return HANDLER

    def handler_for(self, capability_id: str) -> MetaSocialDeliveryHandler:
        MANIFEST.require_declares(capability_id)
        if capability_id != SEND_CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not a delivery capability")
        return MetaSocialDeliveryHandler(self.transport)

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        required = (WEBHOOK_SIGNING_SECRET, WEBHOOK_VERIFY_TOKEN)
        if any(_material(secrets, name) is None for name in required):
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        if any(name in config for name in DELIVERY_CONFIG_PROPERTIES):
            try:
                delivery = _delivery_config(config)
                for channel in (FACEBOOK_MESSENGER, INSTAGRAM_DM):
                    _bound_account(delivery, channel)
                    _access_material(secrets, delivery, channel)
            except DeliveryContractError:
                return (Diagnostic(ok=False, code="delivery_configuration_invalid"),)
        return ()


PLUGIN: Final = MetaSocialConnector()

__all__ = [
    "MANIFEST",
    "PLUGIN",
    "MetaSocialConnector",
    "MetaSocialDeliveryHandler",
]
