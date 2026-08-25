"""What a channel IS — the one vocabulary, declared and never enumerated.

**STATUS: PROTOTYPE — not a declared owner (2026-08-12).** This module exists as
audit evidence on branch `docs/omni-inbox-sources`. Whether this capability
belongs in the kernel at all is under adjudication in
`docs/superpowers/plans/2026-08-12-fleet-decomposition-matrix.md`. Do not publish,
do not adopt, and read every "Owner of" claim below as a proposal.

**Owner of:** *given a channel, how does it behave?*

Before this module the kernel knew about channels in three places and validated
them in none:

* `dotmac_kernel.consent.register_numeric_channels` — a real per-channel
  behaviour registry, but for exactly one facet: whether an address is a phone
  number and must be normalised to digits.
* `dotmac_kernel.channel_policy` — channel names inside the policy document,
  documented as "an open registered string".
* `dotmac_kernel.delivery_providers.OutboundMessage.channel` — a bare `str`,
  checked only for non-emptiness.

A fourth was about to appear in the inbox module
(`docs/inventories/inbox-sources.md` § "Four places now know what a channel
is"). This module is the answer: one registry, one owner per channel, and four
traits that every consumer reads instead of learning channel NAMES.

## Why the kernel owns it and no module can

Modules may not import each other (ADR-0006 § 2, enforced by the "Modules are
independent of each other" contract). Consent, channel policy, delivery and any
conversation module all need the same vocabulary, so the only place it can live
without becoming a second registry is here.

## Why a registry and not an enum

ADR-0008, the same rule that governs `SettingDomain`. The fleet's two inbox
implementations name sixteen channels between them and agree on six; the next
product's cannot be predicted. A native Postgres enum needs `ALTER TYPE` to
grow, and a merged Python enum makes every product carry every other product's
terms.

## The four traits, and the question each one answers

| Trait | Answers |
|---|---|
| `address_form` | how is a recipient canonicalised, and is it matchable? |
| `transport` | does an outbound message leave the deployment at all? |
| `thread_identity` | does the provider supply thread identity, or is it derived? |
| `message_id_scope` | how far is a provider's message id unique — the dedup key |

The first is the kernel's own: `consent.normalize_address` reads it, and it is
what stops a suppression being dodged by punctuation. The other three are read
by conversation-shaped consumers. They live here anyway rather than being split
across packages, because a channel is one thing and a spec that describes half
of it invites a second registry for the other half — which is the exact problem
this module exists to end.

## `address_form` has three values, not two

An earlier draft of this in the inbox module had `contact_identity:
addressable | opaque`, which collapses email and phone into one value. Merging
that with `register_numeric_channels` would have LOST the numeric distinction
and reintroduced the punctuation bug that registry exists to prevent. Three
values is the correction; "addressable" is now simply `form is not OPAQUE`.

## Nothing is auto-registered except the two consent already shipped

`sms` and `whatsapp` ship declared as `PHONE`, because
`consent._NUMERIC_CHANNELS` already shipped exactly those two and removing them
would silently change how existing suppressions normalise. Everything else —
including `email` — is the product's to declare. An undeclared channel is not an
error here: `address_form_for` returns the safe default, matching today's
behaviour, because consent must never fail closed on a missing declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "DEFAULT_CHANNELS",
    "AddressForm",
    "ChannelSpec",
    "MessageIdScope",
    "ThreadIdentity",
    "Transport",
    "UnknownChannelError",
    "address_form_for",
    "channel_spec",
    "is_registered",
    "register_channels",
    "registered_channels",
    "reset_registry_for_tests",
]

_CODE_MAX: Final[int] = 40


class AddressForm(StrEnum):
    """How a recipient address is canonicalised, and whether it travels.

    Read by `dotmac_kernel.consent.normalize_address`, which is what makes this
    the kernel's own trait rather than a conversation concern.
    """

    #: A mailbox-style address in a global namespace. Case-insensitive, so it
    #: canonicalises by lowercasing. Matchable against a directory.
    EMAIL = "email"
    #: A telephone number. Punctuation and spacing are not identity, so it
    #: canonicalises to digits only — without this, `+234 801 234 5678` and
    #: `2348012345678` are two addresses and a suppression is dodgeable.
    PHONE = "phone"
    #: A provider-scoped opaque id — a Messenger PSID, a widget visitor token.
    #: Comparing it to anything outside that provider account is meaningless and
    #: normalising it is corruption, so it is preserved verbatim.
    OPAQUE = "opaque"

    @property
    def is_addressable(self) -> bool:
        """Whether the address means anything outside its provider account."""
        return self is not AddressForm.OPAQUE


class Transport(StrEnum):
    """Whether an outbound message leaves the deployment."""

    #: Delivery goes through a provider the product owns and operates.
    EXTERNAL = "external"
    #: Delivery is in-band — an internal note, or a chat rendered straight into
    #: a surface the deployment already serves. There is no provider to call and
    #: no delivery receipt to wait for.
    INTERNAL = "internal"


class ThreadIdentity(StrEnum):
    """Where a conversation's thread identity comes from."""

    #: The provider supplies it: an RFC 5322 `References` chain, a Messenger
    #: thread id. Threading follows the provider, including across subject
    #: changes.
    PROVIDER = "provider"
    #: The provider supplies none, so it is derived from `(channel, contact)`.
    DERIVED = "derived"


