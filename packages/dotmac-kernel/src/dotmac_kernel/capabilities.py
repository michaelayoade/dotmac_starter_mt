"""Capability catalogue (WS1) — the code-authoritative set of capability codes.

A *capability code* (e.g. ``"inventory.use"``) is a module's declaration, on its
manifest's ``capabilities`` (either a ``ModuleManifest`` or a not-yet-migrated
``FeatureManifest`` — both are accepted), that a capability physically exists.
This module builds the catalogue of those declarations and is the ONE place that
answers "is this code real?".

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
from dataclasses import dataclass
from typing import Final

from dotmac_kernel.modules import AnyManifest
from dotmac_kernel.route_metadata import CAPABILITY_CODE_ATTR


# The attribute `require_capability` stamps on the dependency callable it
# returns, so `create_app` can walk a mounted route's dependency tree and read
# back which capability codes that route actually references. The mirror of
# `permissions.PERMISSION_CODE_ATTR`, and the reason an undeclared code is a
# BOOT failure rather than a silent permanent 403.
@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """One capability a module DECLARES it owns.

    `code` is the stable identifier every downstream authority references — an
    entitlement grant, a deployment profile, a signed licence.

    `default_granted` answers the question enforcement forces: what does a
    tenant that nobody has explicitly provisioned get? It is a per-capability
    DECLARATION, not a global policy, because the answer legitimately differs by
    capability and by product: a self-hosted deployment expects its bundled
    features to work on day one, while a SaaS deployment sells the same
    capability and ships it default-off. `provision_tenant` applies these
    defaults when it creates a tenant; changing one changes what NEW tenants
    get and never touches an existing grant.

    A bare string is still accepted wherever a spec is (see
    `ModuleManifest.capabilities`), and means `default_granted=True` — the
    behaviour those declarations had before enforcement existed.
    """

    code: str
    description: str = ""
    default_granted: bool = True

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("capability spec requires a non-empty `code`")

    @classmethod
    def coerce(cls, value: str | CapabilitySpec) -> CapabilitySpec:
        """A declaration written either way, normalised to a spec."""
        # `CapabilitySpec` by name, not `cls`: mypy does not narrow a union
        # through a classmethod's `cls`, so `isinstance(value, cls)` leaves
        # `value` as `str | CapabilitySpec` in the else branch.
        if isinstance(value, CapabilitySpec):
            return value
        return cls(code=value)


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

    __slots__ = ("_owner_by_code", "_spec_by_code")

    def __init__(
        self,
        owner_by_code: Mapping[str, str],
        specs: Mapping[str, CapabilitySpec] | None = None,
    ) -> None:
        self._owner_by_code: dict[str, str] = dict(owner_by_code)
        # A catalogue built from a bare {code: owner} map (a test, a probe) has
        # no declarations to carry, so every code falls back to the default
        # spec — same meaning a bare string declaration has.
        self._spec_by_code: dict[str, CapabilitySpec] = dict(specs or {})

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> CapabilityCatalogue:
        owner_by_code: dict[str, str] = {}
        spec_by_code: dict[str, CapabilitySpec] = {}
        for manifest in manifests:
            for declaration in manifest.capabilities:
                spec = CapabilitySpec.coerce(declaration)
                existing = owner_by_code.get(spec.code)
                if existing is not None and existing != manifest.name:
                    raise DuplicateCapabilityError(
                        f"capability {spec.code!r} declared by both {existing!r} "
                        f"and {manifest.name!r} — a capability code has one "
                        "owning module"
                    )
                owner_by_code[spec.code] = manifest.name
                spec_by_code[spec.code] = spec
        return cls(owner_by_code, spec_by_code)

    def spec(self, code: str) -> CapabilitySpec:
        """The declaration for `code`. Raises if it is not declared."""
        self.require(code)
        return self._spec_by_code.get(code, CapabilitySpec(code=code))

    def default_granted_codes(self) -> tuple[str, ...]:
        """Codes a NEWLY PROVISIONED tenant is entitled to without an explicit
        grant — the set `provision_tenant` applies. Sorted, so the grants a
        tenant is created with are deterministic."""
        return tuple(
            sorted(
                code for code in self._owner_by_code if self.spec(code).default_granted
            )
        )

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


# ── The process-active catalogue ────────────────────────────────────────────
# Same shape as `permissions.install_permissions`/`active_permissions`, and for
# the same reason: `require_capability` needs to answer "is this code real?" at
# request time without importing an app or a manifest list.
#
# The default is EMPTY, which means `require` raises for every code — deny, not
# allow. An uninstalled catalogue is a wiring mistake, and a wiring mistake must
# not silently entitle every tenant to everything.
_EMPTY_CATALOGUE: Final[CapabilityCatalogue] = CapabilityCatalogue({})
_active_catalogue: CapabilityCatalogue = _EMPTY_CATALOGUE


def install_capabilities(catalogue: CapabilityCatalogue) -> None:
    """Install the process-active capability catalogue.

    Called by `create_app` with the catalogue built from the INSTALLED module
    set — not the enabled subset. A disabled module's capabilities stay
    DECLARED: disabling a module must never turn a real code into an undeclared
    one, or a grant referencing it would become unexplainable. Whether a
    disabled module's routes can be reached is a separate question, already
    answered by not mounting them.

    A consumer that builds an app by hand (a unit test mounting a router on a
    bare `FastAPI()`) must call this itself, exactly as it must call
    `install_permissions`.
    """
    global _active_catalogue
    _active_catalogue = catalogue


def active_capabilities() -> CapabilityCatalogue:
    """The process-active catalogue — empty (deny-everything) until installed."""
    return _active_catalogue


__all__ = [
    "CAPABILITY_CODE_ATTR",
    "CapabilityCatalogue",
    "CapabilitySpec",
    "DuplicateCapabilityError",
    "UndeclaredCapabilityError",
    "active_capabilities",
    "install_capabilities",
]
