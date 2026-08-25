"""Permission catalogue (module control-plane directive, step 3).

A *permission code* (e.g. ``"rbac.audit.read"``) is a module's declaration, on
its manifest's ``permissions``, that an authorization decision EXISTS and that
this module owns it. This module builds the catalogue of those declarations and
is the ONE place that answers "is this permission real, and who may hold it?".

Same shape and same fail-closed posture as
``dotmac_kernel.capabilities.CapabilityCatalogue``, deliberately — the two are
sibling declaration catalogues and differ only in what they gate:

============  ===================================  =========================
Catalogue     Answers                              Consumed by
============  ===================================  =========================
capability    "does this capability exist, and     entitlement grants (WS2),
              is this TENANT entitled to it?"      deployment profiles
permission    "does this permission exist, and     the route guard
              does this ACTOR hold it?"            (``require_permission``)
============  ===================================  =========================

The load-bearing invariant is the capability catalogue's, restated for actors:
**permission codes are declared by manifests and may never be invented anywhere
else.** A guard may only ever *reference* a declared code; two modules declaring
the same code is a conflict, not a merge. `create_app` validates every code
referenced by a mounted route against the catalogue built from the installed
manifests, so a typo'd or orphaned reference stops the boot rather than
surfacing as a mystery 403 on the first request that reaches it.

## Why a spec, not a bare string

A capability declaration is a bare code because the *decision* it feeds lives in
a database (a tenant's grant row). A permission's decision has no store yet, so
the declaration carries the binding it needs: ``default_roles``, the role slugs
that hold the permission out of the box. That is the same relationship a
``SettingSpec.default`` has to a ``domain_settings`` row — the code-declared
default, layered under a future tenant-configurable override, NOT a second
authority. Adding tenant-configurable role→permission grants is a later program
step; when it lands it layers over this default exactly as a tenant setting
layers over a spec default.

## Process-active catalogue

`require_permission` runs inside a request and `write_audit_event`'s sibling
registry runs inside a service, so neither can be handed a catalogue as an
argument without threading it through every call site. The catalogue is
therefore installed process-wide by `create_app` (`install_permissions`), the
same way `install_surface_globals` installs the process-static Jinja surface
globals. The default is an EMPTY catalogue: with nothing installed, every code
is undeclared and every guarded route denies — fail closed, never fail open.

Import-safe: pure data over the manifests; no engine, no I/O, no FastAPI.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from dotmac_kernel.route_metadata import PERMISSION_CODE_ATTR

if TYPE_CHECKING:  # avoids a runtime cycle: `features` imports this module
    from dotmac_kernel.modules import AnyManifest


# The attribute `dotmac_kernel.deps.require_permission` stamps on the dependency
# callable it returns, carrying the referenced code. `create_app` reads it back
# off every mounted route to prove the reference is declared. Kernel-internal:
# a route declares its permission by CALLING the guard, never by setting this.
class DuplicatePermissionError(ValueError):
    """Two modules declared the same permission code — there is no single owner."""


class UndeclaredPermissionError(KeyError):
    """A permission code was referenced that no installed module declares."""


@dataclass(frozen=True)
class PermissionSpec:
    """One permission a module declares it OWNS.

    `code` is the stable identifier a guard references. `default_roles` is the
    code-declared default binding (see the module docstring): the role slugs
    whose holders satisfy the permission. It is deliberately non-empty — a
    permission no role can hold is an unreachable route, which is a declaration
    bug, not a lockdown.
    """

    code: str
    description: str = ""
    default_roles: Sequence[str] = ("admin",)

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("permission spec requires a non-empty `code`")
        roles = tuple(self.default_roles)
        if not roles:
            raise ValueError(
                f"permission {self.code!r} declares no `default_roles` — a "
                "permission no role can hold makes its routes unreachable"
            )
        object.__setattr__(self, "default_roles", roles)


class PermissionCatalogue:
    """The immutable set of permission codes declared across a set of modules.

    Build it once from the installed manifests (`from_manifests`); then ask it
    whether a referenced code is real (`is_declared`/`require`), what it binds
    to (`spec`), and who owns it (`owner`). Construction fails closed on a
    duplicate declaration.
    """

    __slots__ = ("_owner_by_code", "_spec_by_code")

    def __init__(self, declarations: Iterable[tuple[str, PermissionSpec]]) -> None:
        """`declarations` is (owning module code, spec) pairs. Construction IS
        validation: a code declared by two different modules raises here."""
        owner_by_code: dict[str, str] = {}
        spec_by_code: dict[str, PermissionSpec] = {}
        for owner, spec in declarations:
            existing = owner_by_code.get(spec.code)
            if existing is not None and existing != owner:
                raise DuplicatePermissionError(
                    f"permission {spec.code!r} declared by both {existing!r} and "
                    f"{owner!r} — a permission code has one owning module"
                )
            owner_by_code[spec.code] = owner
            spec_by_code[spec.code] = spec
        self._owner_by_code = owner_by_code
        self._spec_by_code = spec_by_code

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> PermissionCatalogue:
        return cls(
            (manifest.name, spec)
            for manifest in manifests
            for spec in manifest.permissions
        )

    def is_declared(self, code: str) -> bool:
        """True iff some installed module declares `code`."""
        return code in self._spec_by_code

    def require(self, code: str) -> PermissionSpec:
        """The declared spec for `code`, or raise `UndeclaredPermissionError`.

        Use at the boundary where a reference (a route guard, a grant) names a
        code — the return value is what the caller then decides against, so a
        caller cannot accidentally check declaredness and then read a default.
        """
        try:
            return self._spec_by_code[code]
        except KeyError as exc:
            raise UndeclaredPermissionError(
                f"permission code {code!r} is not declared by any installed module"
            ) from exc

    def spec(self, code: str) -> PermissionSpec | None:
        """The declared spec for `code`, or None if undeclared."""
        return self._spec_by_code.get(code)

    def owner(self, code: str) -> str | None:
        """The module that declares `code`, or None if undeclared."""
        return self._owner_by_code.get(code)

    def codes(self) -> frozenset[str]:
        """Every declared permission code."""
        return frozenset(self._spec_by_code)


# The process-active catalogue. EMPTY by default: nothing declared, so every
# referenced code is undeclared and every guarded route denies (see the module
# docstring's fail-closed note). `create_app` installs the real one.
#
# DELIBERATELY ASYMMETRIC with `audit_actions`, which raises
# `AuditActionsNotInstalledError` instead of defaulting to empty. The difference
# is what the default DOES: an uninstalled permission catalogue denies, which is
# the safe answer for an authorization check, so defaulting to empty is correct
# here. An uninstalled audit registry would instead reject every write inside
# the caller's transaction — turning a wiring mistake into a failed business
# operation — so that seam must be able to say "never installed" out loud.
_EMPTY_CATALOGUE: Final[PermissionCatalogue] = PermissionCatalogue(())
_active_catalogue: PermissionCatalogue = _EMPTY_CATALOGUE


def install_permissions(catalogue: PermissionCatalogue) -> None:
    """Install the process-active permission catalogue.

    Called by `create_app` with the catalogue built from the INSTALLED module
    set (not the enabled subset — a disabled module's permissions stay declared,
    so disabling a module never turns a real code into an undeclared one). A
    consumer that builds an app by hand (a unit test mounting a router on a bare
    `FastAPI()`) must call this itself, exactly as it must call
    `install_surface_globals`.
    """
    global _active_catalogue
    _active_catalogue = catalogue


def active_permissions() -> PermissionCatalogue:
    """The process-active catalogue — empty (deny-everything) until installed."""
    return _active_catalogue


__all__ = [
    "DuplicatePermissionError",
    "PERMISSION_CODE_ATTR",
    "PermissionCatalogue",
    "PermissionSpec",
    "UndeclaredPermissionError",
    "active_permissions",
    "install_permissions",
]
