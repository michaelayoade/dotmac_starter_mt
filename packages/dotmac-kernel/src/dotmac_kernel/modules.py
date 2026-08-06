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

The directive's `ModuleManifest` sketch also lists `settings`, `feature_flags`,
`entity_types`, and `health_checks`. Those belong to later program steps (5 =
typed flags), and the same directive's governance list says CI must fail when "a
declaration has no consumer". Adding those fields now would ship exactly that:
declarations nothing derives behavior from. Each lands with the registry code
that consumes it — as `permissions` and `audit_actions` did in step 3, which
landed together with `dotmac_kernel.permissions` (consumed by
`dotmac_kernel.deps.require_permission` and validated at boot by `create_app`)
and `dotmac_kernel.audit_actions` (consumed by
`dotmac_kernel.audit.write_audit_event`).

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
from dotmac_kernel.namespaces import (
    HOST_SCHEMA,
    MigrationOwner,
    NamespaceRegistry,
    module_schema,
    validate_branch_label,
    validate_migration_prefix,
    validate_short_code,
)
from dotmac_kernel.permissions import PermissionSpec

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
    # Permissions this module declares and owns — the actor-authorization
    # counterpart of `capabilities` (tenant entitlement). Referenced by
    # `dotmac_kernel.deps.require_permission`; see `dotmac_kernel.permissions`.
    permissions: Sequence[PermissionSpec] = field(default_factory=tuple)
    # Audit actions this module declares and owns. Enforced at the write by
    # `dotmac_kernel.audit.write_audit_event`; see `dotmac_kernel.audit_actions`.
    audit_actions: Sequence[str] = field(default_factory=tuple)
    # ── D1: database namespace + migration lineage identity (ADR-0006) ──────
    # `short_code` is the registry-ALLOCATED database identity of a STATEFUL
    # module (see `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`). Its schema
    # is the derived, read-only `db_schema` property — `mod_<short_code>` — so
    # there is no settable schema attribute anything can re-point at runtime,
    # and the namespace is never inferred from `code` or any display name.
    # A STATELESS module leaves all four fields at their defaults: it declares
    # no database namespace at all.
    short_code: str | None = None
    # Immutable, globally unique short prefix for this owner's revision ids
    # (`<prefix>_<sequence>_<slug>`). Allocated in the same ledger row.
    migration_prefix: str | None = None
    # The module lineage's own Alembic branch label. Defaults to `code`; an
    # explicit value exists so a module whose code is not a legal label (or
    # which must keep a historical label through a rename) can still declare
    # one. Globally unique — it is how an `alembic_version` row is attributed.
    migration_branch: str | None = None
    # The unqualified tables this module OWNS inside its schema. The composed
    # migration gate rejects a create outside this declaration; the live-catalog
    # gate checks the declaration in both directions after migrations run.
    tables: Sequence[str] = field(default_factory=tuple)
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
            "permissions",
            "audit_actions",
            "tables",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self._validate_namespace()

    def _validate_namespace(self) -> None:
        """Stateful and stateless are the only two coherent shapes (D1).

        A module either declares a full database identity (short code AND
        migration prefix, from one ledger row) or none of it. A half-declared
        module — tables with no schema, a prefix with no namespace — would
        write into `public`, which is exactly the compatibility namespace D1
        closes to installable modules.
        """
        stateful_signals = (
            self.short_code is not None,
            self.migration_prefix is not None,
            bool(self.tables),
        )
        if not any(stateful_signals):
            if self.migration_branch is not None:
                raise ModuleRegistryError(
                    f"module {self.code!r} declares a migration branch but no "
                    "database namespace — a stateless module owns no lineage"
                )
            return
        if self.short_code is None:
            raise ModuleRegistryError(
                f"module {self.code!r} declares migration/table state but no "
                f"`short_code`, so its tables would land in {HOST_SCHEMA!r} — "
                "the compatibility namespace, which is not available to "
                "installable modules (ADR-0006 D1)"
            )
        if self.migration_prefix is None:
            raise ModuleRegistryError(
                f"module {self.code!r} owns schema {self.db_schema!r} but "
                "declares no `migration_prefix` — a stateful module needs a "
                "lineage of its own"
            )
        validate_short_code(self.short_code)
        validate_migration_prefix(self.migration_prefix)
        validate_branch_label(self.migration_branch or self.code)
        seen: set[str] = set()
        for table in self.tables:
            if table in seen:
                raise ModuleRegistryError(
                    f"module {self.code!r} declares table {table!r} twice"
                )
            seen.add(table)

    # ── D1 derived views ────────────────────────────────────────────────────

    @property
    def db_schema(self) -> str | None:
        """The module's immutable Postgres schema, or `None` if stateless.

        Derived, read-only, and built only by `namespaces.module_schema` — the
        `mod_` form is structural, not a convention a manifest can opt out of.
        """
        return None if self.short_code is None else module_schema(self.short_code)

    @property
    def is_stateful(self) -> bool:
        """True when this module owns a database namespace."""
        return self.short_code is not None

    def migration_owner(self) -> MigrationOwner | None:
        """This module's migration-lineage owner, or `None` if stateless.

        Declaration only — `NamespaceRegistry.from_manifests` is what checks it
        against the immutable ledger allocation.
        """
        if self.short_code is None or self.migration_prefix is None:
            return None
        return MigrationOwner(
            owner=self.code,
            prefix=self.migration_prefix,
            branch_label=self.migration_branch or self.code,
            db_schema=module_schema(self.short_code),
        )

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
        short_code: str | None = None,
        migration_prefix: str | None = None,
        migration_branch: str | None = None,
        tables: Sequence[str] = (),
    ) -> ModuleManifest:
        """Adapt a `FeatureManifest` into a `ModuleManifest`.

        The keyword arguments exist so an assembly can enrich a feature it has
        not yet migrated (pin a real version, declare its edges) without
        rewriting the feature package. Defaults preserve today's meaning
        exactly: no declared version, no dependencies, and — for D1 — no
        database namespace, which is correct for a host-assembly feature: its
        tables live in the `public` compatibility namespace, owned by the
        `assembly` migration owner, not by the feature.
        """
        return cls(
            code=manifest.name,
            version=version,
            contract_version=contract_version,
            dependencies=dependencies,
            short_code=short_code,
            migration_prefix=migration_prefix,
            migration_branch=migration_branch,
            tables=tables,
            api_routers=manifest.routers,
            web_routers=manifest.web_routers,
            nav=manifest.nav,
            capabilities=manifest.capabilities,
            permissions=manifest.permissions,
            audit_actions=manifest.audit_actions,
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
    # D1: the module's Postgres schema (`None` = stateless), and the branch
    # label its `alembic_version` rows carry. Reported so an operator can
    # explain any row in the composed version table from the inventory alone.
    db_schema: str | None = None
    migration_branch: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-safe row — the shape a diagnostics payload embeds."""
        return {
            "code": self.code,
            "version": self.version,
            "contract_version": self.contract_version,
            "dependencies": list(self.dependencies),
            "core": self.core,
            "enabled": self.enabled,
            "db_schema": self.db_schema,
            "migration_branch": self.migration_branch,
        }


class ModuleRegistry:
    """The validated set of installed modules, in deterministic startup order.

    Construction is validation: a registry that exists is a module set that
    passed every check (unique codes, supported contract versions, satisfied
    dependencies, acyclic, coherent database namespaces). Module failures raise
    a `ModuleRegistryError` subclass; namespace failures raise a
    `dotmac_kernel.namespaces.NamespaceError` subclass — fail closed either
    way, never a partially-loaded deployment.

    D1 (ADR-0006): this registry is also where a module's database namespace
    and migration prefix are ASSIGNED. It builds a `NamespaceRegistry` from the
    installed manifests plus the kernel's immutable allocation ledger, so a
    module that invented, re-pointed, or contested a namespace cannot be
    registered at all.
    """

    __slots__ = ("_by_code", "_namespaces", "_order")

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
        self._namespaces: NamespaceRegistry = NamespaceRegistry.from_manifests(declared)
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

    def namespaces(self) -> NamespaceRegistry:
        """The validated D1 namespace/migration-owner composition for this
        module set — what the composed migration gate and the post-migration
        live-catalog gate both read."""
        return self._namespaces

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
                db_schema=m.db_schema,
                migration_branch=(
                    (m.migration_branch or m.code) if m.is_stateful else None
                ),
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
            # D1 item 5: `alembic_version` stays the migration truth, and this
            # is the attribution that makes its rows explainable — every branch
            # label in the composed version table maps to exactly one owner and
            # (for a module) one schema.
            "migration_owners": [
                {
                    "owner": owner.owner,
                    "prefix": owner.prefix,
                    "branch_label": owner.branch_label,
                    "db_schema": owner.db_schema,
                }
                for owner in sorted(self._namespaces.owners(), key=lambda o: o.owner)
            ],
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
