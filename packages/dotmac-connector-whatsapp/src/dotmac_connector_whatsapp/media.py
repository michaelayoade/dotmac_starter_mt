"""Attachment mapping: which media types the provider accepts, how large, and
which of caption/filename each one carries.

## What was ported and what was missing

Sub's `whatsapp_runtime.build_media_payload` is the qualifying source for the
per-type CAPTION and FILENAME rules, and its
`team_inbox_media._normalized_upload` is the source for MIME normalization
(``split(";")[0].strip().lower()``). Both come across unchanged in meaning.

Two things were NOT in Sub and are added here rather than ported:

* **A per-type size table.** Sub enforces one flat
  ``MAX_OUTBOUND_ATTACHMENT_BYTES = 10 * 1024 * 1024`` at upload-staging time,
  and `build_media_payload`/`send_media_message` check no size at all. That
  ceiling is a PRODUCT policy about what an operator may stage; it is not the
  provider's contract, and it stays in Sub. Meta's limits differ per type and
  are the connector's business.
* **A MIME-to-media-type consistency check.** Sub passes `asset_type` straight
  through as `media_type` and validates the MIME against one flat allowlist
  that contains types Meta does not accept for any type (``image/gif``,
  ``audio/wav``, ``video/quicktime``, ``application/zip``, ``text/csv``). Those
  files are uploaded today and rejected by the provider.

## Why this is a pre-flight refusal and not a provider round trip

Meta rejects an oversize or wrongly-typed attachment AFTER the upload has been
streamed to it. Discovering that at the provider costs a full body upload, an
attempt row, and a dead-letter that reads like a transport fault. Every rule
below is a fact the connector already holds before the first byte leaves, so it
is checked here and returned as a typed terminal outcome.

## What is a knob and what is not

Per-type SIZE limits, the caption length and the filename length are
configuration, with the documented defaults in this module. They differ by API
version and by what an account is provisioned for, so an operator must be able
to state them — and the schema's ``maximum`` is the provider's own number, so
a configuration may only NARROW a limit. Widening one would not make Meta
accept the file.

The supported MIME SETS are deliberately NOT configuration, for the same
reason: widening a set locally would only move the rejection back to the
provider and reintroduce the round trip this module exists to remove. A new
provider MIME type arrives as a reviewed connector release.

## What this module does NOT know

Size can only be checked where the connector holds the bytes — the
`content_base64` upload path. A `link` or a pre-uploaded `media_id` is a
reference whose bytes the connector has never seen, and this module says so
rather than inventing a check that would pass for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from dotmac_connector_whatsapp.wire import DeliveryContractError

__all__ = [
    "CAPTIONABLE_MEDIA_TYPES",
    "DEFAULT_MAX_CAPTION_CHARACTERS",
    "DEFAULT_MAX_FILENAME_CHARACTERS",
    "DEFAULT_MEDIA_BYTE_LIMITS",
    "FILENAMED_MEDIA_TYPES",
    "MEDIA_LIMITS_SCHEMA",
    "SUPPORTED_MEDIA_MIME_TYPES",
    "SUPPORTED_OUTBOUND_MEDIA_TYPES",
    "MediaLimits",
    "normalized_mime_type",
    "require_supported_attachment",
]

#: Outbound media types this connector translates — Sub's exact set
#: (`whatsapp_runtime.py` ``{"image", "document", "audio", "video"}``). Sticker
#: is deliberately absent: it is observed inbound only, and no product command
#: in the qualifying source sends one, so declaring it would be surface with no
#: source behind it.
SUPPORTED_OUTBOUND_MEDIA_TYPES: Final[tuple[str, ...]] = (
    "image",
    "document",
    "audio",
    "video",
)

#: Meta's documented Cloud API media limits, in bytes, as the DEFAULTS an
#: operator may narrow per binding.
DEFAULT_MEDIA_BYTE_LIMITS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "image": 5 * 1024 * 1024,
        "document": 100 * 1024 * 1024,
        "audio": 16 * 1024 * 1024,
        "video": 16 * 1024 * 1024,
    }
)

#: The provider contract, per type. Not configuration — see the module
#: docstring.
SUPPORTED_MEDIA_MIME_TYPES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "image": frozenset({"image/jpeg", "image/png"}),
        "audio": frozenset(
            {
                "audio/aac",
                "audio/amr",
                "audio/mpeg",
                "audio/mp4",
                "audio/ogg",
            }
        ),
        "video": frozenset({"video/3gp", "video/3gpp", "video/mp4"}),
        "document": frozenset(
            {
                "application/msword",
                "application/pdf",
                "application/vnd.ms-excel",
                "application/vnd.ms-powerpoint",
                (
                    "application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"
                ),
                (
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                (
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"
                ),
                "text/plain",
            }
        ),
    }
)

#: A caption is carried by these types only — Sub's exact rule. Audio has
#: nowhere to render one.
CAPTIONABLE_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {"document", "image", "video"}
)
#: A filename is a document-only field — Sub's exact rule.
FILENAMED_MEDIA_TYPES: Final[frozenset[str]] = frozenset({"document"})

#: Sub truncated to these two numbers (``caption[:1024]``, ``filename[:255]``).
#: They are kept as the LIMITS; the truncation is not — see
#: :func:`require_supported_attachment`.
DEFAULT_MAX_CAPTION_CHARACTERS: Final = 1024
DEFAULT_MAX_FILENAME_CHARACTERS: Final = 255

#: The JSON-schema fragment for the `media_limits` configuration object, built
#: from the same defaults the dataclass falls back to, so the schema and the
#: runtime cannot drift into disagreeing about what is permissible.
MEDIA_LIMITS_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        f"{media_type}_bytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": DEFAULT_MEDIA_BYTE_LIMITS[media_type],
        }
        for media_type in SUPPORTED_OUTBOUND_MEDIA_TYPES
    }
    | {
        "caption_characters": {
            "type": "integer",
            "minimum": 1,
            "maximum": DEFAULT_MAX_CAPTION_CHARACTERS,
        },
        "filename_characters": {
            "type": "integer",
            "minimum": 1,
            "maximum": DEFAULT_MAX_FILENAME_CHARACTERS,
        },
    },
}


def normalized_mime_type(value: str) -> str:
    """``Text/Plain; charset=utf-8`` -> ``text/plain``.

    Ported from Sub's `team_inbox_media` normalization. Parameters are dropped
    before comparison because they do not change which media type the provider
    is being handed, and a charset suffix would otherwise turn an accepted type
    into a refusal — Sub has a named test for exactly that
    (`test_content_type_parameters_do_not_defeat_the_allowlist`).
    """
    return value.split(";", 1)[0].strip().casefold()


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Resolved per-binding attachment limits."""

    byte_limits: Mapping[str, int]
    caption_characters: int
    filename_characters: int

    @classmethod
    def resolve(cls, config: Mapping[str, object]) -> MediaLimits:
        raw = config.get("media_limits")
        if raw is not None and not isinstance(raw, Mapping):
            raise DeliveryContractError("media_limits_invalid")
        declared: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        byte_limits = {
            media_type: _narrowed(
                declared.get(f"{media_type}_bytes"),
                ceiling=DEFAULT_MEDIA_BYTE_LIMITS[media_type],
            )
            for media_type in SUPPORTED_OUTBOUND_MEDIA_TYPES
        }
        return cls(
            byte_limits=MappingProxyType(byte_limits),
            caption_characters=_narrowed(
                declared.get("caption_characters"),
                ceiling=DEFAULT_MAX_CAPTION_CHARACTERS,
            ),
            filename_characters=_narrowed(
                declared.get("filename_characters"),
                ceiling=DEFAULT_MAX_FILENAME_CHARACTERS,
            ),
        )


