"""Setting scopes — how specific a value is, kept separate from who may see it.

`domain_settings.tenant_id` used to carry two meanings at once: *which tenant
owns this row* (isolation, what RLS keys on) and *how specific this value is*
(precedence, what resolution walks). Conflating them capped the hierarchy at
exactly two levels — platform and tenant — because there was nowhere left to put
a third.

Real products need more: an ISP wants per-POP, a retailer per-branch, almost
everything eventually wants per-user. So the two meanings are separated:

* **`tenant_id` still does isolation, and ONLY isolation.** The RLS policies are
  unchanged — byte for byte — and remain the thing every canary already proves.
* **`scope_kind` + `scope_id` carry precedence**, always WITHIN a tenant.

## Why not one generic scope column

Replacing `tenant_id` with a generic `(scope_kind, scope_id)` reads cleaner and
was rejected. RLS could no longer say `tenant_id = app_current_tenant_id()`,
because a `site`-scoped row does not contain its tenant — it would be reachable
only by joining to whatever owns sites. That would make **tenant ownership
derived rather than stored**, put a per-row subquery in the security predicate,
and give every new scope kind a fresh way to get RLS wrong. The failure mode is
silent cross-tenant disclosure, which is exactly the class of bug the cache-scope
work made unrepresentable one layer up.

Isolation stays a stored fact that RLS reads directly.

## Declaring a hierarchy

Scope kinds are the seventh declaration registry (ADR-0008). A module declares
the kinds it introduces and where they sit, and `resolve_value` walks the chain
most-specific first:

    user → site → tenant → platform → env var → spec default

`platform` and `tenant` are the kernel's own and are always present. `NULL`
never means "some level" — `scope_kind` is NOT NULL, because meaning-by-absence
is precisely what let `dotmac_erp` hold duplicate global settings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:  # avoids a runtime cycle: `features` imports this module
    from dotmac_kernel.modules import AnyManifest

# The kernel's own kinds. `platform` is the deployment-wide fallback every
# tenant reads; `tenant` is a value set for one whole tenant.
PLATFORM = "platform"
TENANT = "tenant"

# Precedence positions for the built-ins. A declared kind names a `rank`; higher
# is MORE specific and therefore wins. The gap between them is deliberate —
# a product inserting `site` or `region` between tenant and platform, or `user`
# above tenant, needs no renumbering of the kernel's own.
PLATFORM_RANK = 0
TENANT_RANK = 100


class ScopeError(Exception):
    """Base for scope declaration and validation failures."""


class DuplicateScopeKindError(ScopeError):
    """Two modules declared the same scope kind — there is no single owner."""


class UndeclaredScopeKindError(ScopeError, ValueError):
    """A scope kind was used that no installed module declares."""


@dataclass(frozen=True, slots=True)
class ScopeKindSpec:
    """One level in a settings hierarchy.

    `rank` orders precedence: a higher rank is more specific and wins. Ranks are
    compared, never assumed contiguous, so a product can slot a level in without
    renumbering anything.
    """

    kind: str
    rank: int
    description: str = ""

    def __post_init__(self) -> None:
        if not self.kind or ":" in self.kind:
            raise ScopeError(
                f"scope kind {self.kind!r} must be a non-empty string without "
                "':' — it is written into cache keys and stored rows"
            )


@dataclass(frozen=True, slots=True)
class SettingScope:
    """One level of one tenant's settings — the unit precedence is expressed in.

    `scope_id` is None for a level that has no instance: `platform` (there is
    one deployment) and `tenant` (the value applies to the whole tenant). Any
    finer kind names the thing it belongs to.

    `tenant_id` is None only for `platform`. Every other scope is inside a
    tenant, which is what keeps isolation a stored fact.
    """

    kind: str
    tenant_id: UUID | None = None
    scope_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind == PLATFORM:
            if self.tenant_id is not None or self.scope_id is not None:
                raise ScopeError(
                    "the platform scope belongs to no tenant and has no "
                    "instance — pass neither tenant_id nor scope_id"
                )
        elif self.tenant_id is None:
            raise ScopeError(
                f"scope {self.kind!r} needs a tenant_id: every scope other than "
                f"{PLATFORM!r} lives inside a tenant, and that is what keeps "
                "isolation a stored fact rather than an inference"
            )
        elif self.kind == TENANT and self.scope_id is not None:
            raise ScopeError(
                "the tenant scope applies to the whole tenant and has no "
                "instance — pass tenant_id only"
            )

    @classmethod
    def platform(cls) -> SettingScope:
        return cls(kind=PLATFORM)

    @classmethod
    def tenant(cls, tenant_id: UUID) -> SettingScope:
        return cls(kind=TENANT, tenant_id=tenant_id)


KERNEL_SCOPE_KINDS: tuple[ScopeKindSpec, ...] = (
    ScopeKindSpec(
        kind=PLATFORM,
        rank=PLATFORM_RANK,
        description="The deployment. Every tenant reads it; no tenant writes it.",
    ),
    ScopeKindSpec(
        kind=TENANT,
        rank=TENANT_RANK,
        description="One whole tenant.",
    ),
)


@dataclass(frozen=True)
class ScopeKindRegistry:
    """The declared scope kinds, ordered. Construction IS validation."""

    spec_by_kind: Mapping[str, ScopeKindSpec]

    def __post_init__(self) -> None:
        # A frozen dataclass holding a `dict` is not immutable — the field
        # cannot be rebound but the mapping can still be mutated.
        object.__setattr__(
            self, "spec_by_kind", MappingProxyType(dict(self.spec_by_kind))
        )
        ranks = [spec.rank for spec in self.spec_by_kind.values()]
        if len(set(ranks)) != len(ranks):
            raise ScopeError(
                "two scope kinds share a rank, so their precedence is undefined "
                f"— ranks: {sorted(ranks)}"
            )

    @classmethod
    def from_specs(cls, specs: Iterable[ScopeKindSpec]) -> ScopeKindRegistry:
        by_kind: dict[str, ScopeKindSpec] = {}
        for spec in specs:
            existing = by_kind.get(spec.kind)
            if existing is not None and existing != spec:
                raise DuplicateScopeKindError(
                    f"scope kind {spec.kind!r} declared twice with different "
                    "definitions — a level has one owner, because its rank "
                    "decides what overrides what"
                )
            by_kind[spec.kind] = spec
        return cls(by_kind)

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> ScopeKindRegistry:
        """The kernel's own kinds plus whatever modules declare."""
        return cls.from_specs(
            (
                *KERNEL_SCOPE_KINDS,
                *(spec for manifest in manifests for spec in manifest.scope_kinds),
            )
        )

    def require(self, kind: str) -> ScopeKindSpec:
        try:
            return self.spec_by_kind[kind]
        except KeyError:
            raise UndeclaredScopeKindError(
                f"scope kind {kind!r} is not declared by any installed module — "
                "declare a `ScopeKindSpec` on the owning module's manifest "
                "(`scope_kinds=(...)`) naming where it sits in the hierarchy"
            ) from None

    def is_declared(self, kind: str) -> bool:
        return kind in self.spec_by_kind

    def kinds(self) -> tuple[ScopeKindSpec, ...]:
        """Every declared kind, MOST SPECIFIC FIRST — resolution order."""
        return tuple(
            sorted(self.spec_by_kind.values(), key=lambda s: s.rank, reverse=True)
        )


