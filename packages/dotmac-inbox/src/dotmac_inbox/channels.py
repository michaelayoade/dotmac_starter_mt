"""Open conversation-channel declarations described by fixed behavior traits.

The module branches on traits, never on channel names. Products declare their
own vocabulary; the shared owner validates declarations and consumes only the
traits needed for threading and message identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

_CODE_MAX: Final[int] = 40


class AddressForm(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    OPAQUE = "opaque"

    @property
    def is_addressable(self) -> bool:
        return self is not AddressForm.OPAQUE


class Transport(StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"


class ThreadIdentity(StrEnum):
    PROVIDER = "provider"
    DERIVED = "derived"
    SUPPLIED = "supplied"


class MessageIdScope(StrEnum):
    GLOBAL = "global"
    ACCOUNT = "account"
    NONE = "none"
    SUPPLIED = "supplied"


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One product-owned channel and the traits conversation behavior reads."""

    code: str
    owner: str
    address_form: AddressForm
    transport: Transport
    thread_identity: ThreadIdentity
    message_id_scope: MessageIdScope
    label: str = ""

    def __post_init__(self) -> None:
        if not self.code or self.code != self.code.strip().lower():
            raise ValueError(
                f"channel code must be lowercase and unpadded: {self.code!r}"
            )
        if len(self.code) > _CODE_MAX:
            raise ValueError(
                f"channel code {self.code!r} exceeds {_CODE_MAX} characters"
            )
        if not self.owner or not self.owner.strip():
            raise ValueError(f"channel {self.code!r} must declare an owner")
        if self.transport is Transport.INTERNAL:
            if self.thread_identity is ThreadIdentity.PROVIDER:
                raise ValueError(
                    f"channel {self.code!r} is internal but claims provider thread "
                    "identity"
                )
            if self.message_id_scope not in {
                MessageIdScope.NONE,
                MessageIdScope.SUPPLIED,
            }:
                raise ValueError(
                    f"channel {self.code!r} is internal but claims "
                    f"message_id_scope={self.message_id_scope.value!r}"
                )


class UnknownChannelError(ValueError):
    """The requested channel has no product declaration."""


_REGISTRY: dict[str, ChannelSpec] = {}


def register_channels(specs: list[ChannelSpec] | tuple[ChannelSpec, ...]) -> None:
    """Register channels; identical import-time redeclaration is idempotent."""
    for spec in specs:
        existing = _REGISTRY.get(spec.code)
        if existing is not None and existing != spec:
            raise ValueError(
                f"channel {spec.code!r} is already declared by {existing.owner!r} "
                "with different traits"
            )
        _REGISTRY[spec.code] = spec


def registered_channels() -> tuple[ChannelSpec, ...]:
    return tuple(_REGISTRY[code] for code in sorted(_REGISTRY))


def channel_spec(code: str | None) -> ChannelSpec:
    normalized = (code or "").strip().lower()
    try:
        return _REGISTRY[normalized]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none declared)"
        raise UnknownChannelError(
            f"channel {code!r} is not declared. Declared channels: {known}. "
            "Declare it with dotmac_inbox.register_channels(...)."
        ) from None


def reset_channel_registry_for_tests() -> None:
    """Clear declarations. Test support only; runtime deregistration is absent."""
    _REGISTRY.clear()


__all__ = [
    "AddressForm",
    "ChannelSpec",
    "MessageIdScope",
    "ThreadIdentity",
    "Transport",
    "UnknownChannelError",
    "channel_spec",
    "register_channels",
    "registered_channels",
    "reset_channel_registry_for_tests",
]