def _narrowed(value: object, *, ceiling: int) -> int:
    """A configured limit, which may only be at or below the provider's own.

    Absent means the documented default, which IS the ceiling. A value above it
    is refused rather than clamped: an operator who wrote 200 MB for an image
    believes 200 MB will be sent, and silently honouring 5 MB would make the
    configuration a lie.
    """
    if value is None:
        return ceiling
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeliveryContractError("media_limits_invalid")
    if not 1 <= value <= ceiling:
        raise DeliveryContractError("media_limits_invalid")
    return value


def require_supported_attachment(
    *,
    media_type: str,
    content_type: str | None,
    content_length: int | None,
    caption: object,
    filename: object,
    limits: MediaLimits,
) -> None:
    """Refuse, before any wire call, what the provider would refuse anyway.

    :param content_type: the declared MIME type, or ``None`` when the product
        named none. It is checked whenever present; the UPLOAD path makes it
        mandatory separately, because that path states a type to the provider.
    :param content_length: decoded byte length, or ``None`` for a `link` or
        `media_id` reference whose bytes the connector has never held.

    ## Refusal, not truncation

    Sub trimmed an over-long caption (``caption[:1024]``) and filename
    (``filename[:255]``) and silently dropped a caption on a type that cannot
    carry one. Both are the connector editing PRODUCT CONTENT to fit a provider
    constraint — a decision that belongs to whoever wrote the message, not to
    the wire adapter. The limits are kept; the edit is replaced by a typed
    terminal refusal the product can act on.
    """
    if media_type not in SUPPORTED_OUTBOUND_MEDIA_TYPES:
        raise DeliveryContractError("media_type_unsupported")
    if content_type is not None:
        normalized = normalized_mime_type(content_type)
        if normalized not in SUPPORTED_MEDIA_MIME_TYPES[media_type]:
            raise DeliveryContractError("media_content_type_unsupported")
    if content_length is not None:
        if content_length <= 0:
            raise DeliveryContractError("media_content_empty")
        if content_length > limits.byte_limits[media_type]:
            raise DeliveryContractError("media_content_too_large")
    if isinstance(caption, str) and caption:
        if media_type not in CAPTIONABLE_MEDIA_TYPES:
            raise DeliveryContractError("media_caption_unsupported")
        if len(caption) > limits.caption_characters:
            raise DeliveryContractError("media_caption_too_long")
    if isinstance(filename, str) and filename:
        if media_type not in FILENAMED_MEDIA_TYPES:
            raise DeliveryContractError("media_filename_unsupported")
        if len(filename) > limits.filename_characters:
            raise DeliveryContractError("media_filename_too_long")
