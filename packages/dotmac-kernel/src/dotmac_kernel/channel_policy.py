"""Which channels carry a class of message (ADR-0006 § 5c).

**Owner of:** *given this event or category, which channels does it go out on?*

## Why this is not a subsystem

ADR-0006 § 5c named channel policy as one of six owners and flagged it the
weakest, blocking it on its own dossier. That dossier
(`docs/inventories/channel-policy-sources.md`) found the unit was drawn one size
too large: Sub stores its whole policy as ONE setting — a JSON document with
`default` / `categories` / `events` — and resolves it through ordinary settings
precedence. The kernel already owns the resolver, the scope chain, ADR-0012
inheritance, change history and an admin surface.

So there is no table, no migration and no service here. What was missing is a
**typed reader** over a setting the product declares, and that is all this module
is.

## The document

    {"default":    ["email"],
     "categories": {"billing": ["email", "sms"]},
     "events":     {"invoice_overdue": ["sms"]}}

Resolution order, most specific first:

1. `events[<event>]`
2. `categories[<category>]`
3. `default`
4. the caller's own fallback

Sub has a fifth step ABOVE all of these — a legacy per-event setting
(`notification_event_<code>_channels`) that shadows the document, and which its
own docstring calls legacy. It is deliberately not ported: a second writer for
the same decision on day one is the parallel-authority problem the
source-of-truth standard exists to prevent.

## The product owns the domain and the vocabulary

`make_spec(domain=...)` builds the `SettingSpec`; the owning module declares that
domain on its manifest and registers the spec, exactly like any other setting.
The kernel neither invents a domain nor knows which channels exist — a channel is
an open registered string here as it is in `dotmac_kernel.consent`.

## Validation happens on WRITE

`validate_policy_document` is the spec's `validator`, so a malformed policy is
refused when an operator saves it rather than discovered when a send fails. Sub's
`ChannelPolicyDocument` is a `TypedDict` — a static hint with no runtime effect —
so a bad document there fails at the point of use, in the send path, at 3am.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_kernel.settings_models import SettingDomain, SettingValueType
from dotmac_kernel.settings_resolver import SettingSpec, resolve_value

#: The setting key. A product may declare it in whatever domain it owns; the key
#: is fixed so the reader and the admin surface agree without configuration.
CHANNEL_POLICY_KEY = "channel_policy"

_DOCUMENT_SECTIONS = ("categories", "events")


class ChannelPolicyError(ValueError):
    """A malformed channel-policy document."""


def _validate_channel_list(value: object, where: str) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ChannelPolicyError(
            f"{where} must be a list of channel names, got {type(value).__name__}"
        )
    for channel in value:
        if not isinstance(channel, str) or not channel.strip():
            raise ChannelPolicyError(f"{where} contains a non-string channel name")


def validate_policy_document(value: object) -> None:
    """Refuse a malformed policy at WRITE time. The spec's `validator`."""
    if not isinstance(value, Mapping):
        raise ChannelPolicyError(
            "a channel policy is an object with `default`, `categories` and "
            f"`events`, got {type(value).__name__}"
        )
    unknown = set(value) - {"default", *_DOCUMENT_SECTIONS}
    if unknown:
        raise ChannelPolicyError(
            f"unknown channel-policy key(s): {', '.join(sorted(unknown))}"
        )
    if "default" in value:
        _validate_channel_list(value["default"], "`default`")
    for section in _DOCUMENT_SECTIONS:
        entries = value.get(section)
        if entries is None:
            continue
        if not isinstance(entries, Mapping):
            raise ChannelPolicyError(f"`{section}` must be an object")
        for name, channels in entries.items():
            if not isinstance(name, str) or not name.strip():
                raise ChannelPolicyError(f"`{section}` has a non-string key")
            _validate_channel_list(channels, f"`{section}.{name}`")


def make_spec(
    *,
    domain: SettingDomain,
    default: Mapping[str, object] | None = None,
    description: str | None = None,
) -> SettingSpec:
    """Build the channel-policy spec for a domain the product owns.

    The product declares `domain` on its manifest's `setting_domains` and passes
    the result to `register_specs`, like any other setting.
    """
    return SettingSpec(
        domain=domain,
        key=CHANNEL_POLICY_KEY,
        value_type=SettingValueType("json"),
        default=dict(default or {"default": []}),
        label="Notification channel policy",
        description=(
            description
            or "Which channels each event or category is delivered on. "
            'Shape: {"default": ["email"], "categories": {...}, "events": {...}}. '
            "Most specific wins: event, then category, then default."
        ),
        validator=validate_policy_document,
    )


def resolve_channels(
    db: Session,
    spec: SettingSpec,
    *,
    tenant_id: UUID | None,
    event: str | None = None,
    category: str | None = None,
    fallback: Iterable[str] = (),
) -> tuple[str, ...]:
    """The channels this message class goes out on. Most specific wins.

    `fallback` is the caller's own answer when the policy says nothing — the last
    resort that keeps an unconfigured deployment reaching the customer rather
    than going silent. It is NOT a default the policy can be overridden by: any
    policy entry that matches beats it.

    A malformed stored document degrades to the fallback rather than raising:
    resolution is on the send path, and a bad policy should not turn one
    operator's typo into a total delivery outage. Writes are validated, which is
    where the loud failure belongs.
    """
    document = resolve_value(
        db, spec.domain, spec.key, tenant_id=tenant_id, default=None
    )
    if not isinstance(document, Mapping):
        return tuple(fallback)

    try:
        validate_policy_document(document)
    except ChannelPolicyError:
        return tuple(fallback)

    for section, name in (("events", event), ("categories", category)):
        if not name:
            continue
        entries = document.get(section)
        if isinstance(entries, Mapping):
            channels = entries.get(name)
            if channels:
                return tuple(str(c) for c in channels)

    default = document.get("default")
    if default:
        return tuple(str(c) for c in default)
    return tuple(fallback)


__all__ = [
    "CHANNEL_POLICY_KEY",
    "ChannelPolicyError",
    "make_spec",
    "resolve_channels",
    "validate_policy_document",
]