class MessageIdScope(StrEnum):
    """How far a provider's message id is unique — the deduplication key."""

    #: Unique across every account: an RFC 5322 `Message-ID` is generated to be.
    GLOBAL = "global"
    #: Unique only within one connected account. The same id legitimately
    #: arrives at two connected accounts, and treating that as a duplicate
    #: silently drops the second.
    ACCOUNT = "account"
    #: The provider gives no usable id. Deduplication falls back to a content
    #: fingerprint, by declaration rather than because a column was NULL.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One declared channel and the four traits its consumers reason about."""

    code: str
    #: The module code that declares it. A code with no owner cannot be
    #: attributed when it turns out to be wrong.
    owner: str
    address_form: AddressForm
    transport: Transport
    thread_identity: ThreadIdentity
    message_id_scope: MessageIdScope
    #: Human label for operator surfaces. Never branched on.
    label: str = ""

    def __post_init__(self) -> None:
        code = self.code
        if not code or not code.strip():
            raise ValueError("channel code must be a non-empty string")
        if code != code.strip().lower():
            raise ValueError(f"channel code must be lowercase and unpadded: {code!r}")
        if len(code) > _CODE_MAX:
            raise ValueError(
                f"channel code {code!r} exceeds {_CODE_MAX} characters — it is "
                "stored in String(40) columns"
            )
        if not self.owner or not self.owner.strip():
            raise ValueError(f"channel {code!r} must declare an owning module")
        # An internal channel has no provider, so claiming a provider-supplied
        # thread id or message id is a declaration nothing can satisfy.
        if self.transport is Transport.INTERNAL:
            if self.thread_identity is ThreadIdentity.PROVIDER:
                raise ValueError(
                    f"channel {code!r} is internal transport but claims provider "
                    "thread identity — there is no provider to supply one"
                )
            if self.message_id_scope is not MessageIdScope.NONE:
                raise ValueError(
                    f"channel {code!r} is internal transport but claims "
                    f"message_id_scope={self.message_id_scope.value!r} — an "
                    "internal message has no provider id; declare 'none'"
                )

    @property
    def is_addressable(self) -> bool:
        return self.address_form.is_addressable


class UnknownChannelError(ValueError):
    """A channel that was never declared, where a declaration was required.

    Raised by `channel_spec`, never by `address_form_for` — see this module's
    docstring for why consent must degrade rather than fail on a missing
    declaration.
    """


def _spec(code: str, owner: str, form: AddressForm, label: str) -> ChannelSpec:
    return ChannelSpec(
        code=code,
        owner=owner,
        address_form=form,
        transport=Transport.EXTERNAL,
        thread_identity=ThreadIdentity.DERIVED,
        message_id_scope=MessageIdScope.ACCOUNT,
        label=label,
    )


