"""Open, owner-controlled hosting business vocabularies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_OBSERVATION_KINDS = ("active", "suspended", "terminated", "provider_correction")
DEFAULT_RESOURCE_KINDS = (
    "disk_bytes",
    "bandwidth_bytes",
    "inode_count",
    "mailbox_count",
    "database_count",
)
DEFAULT_SUSPENSION_RESTORERS = {
    "delinquency": frozenset({"collections.payment_satisfied"}),
    "abuse": frozenset({"abuse.cleared"}),
    "manual": frozenset({"operator.manual_restore"}),
}


def _codes(name: str, values: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        code = value.strip()
        if not code or len(code) > 120:
            raise ValueError(f"{name} codes must be non-empty and at most 120 chars")
        if code != code.lower() or any(character.isspace() for character in code):
            raise ValueError(f"{name} code {code!r} must be lower-case without spaces")
        normalized.add(code)
    if not normalized:
        raise ValueError(f"{name} registry cannot be empty")
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class HostingVocabularyRegistry:
    observation_kinds: frozenset[str]
    resource_kinds: frozenset[str]
    suspension_restorers: Mapping[str, frozenset[str]]

    def __init__(
        self,
        *,
        observation_kinds: Iterable[str] = DEFAULT_OBSERVATION_KINDS,
        resource_kinds: Iterable[str] = DEFAULT_RESOURCE_KINDS,
        suspension_restorers: Mapping[str, Iterable[str]] = DEFAULT_SUSPENSION_RESTORERS,
    ) -> None:
        object.__setattr__(
            self, "observation_kinds", _codes("observation kind", observation_kinds)
        )
        object.__setattr__(self, "resource_kinds", _codes("resource kind", resource_kinds))
        normalized = {
            reason: _codes(f"restorer for {reason}", restorers)
            for reason, restorers in suspension_restorers.items()
        }
        _codes("suspension reason", normalized)
        object.__setattr__(self, "suspension_restorers", normalized)

    def require_observation_kind(self, code: str) -> None:
        if code not in self.observation_kinds:
            raise KeyError(f"hosting observation kind {code!r} is not registered")

    def require_resource_kind(self, code: str) -> None:
        if code not in self.resource_kinds:
            raise KeyError(f"hosting resource kind {code!r} is not registered")

    def require_suspension_reason(self, code: str) -> None:
        if code not in self.suspension_restorers:
            raise KeyError(f"hosting suspension reason {code!r} is not registered")

    def permits_restore(self, reason: str, restorer: str) -> bool:
        self.require_suspension_reason(reason)
        return restorer in self.suspension_restorers[reason]

    def extended(
        self,
        *,
        observation_kinds: Iterable[str] = (),
        resource_kinds: Iterable[str] = (),
        suspension_restorers: Mapping[str, Iterable[str]] | None = None,
    ) -> HostingVocabularyRegistry:
        restorers = dict(self.suspension_restorers)
        if suspension_restorers:
            restorers.update(suspension_restorers)
        return HostingVocabularyRegistry(
            observation_kinds=(*self.observation_kinds, *observation_kinds),
            resource_kinds=(*self.resource_kinds, *resource_kinds),
            suspension_restorers=restorers,
        )


_active_registry = HostingVocabularyRegistry()


def active_hosting_vocabulary() -> HostingVocabularyRegistry:
    return _active_registry


def install_hosting_vocabulary(registry: HostingVocabularyRegistry) -> None:
    global _active_registry
    _active_registry = registry


__all__ = [
    "DEFAULT_OBSERVATION_KINDS",
    "DEFAULT_RESOURCE_KINDS",
    "DEFAULT_SUSPENSION_RESTORERS",
    "HostingVocabularyRegistry",
    "active_hosting_vocabulary",
    "install_hosting_vocabulary",
]
