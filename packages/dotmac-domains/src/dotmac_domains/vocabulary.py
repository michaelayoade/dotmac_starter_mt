"""Open, owner-controlled domain lifecycle vocabularies.

Registry status tokens remain provider observations and are never registered or
mapped here.  The two registries below name Dotmac semantic categories.  They
are immutable after construction so an application installs one reviewed
catalogue rather than allowing request-time mutation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_OBSERVATION_KINDS = (
    "registered",
    "renewed",
    "expiry_observed",
    "transfer_requested",
    "transfer_completed",
    "transfer_rejected",
    "redemption_observed",
    "deleted",
    "provider_correction",
)

DEFAULT_CONSEQUENCE_KINDS = (
    "renewal_review",
    "transfer_hold",
    "release",
    "allow_lapse",
)


def _codes(name: str, values: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in values:
        code = value.strip()
        if not code or len(code) > 120:
            raise ValueError(f"{name} codes must be non-empty and at most 120 chars")
        if code != code.lower() or any(ch.isspace() for ch in code):
            raise ValueError(f"{name} code {code!r} must be lower-case without spaces")
        normalized.add(code)
    if not normalized:
        raise ValueError(f"{name} registry cannot be empty")
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class DomainVocabularyRegistry:
    """Reviewed semantic vocabulary installed by the consuming assembly."""

    observation_kinds: frozenset[str]
    consequence_kinds: frozenset[str]

    def __init__(
        self,
        *,
        observation_kinds: Iterable[str] = DEFAULT_OBSERVATION_KINDS,
        consequence_kinds: Iterable[str] = DEFAULT_CONSEQUENCE_KINDS,
    ) -> None:
        object.__setattr__(
            self, "observation_kinds", _codes("observation kind", observation_kinds)
        )
        object.__setattr__(
            self, "consequence_kinds", _codes("consequence kind", consequence_kinds)
        )

    def require_observation_kind(self, code: str) -> None:
        if code not in self.observation_kinds:
            raise KeyError(f"domain observation kind {code!r} is not registered")

    def require_consequence_kind(self, code: str) -> None:
        if code not in self.consequence_kinds:
            raise KeyError(f"domain consequence kind {code!r} is not registered")

    def extended(
        self,
        *,
        observation_kinds: Iterable[str] = (),
        consequence_kinds: Iterable[str] = (),
    ) -> DomainVocabularyRegistry:
        """Return a new reviewed catalogue; never mutate the active one."""

        return DomainVocabularyRegistry(
            observation_kinds=(*self.observation_kinds, *observation_kinds),
            consequence_kinds=(*self.consequence_kinds, *consequence_kinds),
        )


_active_registry = DomainVocabularyRegistry()


def active_domain_vocabulary() -> DomainVocabularyRegistry:
    return _active_registry


def install_domain_vocabulary(registry: DomainVocabularyRegistry) -> None:
    global _active_registry
    _active_registry = registry


__all__ = [
    "DEFAULT_CONSEQUENCE_KINDS",
    "DEFAULT_OBSERVATION_KINDS",
    "DomainVocabularyRegistry",
    "active_domain_vocabulary",
    "install_domain_vocabulary",
]
