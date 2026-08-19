"""Manifest-owned routing vocabulary for durable outbox events.

An outbox event type is owned by the module that consumes that event. The
backing outbox column remains plain text, while this registry makes typos and
owner collisions fail at the producing boundary. It deliberately does not
decide delivery, retry, or any business consequence.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dotmac_kernel.modules import AnyManifest


class DuplicateOutboxEventTypeError(ValueError):
    """Two modules declared one routing code, so it has no single owner."""


class UndeclaredOutboxEventTypeError(KeyError):
    """A producer referenced a routing code no installed module declares."""


class OutboxEventTypesNotInstalledError(RuntimeError):
    """The process never installed its manifest-derived routing vocabulary."""


class OutboxEventTypeRegistry:
    """Immutable owner map for outbox event types declared by manifests."""

    __slots__ = ("_owner_by_event_type",)

    def __init__(self, declarations: Iterable[tuple[str, str]]) -> None:
        owner_by_event_type: dict[str, str] = {}
        for owner, event_type in declarations:
            if not event_type:
                raise ValueError("outbox event type declarations must be non-empty")
            existing = owner_by_event_type.get(event_type)
            if existing is not None and existing != owner:
                raise DuplicateOutboxEventTypeError(
                    f"outbox event type {event_type!r} declared by both "
                    f"{existing!r} and {owner!r} — a routing code has one owner"
                )
            owner_by_event_type[event_type] = owner
        self._owner_by_event_type = owner_by_event_type

    @classmethod
    def from_manifests(
        cls, manifests: Iterable[AnyManifest]
    ) -> OutboxEventTypeRegistry:
        return cls(
            (manifest.name, event_type)
            for manifest in manifests
            for event_type in getattr(manifest, "outbox_event_types", ())
        )

    def require(self, event_type: str) -> None:
        if event_type not in self._owner_by_event_type:
            raise UndeclaredOutboxEventTypeError(
                f"outbox event type {event_type!r} is not declared by any "
                "installed module — declare it on the consuming module's "
                "manifest (`outbox_event_types=(...)`)"
            )

    def owner(self, event_type: str) -> str | None:
        return self._owner_by_event_type.get(event_type)

    def event_types(self) -> frozenset[str]:
        return frozenset(self._owner_by_event_type)


_active_registry: OutboxEventTypeRegistry | None = None


def install_outbox_event_types(registry: OutboxEventTypeRegistry) -> None:
    """Install the process vocabulary built from every installed manifest."""
    global _active_registry
    _active_registry = registry


def active_outbox_event_types() -> OutboxEventTypeRegistry:
    """Return the process vocabulary, refusing an assembly wiring omission."""
    if _active_registry is None:
        raise OutboxEventTypesNotInstalledError(
            "no outbox event-type registry is installed in this process; "
            "create_app installs one, while workers and tests must install "
            "OutboxEventTypeRegistry.from_manifests(...) explicitly"
        )
    return _active_registry


__all__ = [
    "DuplicateOutboxEventTypeError",
    "OutboxEventTypeRegistry",
    "OutboxEventTypesNotInstalledError",
    "UndeclaredOutboxEventTypeError",
    "active_outbox_event_types",
    "install_outbox_event_types",
]
