"""Module manifest + registry (module control-plane directive, step 2).

`ModuleManifest` is the **versioned** expansion of `FeatureManifest`: the single
declaration point for what an installed module IS. `ModuleRegistry` is the one
place that answers "is the installed set of modules coherent?" — unique codes,
compatible contract versions, satisfied dependencies, no cycles — and derives a
**deterministic startup order** plus the installed-module/version inventory that
health and diagnostics surfaces report.

Pure and in-memory, like `capabilities` and `profiles`: no database, no I/O, no
FastAPI app. It **describes installed code**; it never grants entitlement (WS2
owns that) and it never deploys anything (the vendor control plane owns that).

## What this step deliberately does NOT declare

The directive's `ModuleManifest` sketch also lists `permissions`, `settings`,
`feature_flags`, `audit_actions`, `entity_types`, and `health_checks`. Those
belong to later program steps (3 = permissions/audit actions, 5 = typed flags),
and the same directive's governance list says CI must fail when "a declaration
has no consumer". Adding those fields now would ship exactly that: six
declarations nothing derives behavior from. Each lands with the registry code
that consumes it.

## Compatibility with `FeatureManifest`

Existing consumers keep working, unchanged, two ways:

1. **Adaptation** — the registry accepts a `FeatureManifest` anywhere a
   `ModuleManifest` is expected and adapts it via `ModuleManifest.from_feature`
   (`name` → `code`, `routers` → `api_routers`, no declared version →
   `UNVERSIONED`, no dependencies).
2. **Aliases** — `ModuleManifest` exposes read-only `name` and `routers`
   properties, so `mount_features`, `install_surface_globals`,
   `CapabilityCatalogue.from_manifests`, and an assembly's own manifest-walking
   code accept an adapted module without a single call-site change.

## Determinism

`startup_order()` is a pure function of (declaration order, dependency edges):
dependencies first, **declaration order as the tiebreak**. Declaration order —
not alphabetical — is the tiebreak on purpose: an assembly's `FEATURE_MODULES`
list is a deliberate mount order (route matching is first-match-wins), so
introducing the registry must not silently reorder an assembly whose modules
declare no dependencies. Same manifests in, same order out, every boot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Final

from fastapi import APIRouter

from dotmac_kernel.features import FeatureManifest, NavItem

# The module-contract generation this kernel implements. A module declares the
# generation it was BUILT against (`ModuleManifest.contract_version`); the
# kernel declares which generations it can still load. Bump
# KERNEL_MODULE_CONTRACT_VERSION when the manifest contract changes in a way a
# module author must react to, and drop the old value from
# SUPPORTED_MODULE_CONTRACT_VERSIONS only when support genuinely ends — an
# installed module built for an unsupported generation must fail loudly at
# startup, never load half-understood.
KERNEL_MODULE_CONTRACT_VERSION: Final[int] = 1
SUPPORTED_MODULE_CONTRACT_VERSIONS: Final[frozenset[int]] = frozenset({1})

# The version recorded for a module adapted from a `FeatureManifest`, which
# declares no version of its own. It is deliberately a real, sortable version
# rather than `None` or `"unknown"`: the inventory always has a version column,
# and `0.0.0` reads unambiguously as "this module has not declared one yet".
UNVERSIONED: Final[str] = "0.0.0"


class ModuleRegistryError(ValueError):
    """Base for every fail-closed module-registry validation error, so a caller
    can catch the whole class of "the installed module set is incoherent"."""


class DuplicateModuleError(ModuleRegistryError):
    """Two manifests declared the same module code — there is no single owner."""


class ModuleContractVersionError(ModuleRegistryError):
    """A module declares a contract version this kernel does not support."""


class MissingModuleDependencyError(ModuleRegistryError):
    """A module depends on a code that is not installed (or, for
    `enabled_order`, not enabled in this deployment)."""


class ModuleDependencyCycleError(ModuleRegistryError):
    """The dependency graph contains a cycle, so no startup order exists."""


class UnknownModuleError(KeyError):
    """A module code was looked up that is not registered."""


@dataclass(frozen=True)
class ModuleManifest:
    """One installed module's declaration (module control-plane directive).

    `code` is the stable identifier every other authority references (a
    dependency edge, a deployment profile's required/forbidden set, a capability
    owner). `version` is the module's own release version; `contract_version` is
    the kernel manifest generation it was built against — the two are
    independent, and only the latter gates loading.
    """

    code: str
    version: str
    contract_version: int = KERNEL_MODULE_CONTRACT_VERSION
    # Module codes that must be installed (and, at mount time, enabled) for this
    # module to work. Edges, not imports: modules still never import each other.
    dependencies: Sequence[str] = field(default_factory=tuple)
    # JSON API routers — mounted for every enabled module, always (`web_enabled`
    # has no say). Named per the directive; `routers` below is the compat alias.
    api_routers: Sequence[APIRouter] = field(default_factory=tuple)
    # HTML/HTMX admin-portal routers — mounted only when `web_enabled` is True.
    web_routers: Sequence[APIRouter] = field(default_factory=tuple)
    nav: Sequence[NavItem] = field(default_factory=tuple)
    # Capability codes this module declares (see `dotmac_kernel.capabilities`).
    capabilities: Sequence[str] = field(default_factory=tuple)
    core: bool = True
    enabled_by_default: bool = True
    seed: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ModuleRegistryError("module manifest requires a non-empty `code`")
        if not self.version:
            raise ModuleRegistryError(
                f"module {self.code!r} requires a non-empty `version`"
            )
        for name in (
            "dependencies",
            "api_routers",
            "web_routers",
            "nav",
            "capabilities",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    # ── Compatibility aliases ───────────────────────────────────────────────
    # Read-only views under the `FeatureManifest` names, so every existing
    # manifest consumer accepts a `ModuleManifest` unchanged. They are
    # properties (not fields) precisely so there is exactly one stored value per
    # concept and the two names can never drift apart.

    @property
    def name(self) -> str:
        """`FeatureManifest.name` alias for `code`."""
        return self.code

    @property
    def routers(self) -> Sequence[APIRouter]:
        """`FeatureManifest.routers` alias for `api_routers`."""
        return self.api_routers

    @classmethod
    def from_feature(
        cls,
        manifest: FeatureManifest,
        *,
        version: str = UNVERSIONED,
        contract_version: int = KERNEL_MODULE_CONTRACT_VERSION,
        dependencies: Sequence[str] = (),
    ) -> ModuleManifest:
        """Adapt a `FeatureManifest` into a `ModuleManifest`.

        The keyword arguments exist so an assembly can enrich a feature it has
        not yet migrated (pin a real version, declare its edges) without
        rewriting the feature package. Defaults preserve today's meaning
        exactly: no declared version, no dependencies.
        """
        return cls(
            code=manifest.name,
            version=version,
            contract_version=contract_version,
            dependencies=dependencies,
            api_routers=manifest.routers,
            web_routers=manifest.web_routers,
            nav=manifest.nav,
            capabilities=manifest.capabilities,
            core=manifest.core,
            enabled_by_default=manifest.enabled_by_default,
            seed=manifest.seed,
        )


# Anywhere the kernel walks "the assembly's modules", either shape is accepted:
# a not-yet-migrated `FeatureManifest` or a full `ModuleManifest`.
AnyManifest = FeatureManifest | ModuleManifest


@dataclass(frozen=True, slots=True)
class ModuleInventoryEntry:
    """One row of the installed-module inventory reported to health/diagnostics.

    `enabled` is deployment state (this deployment's `disabled` set +
    `enabled_by_default`); everything else is what the installed code declares.
    """

    code: str
    version: str
    contract_version: int
    dependencies: tuple[str, ...]
    core: bool
    enabled: bool

    def as_dict(self) -> dict[str, object]:
        """JSON-safe row — the shape a diagnostics payload embeds."""
        return {
            "code": self.code,
            "version": self.version,
            "contract_version": self.contract_version,
            "dependencies": list(self.dependencies),
            "core": self.core,
            "enabled": self.enabled,
        }


class ModuleRegistry:
    """The validated set of installed modules, in deterministic startup order.

    Construction is validation: a registry that exists is a module set that
    passed every check (unique codes, supported contract versions, satisfied
    dependencies, acyclic). All four failures raise a `ModuleRegistryError`
    subclass — fail closed, never a partially-loaded deployment.
    """

    __slots__ = ("_by_code", "_order")

    def __init__(
        self,
        manifests: Iterable[AnyManifest],
        *,
        supported_contract_versions: Set[int] = SUPPORTED_MODULE_CONTRACT_VERSIONS,
    ) -> None:
        declared = [
            m if isinstance(m, ModuleManifest) else ModuleManifest.from_feature(m)
            for m in manifests
        ]
        self._check_unique_codes(declared)
        self._check_contract_versions(declared, supported_contract_versions)
        self._check_dependencies_installed(declared)
        self._order: tuple[ModuleManifest, ...] = self._topological_order(declared)
        self._by_code: dict[str, ModuleManifest] = {m.code: m for m in self._order}

    # ── Validation ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_unique_codes(manifests: Sequence[ModuleManifest]) -> None:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for manifest in manifests:
            if manifest.code in seen:
                duplicates.add(manifest.code)
            seen.add(manifest.code)
        if duplicates:
            listed = ", ".join(repr(code) for code in sorted(duplicates))
            raise DuplicateModuleError(
                f"module code(s) declared by more than one manifest: {listed} — "
                "a module code has exactly one owning manifest"
            )

    @staticmethod
    def _check_contract_versions(
        manifests: Sequence[ModuleManifest], supported: Set[int]
    ) -> None:
        bad = sorted(
            (m.code, m.contract_version)
            for m in manifests
            if m.contract_version not in supported
        )
        if bad:
            listed = ", ".join(f"{code!r} (contract v{ver})" for code, ver in bad)
            supported_listed = ", ".join(str(v) for v in sorted(supported))
            raise ModuleContractVersionError(
                f"module(s) built against an unsupported manifest contract: "
                f"{listed}; this kernel supports contract version(s) "
                f"{supported_listed}"
            )

    @staticmethod
    def _check_dependencies_installed(manifests: Sequence[ModuleManifest]) -> None:
        installed = {m.code for m in manifests}
        missing = sorted(
            (m.code, dep)
            for m in manifests
            for dep in m.dependencies
            if dep not in installed
        )
        if missing:
            listed = ", ".join(f"{code!r} -> {dep!r}" for code, dep in missing)
            raise MissingModuleDependencyError(
                f"module dependency not installed: {listed}"
            )

    @staticmethod
    def _topological_order(
        manifests: Sequence[ModuleManifest],
    ) -> tuple[ModuleManifest, ...]:
        """Dependencies first, declaration order as the tiebreak (see module
        docstring). Repeatedly emits the EARLIEST-DECLARED manifest whose
        dependencies are all already emitted; if none qualifies, what remains
        contains a cycle."""
        remaining = list(manifests)
        emitted: set[str] = set()
        order: list[ModuleManifest] = []
        while remaining:
            for index, manifest in enumerate(remaining):
                if all(dep in emitted for dep in manifest.dependencies):
                    order.append(manifest)
                    emitted.add(manifest.code)
                    del remaining[index]
                    break
            else:
                cycle = ModuleRegistry._find_cycle(remaining)
                raise ModuleDependencyCycleError(
                    "module dependency cycle: " + " -> ".join(cycle)
                )
        return tuple(order)

    @staticmethod
    def _find_cycle(manifests: Sequence[ModuleManifest]) -> list[str]:
        """One concrete cycle among `manifests`, as a closed path
        (`a -> b -> a`) — a diagnosable message beats "there is a cycle".

        Deterministic: nodes are visited in declaration order and each node's
        edges in declared order, so the same graph always names the same cycle.
        """
        deps = {m.code: m.dependencies for m in manifests}
        path: list[str] = []
        on_path: set[str] = set()
        settled: set[str] = set()

        def walk(code: str) -> list[str] | None:
            if code in on_path:
                return [*path[path.index(code) :], code]
            if code in settled or code not in deps:
                return None
            path.append(code)
            on_path.add(code)
            for dep in deps[code]:
                found = walk(dep)
                if found is not None:
                    return found
            path.pop()
            on_path.discard(code)
            settled.add(code)
            return None

        for manifest in manifests:
            found = walk(manifest.code)
            if found is not None:
                return found
        # Unreachable: `_topological_order` only calls this when it is stuck,
        # which means a cycle exists among `manifests`.
        raise ModuleDependencyCycleError(
            "module dependency cycle among: "
            + ", ".join(sorted(m.code for m in manifests))
        )

    # ── Reading the registry ────────────────────────────────────────────────

    def startup_order(self) -> tuple[ModuleManifest, ...]:
        """Every INSTALLED module, dependencies first (see module docstring)."""
        return self._order

    def codes(self) -> frozenset[str]:
        """Every installed module code."""
        return frozenset(self._by_code)

    def get(self, code: str) -> ModuleManifest:
        try:
            return self._by_code[code]
        except KeyError as exc:
            raise UnknownModuleError(f"module {code!r} is not installed") from exc

    def is_installed(self, code: str) -> bool:
        return code in self._by_code

    def enabled_codes(self, disabled: Set[str] = frozenset()) -> frozenset[str]:
        """Installed AND deployment-enabled: not in `disabled`, and not opted out
        via `enabled_by_default=False`. This is the single definition of
        "enabled" the mount/seed/inventory paths all read."""
        return frozenset(
            m.code
            for m in self._order
            if m.code not in disabled and m.enabled_by_default
        )

    def enabled_order(
        self, disabled: Set[str] = frozenset()
    ) -> tuple[ModuleManifest, ...]:
        """`startup_order()` filtered to the enabled modules.

        Fails closed when an enabled module depends on one that is NOT enabled:
        "dependencies satisfied" means the dependency is actually running, not
        merely present on disk. Disabling a module that something else needs is
        a deployment misconfiguration, and it must surface at startup rather
        than as a mystery 500 on the first request that crosses the edge.
        """
        enabled = self.enabled_codes(disabled)
        unsatisfied = sorted(
            (m.code, dep)
            for m in self._order
            if m.code in enabled
            for dep in m.dependencies
            if dep not in enabled
        )
        if unsatisfied:
            listed = ", ".join(f"{code!r} -> {dep!r}" for code, dep in unsatisfied)
            raise MissingModuleDependencyError(
                f"enabled module depends on a module that is not enabled: {listed}"
            )
        return tuple(m for m in self._order if m.code in enabled)

    # ── Inventory (health / diagnostics) ────────────────────────────────────

    def inventory(
        self, disabled: Set[str] = frozenset()
    ) -> tuple[ModuleInventoryEntry, ...]:
        """The installed-module/version inventory, sorted by code.

        Sorted by CODE, not startup order, because this is a report a human or a
        monitor diffs across deployments — a stable row order makes two
        inventories comparable. `startup_order` is reported separately by
        `inventory_payload`.
        """
        enabled = self.enabled_codes(disabled)
        return tuple(
            ModuleInventoryEntry(
                code=m.code,
                version=m.version,
                contract_version=m.contract_version,
                dependencies=tuple(m.dependencies),
                core=m.core,
                enabled=m.code in enabled,
            )
            for m in sorted(self._order, key=lambda m: m.code)
        )

    def inventory_payload(
        self, disabled: Set[str] = frozenset()
    ) -> Mapping[str, object]:
        """JSON-safe diagnostics payload: what is installed, at what version,
        which are enabled, and the order they start in.

        The kernel provides the CONTRACT, not an endpoint. Public `/health` is
        liveness only and deliberately discloses none of this; an authenticated
        platform diagnostics surface is the module control-plane's own step and
        composes this payload.
        """
        return {
            "kernel_contract_version": KERNEL_MODULE_CONTRACT_VERSION,
            "modules": [entry.as_dict() for entry in self.inventory(disabled)],
            "startup_order": [m.code for m in self.enabled_order(disabled)],
        }


__all__ = [
    "KERNEL_MODULE_CONTRACT_VERSION",
    "SUPPORTED_MODULE_CONTRACT_VERSIONS",
    "UNVERSIONED",
    "AnyManifest",
    "ModuleManifest",
    "ModuleInventoryEntry",
    "ModuleRegistry",
    "ModuleRegistryError",
    "DuplicateModuleError",
    "ModuleContractVersionError",
    "MissingModuleDependencyError",
    "ModuleDependencyCycleError",
    "UnknownModuleError",
]
