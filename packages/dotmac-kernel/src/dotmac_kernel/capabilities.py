"""Capability catalogue (WS1) — the code-authoritative set of capability codes.

A *capability code* (e.g. ``"inventory.use"``) is a module's declaration, on its
``FeatureManifest.capabilities``, that a capability physically exists. This module
builds the catalogue of those declarations and is the ONE place that answers
"is this code real?".

The load-bearing invariant (deployment-profiles plan): **capability codes are
declared by manifests and may never be invented anywhere else** — an entitlement
grant, a deployment profile, or a DB row may only ever *reference* a declared
code. Two modules declaring the same code is a conflict, not a merge. Nothing
here touches a database: the catalogue is derived from code (the manifests),
while operational state (which tenant is granted what) is a separate, later
authority (WS2) that consumes this catalogue.

Import-safe: pure data over the manifests; no engine, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from dotmac_kernel.features import FeatureManifest


class DuplicateCapabilityError(ValueError):
    """Two modules declared the same capability code — there is no single owner."""


class UndeclaredCapabilityError(KeyError):
    """A capability code was referenced that no installed module declares."""


class CapabilityCatalogue:
    """The immutable set of capability codes declared across a set of modules.

    Build it once from the installed manifests (`from_manifests`); then ask it
    whether a referenced code is real (`is_declared`/`require`) and who owns it
    (`owner`). Construction fails closed on a duplicate declaration.
    """

    __slots__ = ("_owner_by_code",)

    def __init__(self, owner_by_code: Mapping[str, str]) -> None:
        self._owner_by_code: dict[str, str] = dict(owner_by_code)

    @classmethod
    def from_manifests(
        cls, manifests: Iterable[FeatureManifest]
    ) -> CapabilityCatalogue:
        owner_by_code: dict[str, str] = {}
        for manifest in manifests:
            for code in manifest.capabilities:
                existing = owner_by_code.get(code)
                if existing is not None and existing != manifest.name:
                    raise DuplicateCapabilityError(
                        f"capability {code!r} declared by both {existing!r} and "
                        f"{manifest.name!r} — a capability code has one owning module"
                    )
                owner_by_code[code] = manifest.name
        return cls(owner_by_code)

    def is_declared(self, code: str) -> bool:
        """True iff some installed module declares `code`."""
        return code in self._owner_by_code

    def require(self, code: str) -> None:
        """Raise `UndeclaredCapabilityError` unless `code` is declared. Use at the
        boundary where an external reference (a grant, a profile) names a code."""
        if code not in self._owner_by_code:
            raise UndeclaredCapabilityError(
                f"capability code {code!r} is not declared by any installed module"
            )

    def owner(self, code: str) -> str | None:
        """The module that declares `code`, or None if undeclared."""
        return self._owner_by_code.get(code)

    def codes(self) -> frozenset[str]:
        """Every declared capability code."""
        return frozenset(self._owner_by_code)


__all__ = [
    "CapabilityCatalogue",
    "DuplicateCapabilityError",
    "UndeclaredCapabilityError",
]