def resolution_chain(
    scope: SettingScope, registry: ScopeKindRegistry
) -> tuple[SettingScope, ...]:
    """The scopes to try, most specific first, ending at platform.

    A read from a `site` scope falls back through its tenant to the deployment,
    because a value set for the whole tenant is a real answer for that site and
    a deployment default is a real answer for that tenant. The chain is derived
    from declared ranks rather than written out, so a product that inserts a
    level gets it in the right place without editing the resolver.
    """
    registry.require(scope.kind)
    chain: list[SettingScope] = [scope]
    if scope.kind not in (PLATFORM, TENANT) and scope.tenant_id is not None:
        chain.append(SettingScope.tenant(scope.tenant_id))
    if scope.kind != PLATFORM:
        chain.append(SettingScope.platform())
    return tuple(chain)


_active: ScopeKindRegistry = ScopeKindRegistry.from_specs(KERNEL_SCOPE_KINDS)


def install_scope_kinds(registry: ScopeKindRegistry) -> None:
    """Install the process-active registry (called by `create_app`)."""
    global _active
    _active = registry


def active_scope_kinds() -> ScopeKindRegistry:
    """The process-active registry.

    Defaults to the kernel's own two kinds rather than to empty: a process that
    has built no app still needs platform and tenant to resolve anything, and an
    undeclared kind is a code defect rather than untrusted input.
    """
    return _active


__all__ = [
    "KERNEL_SCOPE_KINDS",
    "PLATFORM",
    "PLATFORM_RANK",
    "TENANT",
    "TENANT_RANK",
    "DuplicateScopeKindError",
    "ScopeError",
    "ScopeKindRegistry",
    "ScopeKindSpec",
    "SettingScope",
    "UndeclaredScopeKindError",
    "active_scope_kinds",
    "install_scope_kinds",
    "resolution_chain",
]