#: The two `dotmac_kernel.consent` already shipped in `_NUMERIC_CHANNELS`.
#: Present so this refactor changes no existing deployment's normalisation, and
#: deliberately NOT extended: `email` and everything else is the product's to
#: declare (`dotmac_kernel.channel_policy` — "the kernel neither invents a
#: domain nor knows which channels exist").
DEFAULT_CHANNELS: Final[tuple[ChannelSpec, ...]] = (
    _spec("sms", "kernel", AddressForm.PHONE, "SMS"),
    _spec("whatsapp", "kernel", AddressForm.PHONE, "WhatsApp"),
)

_REGISTRY: dict[str, ChannelSpec] = {spec.code: spec for spec in DEFAULT_CHANNELS}


def _normalize_code(code: str | None) -> str:
    return (code or "").strip().lower()


def register_channels(specs: tuple[ChannelSpec, ...] | list[ChannelSpec]) -> None:
    """Declare channels at import time. Idempotent for an identical re-declaration.

    Re-declaring a code with DIFFERENT traits raises. Two modules disagreeing
    about whether a channel's address is a phone number is a real conflict, and
    last-writer-wins would resolve it silently and arbitrarily — with a
    dodgeable suppression as the consequence.

    A product MAY re-declare a `DEFAULT_CHANNELS` entry to take ownership of it,
    provided the traits match; changing `sms` to `EMAIL` is refused.
    """
    for spec in specs:
        existing = _REGISTRY.get(spec.code)
        if existing is not None and existing != spec:
            if existing.address_form is spec.address_form and existing.owner != (
                spec.owner
            ):
                # Same normalisation, different declarer: a product adopting a
                # kernel default and filling in the conversation traits. Allowed,
                # because nothing consent decides changes.
                _REGISTRY[spec.code] = spec
                continue
            raise ValueError(
                f"channel {spec.code!r} is already declared by {existing.owner!r} "
                f"with address_form={existing.address_form.value!r}; a channel "
                "has one owner and one set of traits"
            )
        _REGISTRY[spec.code] = spec


def registered_channels() -> tuple[ChannelSpec, ...]:
    """Every declared channel, ordered by code for stable output."""
    return tuple(_REGISTRY[code] for code in sorted(_REGISTRY))


def is_registered(code: str | None) -> bool:
    """Whether `code` has been declared. Never raises."""
    return _normalize_code(code) in _REGISTRY


def channel_spec(code: str | None) -> ChannelSpec:
    """The spec for `code`, or `UnknownChannelError`.

    For callers that REQUIRE a declaration — threading and deduplication, where
    a silent default would give one channel another's rules and merge two
    customers' messages into one conversation.
    """
    normalised = _normalize_code(code)
    try:
        return _REGISTRY[normalised]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none declared)"
        raise UnknownChannelError(
            f"channel {code!r} is not declared. Declared channels: {known}. "
            "Declare it with dotmac_kernel.channels.register_channels(...) in "
            "the owning module, rather than adding a branch at the call site."
        ) from None


def address_form_for(code: str | None) -> AddressForm:
    """The address form for `code`, defaulting to `EMAIL` when undeclared.

    Deliberately total. Consent is on the path of every send, and a missing
    declaration must not stop an invoice — so an unknown channel gets the
    lowercase treatment, which is exactly what `normalize_address` did for every
    non-numeric channel before this module existed.

    The failure mode is understood and documented: an undeclared PHONE-like
    channel normalises by case rather than by digits, so a suppression on it can
    be dodged by punctuation. That is why declaring a channel is the fix, and why
    `sms`/`whatsapp` ship pre-declared rather than relying on products to
    remember.
    """
    spec = _REGISTRY.get(_normalize_code(code))
    return spec.address_form if spec is not None else AddressForm.EMAIL


def reset_registry_for_tests(*, include_defaults: bool = True) -> None:
    """Empty the registry. Tests only — there is no runtime deregistration.

    `include_defaults=False` clears it completely, including `sms`/`whatsapp`.
    Needed by callers that restore an exact prior set, which would otherwise
    find the defaults silently re-added underneath them.
    """
    _REGISTRY.clear()
    if include_defaults:
        _REGISTRY.update({spec.code: spec for spec in DEFAULT_CHANNELS})
