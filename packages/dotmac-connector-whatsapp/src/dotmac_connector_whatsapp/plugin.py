"""Stateless ingress, catalogue and delivery translation for Meta WhatsApp
Cloud API.

Three modes over one provider: INGRESS verifies and normalizes webhook bytes,
POLL reads the approved message-template catalogue, and DELIVERY sends text,
template and media commands the product has already decided on.

The two send-side gates that run BEFORE any wire call live in their own
modules: `catalogue` (is this template approved, and does its arity match) and
`media` (does this attachment fit the provider's type, size, caption and
filename rules). Both exist because the alternative is paying a provider round
trip for a refusal the connector could already make.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping
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
    InboundEvent,
    IngressHandler,
    IngressRequest,
    PollHandler,
    SecretBindingDeclaration,
    SpiRange,
    VerificationResult,
)

from dotmac_connector_whatsapp.catalogue import (
    DEFAULT_TEMPLATE_PAGE_SIZE,
    MAX_TEMPLATE_CACHE_TTL_SECONDS,
    TEMPLATE_READ_CAPABILITY_ID,
    TemplateCatalogueCache,
    TemplateCatalogueFailure,
    WhatsAppTemplateCatalogueHandler,
    ordered_template_parameters,
    require_sendable_template,
    require_waba_id,
    resolve_cache_ttl_seconds,
    resolve_page_size,
)
from dotmac_connector_whatsapp.media import (
    MEDIA_LIMITS_SCHEMA,
    MediaLimits,
    normalized_mime_type,
    require_supported_attachment,
)
from dotmac_connector_whatsapp.wire import (
    CHANNEL,
    GRAPH_HOST,
    PROVIDER,
    DeliveryContractError,
    MediaUploadFailure,
    graph_client,
    request_failure,
    retry_after_seconds,
)

CONNECTOR_KEY: Final = "meta_whatsapp"
CAPABILITY_ID: Final = "messaging.receive.v1"
SEND_CAPABILITY_ID: Final = "messaging.send.v1"
VERSION: Final = "0.1.0a3"
SIGNATURE_HEADER: Final = "x-hub-signature-256"
WEBHOOK_SIGNING_SECRET: Final = "webhook_signing_secret"
WEBHOOK_SIGNING_PREVIOUS_SECRET: Final = "webhook_signing_previous_secret"
WEBHOOK_VERIFY_TOKEN: Final = "webhook_verify_token"  # nosec B105
ACCESS_TOKEN: Final = "access_token"  # nosec B105
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
INGRESS_ONLY_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
}

DELIVERY_CONFIG_PROPERTIES: Final[dict[str, object]] = {
    "phone_number_id": {
        "type": "string",
        "pattern": r"^[0-9]{1,40}$",
    },
    # Exact and explicit: an API version is a compatibility decision, not a
    # connector default that silently ages into an unsupported endpoint.
    "graph_api_version": {
        "type": "string",
        "pattern": r"^v[0-9]{1,2}\.[0-9]+$",
    },
    "timeout_seconds": {
        "type": "number",
        "minimum": 1,
        "maximum": 60,
    },
}

# The catalogue knobs. `waba_id` is the account whose approved templates govern
# a send: it is REQUIRED on a send binding rather than optional, because the
# pre-flight gate has no fail-open branch to fall back to and an installation
# that cannot name its WABA cannot be told whether a template is approved.
WABA_CONFIG_PROPERTY: Final[dict[str, object]] = {
    "waba_id": {"type": "string", "pattern": r"^[0-9]{1,40}$"},
}
TEMPLATE_CONFIG_PROPERTIES: Final[dict[str, object]] = {
    # Sub's proven 300 s, as a knob. 0 disables reuse: every send re-reads.
    "template_cache_ttl_seconds": {
        "type": "integer",
        "minimum": 0,
        "maximum": MAX_TEMPLATE_CACHE_TTL_SECONDS,
    },
    "template_page_size": {
        "type": "integer",
        "minimum": 1,
        "maximum": DEFAULT_TEMPLATE_PAGE_SIZE,
    },
}

RECEIVE_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": DELIVERY_CONFIG_PROPERTIES,
}

SEND_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "phone_number_id",
        "graph_api_version",
        "timeout_seconds",
        "waba_id",
    ],
    "properties": DELIVERY_CONFIG_PROPERTIES
    | WABA_CONFIG_PROPERTY
    | TEMPLATE_CONFIG_PROPERTIES
    | {"media_limits": MEDIA_LIMITS_SCHEMA},
}

TEMPLATE_READ_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["waba_id", "graph_api_version", "timeout_seconds"],
    "properties": {
        "graph_api_version": DELIVERY_CONFIG_PROPERTIES["graph_api_version"],
        "timeout_seconds": DELIVERY_CONFIG_PROPERTIES["timeout_seconds"],
    }
    | WABA_CONFIG_PROPERTY
    | {"template_page_size": TEMPLATE_CONFIG_PROPERTIES["template_page_size"]},
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

# Exact a2 contract. It remains discoverable so an installed ingress-only
# revision can be adopted deliberately rather than becoming an unknown digest.
INGRESS_MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version="0.1.0a2",
    spi_range=SpiRange.parse(">=1.3,<2.0"),
    capabilities=(
        CapabilityDeclaration(
            capability_id=CAPABILITY_ID,
            config_schema=INGRESS_ONLY_CONFIG_SCHEMA,
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

MANIFEST: Final = ConnectorManifest(
    connector_key=CONNECTOR_KEY,
    version=VERSION,
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
        CapabilityDeclaration(
            capability_id=TEMPLATE_READ_CAPABILITY_ID,
            config_schema=TEMPLATE_READ_CONFIG_SCHEMA,
            modes=frozenset({ConnectorMode.POLL}),
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
        SecretBindingDeclaration(
            name=ACCESS_TOKEN,
            required=False,
            description=(
                "Graph API access token; required when messaging.send.v1 or "
                "messaging.templates.read.v1 is bound."
            ),
        ),
    ),
    egress=EgressDeclaration(hosts=(GRAPH_HOST,)),
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


@dataclass(frozen=True, slots=True)
class _DeliveryConfig:
    phone_number_id: str
    graph_api_version: str
    timeout_seconds: float


def _delivery_config(config: Mapping[str, object]) -> _DeliveryConfig:
    phone_number_id = config.get("phone_number_id")
    graph_api_version = config.get("graph_api_version")
    timeout_seconds = config.get("timeout_seconds")
    if (
        not isinstance(phone_number_id, str)
        or re.fullmatch(r"[0-9]{1,40}", phone_number_id) is None
    ):
        raise DeliveryContractError("phone_number_id_invalid")
    if (
        not isinstance(graph_api_version, str)
        or re.fullmatch(r"v[0-9]{1,2}\.[0-9]+", graph_api_version) is None
    ):
        raise DeliveryContractError("graph_api_version_invalid")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not 1 <= float(timeout_seconds) <= 60
    ):
        raise DeliveryContractError("timeout_seconds_invalid")
    return _DeliveryConfig(
        phone_number_id=phone_number_id,
        graph_api_version=graph_api_version,
        timeout_seconds=float(timeout_seconds),
    )


def _access_token(secrets: Mapping[str, object]) -> str:
    value = secrets.get(ACCESS_TOKEN)
    if not isinstance(value, str) or not value:
        raise DeliveryContractError("access_token_unavailable")
    return value


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryContractError(code)
    return value.strip()


def _text_payload(params: Mapping[str, object]) -> dict[str, object]:
    return {
        "messaging_product": "whatsapp",
        "to": _required_text(params.get("recipient"), "recipient_required"),
        "type": "text",
        "text": {"body": _required_text(params.get("body"), "body_required")},
    }


def _template_payload(params: Mapping[str, object]) -> dict[str, object]:
    recipient = _required_text(params.get("recipient"), "recipient_required")
    name = _required_text(params.get("template_name"), "template_name_required")
    # Preserve Sub's production behaviour: the provider's default template
    # locale is English when the product command does not name one.
    language_value = params.get("language", "en")
    language = _required_text(language_value or "en", "template_language_required")
    variables = params.get("variables", {})
    components = params.get("components", [])
    if not isinstance(variables, Mapping):
        raise DeliveryContractError("template_variables_invalid")
    if not isinstance(components, list) or any(
        not isinstance(item, Mapping) for item in components
    ):
        raise DeliveryContractError("template_components_invalid")
    template: dict[str, object] = {
        "name": name,
        "language": {"code": language},
    }
    parameters = ordered_template_parameters(variables)
    if components:
        template["components"] = [dict(item) for item in components]
    elif parameters:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in parameters],
            }
        ]
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": template,
    }


def _media_payload(
    params: Mapping[str, object],
    *,
    limits: MediaLimits,
    uploaded_media_id: str | None = None,
    content_length: int | None = None,
) -> dict[str, object]:
    recipient = _required_text(params.get("recipient"), "recipient_required")
    media_type = _required_text(params.get("media_type"), "media_type_required").lower()
    caption = params.get("caption")
    filename = params.get("filename")
    declared_type = params.get("content_type")
    require_supported_attachment(
        media_type=media_type,
        content_type=(
            declared_type
            if isinstance(declared_type, str) and declared_type.strip()
            else None
        ),
        content_length=content_length,
        caption=caption,
        filename=filename,
        limits=limits,
    )
    media_id_value = params.get("media_id")
    link_value = params.get("link")
    media_id = uploaded_media_id or (
        media_id_value if isinstance(media_id_value, str) else None
    )
    link = link_value if isinstance(link_value, str) else None
    media: dict[str, object]
    if media_id and media_id.strip():
        media = {"id": media_id.strip()}
    elif link and link.strip():
        media = {"link": link.strip()}
    else:
        raise DeliveryContractError("media_reference_required")
    # The lengths were checked above and refused, not trimmed, so what the
    # product wrote is what goes on the wire.
    if isinstance(caption, str) and caption:
        media["caption"] = caption
    if isinstance(filename, str) and filename:
        media["filename"] = filename
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": media_type,
        media_type: media,
    }


def _response_outcome(response: httpx.Response) -> Outcome:
    status = response.status_code
    if status == 429:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_rate_limited",
            retry_after_seconds=retry_after_seconds(response),
            provider_status_code=status,
        )
    if status >= 500:
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_retryable_response",
            provider_status_code=status,
        )
    if status < 200 or status >= 300:
        return Outcome(
            status=OutcomeStatus.TERMINAL,
            error_code="provider_rejected_message",
            provider_status_code=status,
        )
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    reference: object = None
    if isinstance(body, Mapping):
        messages = body.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
            reference = messages[0].get("id")
    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 500
    ):
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


@dataclass(frozen=True, slots=True)
class WhatsAppDeliveryHandler:
    """Provider I/O only; the engine owns claims, retries and persistence.

    The `catalogue_cache` it is handed is the connector's, not its own: a
    handler is constructed per dispatch, so a cache owned here would be cold on
    every send and the TTL would mean nothing.
    """

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    catalogue_cache: TemplateCatalogueCache = field(
        default_factory=TemplateCatalogueCache, repr=False
    )
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def __call__(self, request: DispatchRequest) -> Outcome:
        if request.capability_id != SEND_CAPABILITY_ID:
            return Outcome(
                status=OutcomeStatus.TERMINAL,
                error_code="capability_unsupported",
            )
        try:
            config = _delivery_config(request.config)
            token = _access_token(request.secrets)
            action = _required_text(request.payload.get("action"), "action_required")
            params = request.payload.get("params")
            if not isinstance(params, Mapping):
                raise DeliveryContractError("params_invalid")
            if action == "send_text":
                payload = _text_payload(params)
            elif action == "send_template":
                # The gate, before the wire call. An unapproved template or an
                # arity the catalogue does not describe never reaches Meta.
                require_sendable_template(
                    params,
                    config=request.config,
                    token=token,
                    transport=self.transport,
                    cache=self.catalogue_cache,
                    timeout_seconds=config.timeout_seconds,
                    graph_api_version=config.graph_api_version,
                    clock=self.clock,
                )
                payload = _template_payload(params)
            elif action == "send_media":
                payload = self._media_payload(
                    params,
                    config=config,
                    token=token,
                    request_config=request.config,
                )
            else:
                raise DeliveryContractError("action_unsupported")
        except (MediaUploadFailure, TemplateCatalogueFailure) as exc:
            return exc.outcome
        except DeliveryContractError as exc:
            return Outcome(
                status=OutcomeStatus.TERMINAL,
                error_code=exc.code,
                error_detail=exc.detail,
            )

        try:
            with graph_client(
                timeout_seconds=config.timeout_seconds, transport=self.transport
            ) as client:
                response = client.post(
                    f"/{config.graph_api_version}/{config.phone_number_id}/messages",
                    json=payload,
                    headers={"authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            return request_failure(exc)
        return _response_outcome(response)

    def _media_payload(
        self,
        params: Mapping[str, object],
        *,
        config: _DeliveryConfig,
        token: str,
        request_config: Mapping[str, object],
    ) -> dict[str, object]:
        limits = MediaLimits.resolve(request_config)
        content_base64 = params.get("content_base64")
        media_id = params.get("media_id")
        link = params.get("link")
        if not (
            isinstance(content_base64, str)
            and content_base64
            and not (isinstance(media_id, str) and media_id.strip())
            and not (isinstance(link, str) and link.strip())
        ):
            # A reference the connector never held the bytes of: type, caption
            # and filename are still checked; size is not knowable and is not
            # pretended to be.
            return _media_payload(params, limits=limits)
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError):
            raise DeliveryContractError("media_content_invalid") from None
        # Sub defaulted the upload's declared type to `application/octet-stream`
        # and its filename to `attachment`. The filename default stays; the type
        # default does NOT, because Meta accepts no such type for any media kind
        # and the upload it produced was always going to be rejected. Requiring
        # a real, supported type turns that provider round trip into a local
        # refusal — which is the whole point of this gate.
        raw_content_type = params.get("content_type")
        if not isinstance(raw_content_type, str) or not raw_content_type.strip():
            raise DeliveryContractError("media_content_type_required")
        media_type = _required_text(
            params.get("media_type"), "media_type_required"
        ).lower()
        require_supported_attachment(
            media_type=media_type,
            content_type=raw_content_type,
            content_length=len(content),
            caption=params.get("caption"),
            filename=params.get("filename"),
            limits=limits,
        )
        filename = _required_text(params.get("filename") or "attachment", "")
        content_type = normalized_mime_type(raw_content_type)
        try:
            with graph_client(
                timeout_seconds=config.timeout_seconds, transport=self.transport
            ) as client:
                response = client.post(
                    f"/{config.graph_api_version}/{config.phone_number_id}/media",
                    headers={"authorization": f"Bearer {token}"},
                    data={"messaging_product": "whatsapp", "type": content_type},
                    files={"file": (filename, content, content_type)},
                )
        except httpx.RequestError as exc:
            upload_outcome = request_failure(exc)
            raise MediaUploadFailure(
                Outcome(
                    status=upload_outcome.status,
                    error_code=(
                        "media_upload_connect_failed"
                        if upload_outcome.status is OutcomeStatus.RETRYABLE
                        else "media_upload_outcome_ambiguous"
                    ),
                )
            ) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise MediaUploadFailure(
                Outcome(
                    status=OutcomeStatus.RETRYABLE,
                    error_code="media_upload_retryable_response",
                    retry_after_seconds=retry_after_seconds(response),
                    provider_status_code=response.status_code,
                )
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise MediaUploadFailure(
                Outcome(
                    status=OutcomeStatus.TERMINAL,
                    error_code="media_upload_rejected",
                    provider_status_code=response.status_code,
                )
            )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = None
        uploaded = body.get("id") if isinstance(body, Mapping) else None
        if not isinstance(uploaded, str) or not uploaded.strip():
            raise MediaUploadFailure(
                Outcome(
                    status=OutcomeStatus.RECONCILIATION_REQUIRED,
                    error_code="media_upload_receipt_missing",
                    provider_status_code=response.status_code,
                )
            )
        return _media_payload(
            params,
            limits=limits,
            uploaded_media_id=uploaded,
            content_length=len(content),
        )


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


INGRESS_HANDLER: Final[IngressHandler] = WhatsAppIngressHandler()


@dataclass(frozen=True, slots=True)
class WhatsAppConnector:
    """SPI plugin object discovered from package metadata.

    The catalogue cache lives HERE — one per plugin object, and therefore one
    per process in a deployment, since discovery constructs the plugin once.
    Handlers are constructed per call and are handed the cache rather than
    owning one.
    """

    transport: httpx.BaseTransport | None = field(default=None, repr=False)
    catalogue_cache: TemplateCatalogueCache = field(
        default_factory=TemplateCatalogueCache, repr=False
    )
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return (HISTORICAL_MANIFEST, INGRESS_MANIFEST)

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset(
            {ConnectorMode.INGRESS, ConnectorMode.POLL, ConnectorMode.DELIVERY}
        )

    def ingress_handler_for(self, capability_id: str) -> IngressHandler:
        MANIFEST.require_declares(capability_id)
        if capability_id != CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not an ingress capability")
        return INGRESS_HANDLER

    def handler_for(self, capability_id: str) -> WhatsAppDeliveryHandler:
        MANIFEST.require_declares(capability_id)
        if capability_id != SEND_CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not a delivery capability")
        return WhatsAppDeliveryHandler(self.transport, self.catalogue_cache, self.clock)

    def poll_handler_for(self, capability_id: str) -> PollHandler:
        MANIFEST.require_declares(capability_id)
        if capability_id != TEMPLATE_READ_CAPABILITY_ID:
            raise ValueError(f"{capability_id!r} is not a poll capability")
        return WhatsAppTemplateCatalogueHandler(self.transport)

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
        if any(name in config for name in DELIVERY_CONFIG_PROPERTIES):
            try:
                _delivery_config(config)
                _access_token(secrets)
            except DeliveryContractError:
                return (Diagnostic(ok=False, code="delivery_configuration_invalid"),)
        # A template-bearing configuration is refused at ACTIVATION rather than
        # at the first send: an installation that cannot name its WABA or whose
        # limits are out of range would otherwise look enabled and refuse every
        # template message it was given.
        if any(
            name in config
            for name in (*WABA_CONFIG_PROPERTY, *TEMPLATE_CONFIG_PROPERTIES)
        ):
            try:
                require_waba_id(config)
                resolve_cache_ttl_seconds(config)
                resolve_page_size(config)
                _access_token(secrets)
            except DeliveryContractError:
                return (Diagnostic(ok=False, code="template_configuration_invalid"),)
        if "media_limits" in config:
            try:
                MediaLimits.resolve(config)
            except DeliveryContractError:
                return (Diagnostic(ok=False, code="media_limits_invalid"),)
        return ()


PLUGIN: Final = WhatsAppConnector()

__all__ = [
    "MANIFEST",
    "PLUGIN",
    "WhatsAppConnector",
    "WhatsAppDeliveryHandler",
    "WhatsAppTemplateCatalogueHandler",
]
