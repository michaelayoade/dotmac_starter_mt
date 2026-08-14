"""Logical migration prerequisites — what a lineage needs, not who provides it.

A module lineage that needs another lineage's *effects* has, until now, had one
way to say so: a physical Alembic edge naming a foreign revision.

```python
depends_on = ("0001_initial_tenant_schema",)   # dotmac-files, before this module
```

That edge is a lie in every assembly that does not run the named lineage. It
says "files needs kernel revision 0001", when what files actually needs is a
tenant catalogue to point a foreign key at and three database roles to grant to.
Kernel 0001 is one *provider* of those effects. It is not the requirement.

## Why the distinction is load-bearing

ERP hosts `public.tenants` in its own lineage (`20260813_tenant_projection`) and
therefore can never run kernel 0001 — that revision creates `public.tenants`
unconditionally as its first table, so the collision is permanent and structural.
Under the physical-edge model, `dotmac-files` is simply not installable in ERP,
and the only ways out are all forbidden: stamp the revision, patch the kernel
migration with a product conditional, or converge ERP's whole identity/RBAC/audit
estate onto kernel 0001 — coupling byte storage to credentials, sessions, roles
and audit for no domain reason.

Naming the requirement instead of the provider dissolves that. `dotmac-files`
requires `tenant_scope_catalog.v1`; the Starter binds it to kernel 0001's effects
and ERP binds it to `20260813_tenant_projection`. Both are truthful, and the
module names neither.

## The three parts

1. **Vocabulary** — a `PrerequisiteSpec` naming one verifiable database
   capability. An open **declaration registry**, never an enum (ADR-0008): a
   product names a new prerequisite without a kernel change.
2. **Declaration** — a module's `ModuleManifest.requires`, and the answering
   `MigrationOwner.provides`.
3. **Binding** — per assembly, checked in, installed at Alembic startup. This is
   what turns the logical requirement back into a real physical edge, so
   ordering stays exactly as correct as a hand-authored `depends_on`.

## A binding may not merely assert

The declaration is precisely the thing that can lie, so it is never the only
control. Two independent guards sit underneath it, both in
`dotmac_kernel.migrations.verify`:

- a **fail-closed live verifier** that inspects real catalog state before the
  requiring migration proceeds — the table shape, the function semantics, the
  roles — so a stamped or aliased provider fails against the database; and
- an **order canary** asserting the provider revision is already recorded in
  `alembic_version`, so a binding cannot point at a revision that has not run.

A binding that names a revision which neither exists nor ran fails at
`alembic upgrade`, loudly, before any DDL.

Pure and in-memory, like `namespaces`: no database, no I/O, no FastAPI app. The
SQL that verifies these prerequisites lives in `dotmac_kernel.migrations.verify`,
which is imported by migrations only.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Final

from dotmac_kernel.planes import ModulePlane, selected_module_planes

#: Dotted `module.path:ATTRIBUTE` naming this assembly's binding sequence, for
#: entry points that never run `env.py` (`alembic heads`, `history`, `show`).
BINDINGS_ENV_VAR: Final[str] = "DOTMAC_MIGRATION_BINDINGS"

# ── Name shape ──────────────────────────────────────────────────────────────

# `<name>.v<major>`. The version is part of the identity, not metadata: a
# prerequisite whose verified contract changes is a NEW prerequisite, because
# every existing binding was accepted against the old contract and silently
# re-pointing them is the drift this module exists to prevent.
_PREREQUISITE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]{2,47}\.v[1-9][0-9]{0,2}$"
)

# Alembic revision ids, as constrained by `alembic_version.version_num`. Kept
# deliberately permissive about *shape* — a binding may legitimately name a
# legacy host revision (`0001_initial_tenant_schema`) or a strict D1 module
# revision (`fi_0001_stored_files`).
_REVISION_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")


# ── Errors ──────────────────────────────────────────────────────────────────


class PrerequisiteError(ValueError):
    """Base for every fail-closed prerequisite declaration/binding error."""


class InvalidPrerequisiteNameError(PrerequisiteError):
    """A prerequisite name is malformed or carries no explicit version."""


class UnknownPrerequisiteError(PrerequisiteError):
    """A lineage declared a prerequisite that no spec registers."""


class DuplicatePrerequisiteError(PrerequisiteError):
    """Two specs registered the same prerequisite name."""


class UnboundPrerequisiteError(PrerequisiteError):
    """This assembly composes a module requiring a prerequisite it never bound.

    Fail-closed by construction: an assembly that provides no tenant scope
    cannot silently compose a module that needs one.
    """


class DuplicateBindingError(PrerequisiteError):
    """One prerequisite was bound to two providers in one assembly."""


class InvalidRevisionReferenceError(PrerequisiteError):
    """A binding names a provider revision that cannot be a revision id."""


# ── Vocabulary ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PrerequisiteSpec:
    """One named, verifiable database capability a lineage may require.

    `summary` is not decoration — it is what a reviewer reads when deciding
    whether a proposed binding is truthful, so it states the observable effects,
    not the intent.
    """

    name: str
    summary: str

    def __post_init__(self) -> None:
        validate_prerequisite_name(self.name)
        if not self.summary.strip():
            raise PrerequisiteError(
                f"prerequisite {self.name!r} needs a summary describing the "
                "observable database effects a provider must supply"
            )


def validate_prerequisite_name(name: str) -> None:
    if not _PREREQUISITE_RE.match(name):
        raise InvalidPrerequisiteNameError(
            f"prerequisite name {name!r} must match {_PREREQUISITE_RE.pattern} "
            "— an explicit `.vN` is required because the version is part of the "
            "identity, not metadata"
        )


def validate_revision_reference(revision: str) -> None:
    if not _REVISION_RE.match(revision):
        raise InvalidRevisionReferenceError(
            f"provider revision {revision!r} is not a usable Alembic revision id"
        )


#: `dotmac-files` needs a foreign-key target and an RLS predicate. Kernel 0001
#: supplies both; so does ERP's `20260813_tenant_projection`, in ERP's own
#: lineage, without any kernel identity/RBAC/audit table existing at all.
TENANT_SCOPE_CATALOG_V1: Final[PrerequisiteSpec] = PrerequisiteSpec(
    name="tenant_scope_catalog.v1",
    summary=(
        "public.tenants with the kernel Tenant column, key and index contract, "
        "and public.app_current_tenant_id() returning the app.current_tenant "
        "GUC as uuid (NULL when unset or malformed). No RLS on the catalogue."
    ),
)

#: The three roles module grants name. A module never creates them: creating a
#: role needs privileges a module migration must not assume, and a module that
#: invents its own roles is a second authority over cluster access.
MODULE_DATABASE_ROLES_V1: Final[PrerequisiteSpec] = PrerequisiteSpec(
    name="module_database_roles.v1",
    summary=(
        "Database roles app_admin (BYPASSRLS, offline/migration), app_user "
        "(online tenant routes, RLS-enforced) and platform_api (online platform "
        "routes) exist and can be granted to. Passwords are never set here."
    ),
)

KERNEL_PREREQUISITES: Final[tuple[PrerequisiteSpec, ...]] = (
    TENANT_SCOPE_CATALOG_V1,
    MODULE_DATABASE_ROLES_V1,
)


_lock = threading.Lock()
_registry: dict[str, PrerequisiteSpec] = {
    spec.name: spec for spec in KERNEL_PREREQUISITES
}


def register_prerequisites(specs: Iterable[PrerequisiteSpec]) -> None:
    """Register product-owned prerequisites (ADR-0008: a registry, not an enum).

    Re-registering an identical spec is a no-op so module import order cannot
    matter; re-registering the same NAME with a different summary is a
    duplicate-authority error.
    """
    with _lock:
        for spec in specs:
            existing = _registry.get(spec.name)
            if existing is not None and existing != spec:
                raise DuplicatePrerequisiteError(
                    f"prerequisite {spec.name!r} is already registered with a "
                    "different contract — a changed contract is a new `.vN`, "
                    "never a redefinition of the accepted one"
                )
            _registry[spec.name] = spec


def prerequisite(name: str) -> PrerequisiteSpec:
    """Resolve a registered spec, or fail closed naming the fix."""
    validate_prerequisite_name(name)
    try:
        return _registry[name]
    except KeyError:
        raise UnknownPrerequisiteError(
            f"prerequisite {name!r} is not registered — register a "
            "PrerequisiteSpec for it before any lineage declares it"
        ) from None


def registered_prerequisites() -> tuple[PrerequisiteSpec, ...]:
    """Every registered spec, name-sorted (deterministic for gates and tests)."""
    with _lock:
        return tuple(sorted(_registry.values(), key=lambda spec: spec.name))


def validate_prerequisites(names: Sequence[str]) -> tuple[str, ...]:
    """Validate a lineage's declared `requires`/`provides` list.

    Duplicates are rejected rather than de-duplicated: a list that names one
    prerequisite twice was written by someone who believed they were different.
    """
    seen: set[str] = set()
    for name in names:
        prerequisite(name)
        if name in seen:
            raise DuplicatePrerequisiteError(
                f"prerequisite {name!r} is declared twice in one list"
            )
        seen.add(name)
    return tuple(names)


# ── Binding ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PrerequisiteBinding:
    """This assembly's answer to "who provides it?".

    `provider_revision` is the revision whose effects satisfy the prerequisite —
    the one the order canary requires in `alembic_version`, and the one the
    resolved physical `depends_on` edge points at. `provider_owner` is the
    lineage owner that revision belongs to, carried so a review diff shows the
    binding crossing a lineage boundary rather than hiding it in a revision id.
    """

    prerequisite: str
    provider_revision: str
    provider_owner: str

    def __post_init__(self) -> None:
        validate_prerequisite_name(self.prerequisite)
        validate_revision_reference(self.provider_revision)
        if not self.provider_owner.strip():
            raise PrerequisiteError(
                f"binding for {self.prerequisite!r} must name the lineage owner "
                "that supplies it"
            )


_bindings: dict[str, PrerequisiteBinding] = {}


def install_prerequisite_bindings(
    bindings: Iterable[PrerequisiteBinding],
) -> None:
    """Install this assembly's bindings. Call from Alembic `env.py`, once.

    Same shape as the kernel's other product-supplied seams
    (`install_secret_source`, `install_key_provider`): the kernel ships no
    binding of its own, because only the assembly knows which of its revisions
    supplies an effect. Replaces any previously installed set, so a test can
    install its own without leaking into the next one.
    """
    resolved: dict[str, PrerequisiteBinding] = {}
    for binding in bindings:
        prerequisite(binding.prerequisite)
        if binding.prerequisite in resolved:
            raise DuplicateBindingError(
                f"prerequisite {binding.prerequisite!r} is bound twice — one "
                "assembly has exactly one provider for one effect"
            )
        resolved[binding.prerequisite] = binding
    with _lock:
        _bindings.clear()
        _bindings.update(resolved)


def installed_bindings() -> tuple[PrerequisiteBinding, ...]:
    """Every installed binding, prerequisite-sorted."""
    with _lock:
        return tuple(sorted(_bindings.values(), key=lambda b: b.prerequisite))


def binding_for(name: str) -> PrerequisiteBinding:
    """The binding for one prerequisite, or fail closed.

    The error names the assembly-side fix, because the reader hitting this is
    composing a module into an assembly that has not said how it supplies the
    effect — not editing the module.
    """
    prerequisite(name)
    with _lock:
        found = _bindings.get(name)
    if found is None:
        raise UnboundPrerequisiteError(
            f"this assembly composes a lineage requiring {name!r} but binds no "
            "provider for it — declare a PrerequisiteBinding naming the "
            "revision that supplies the effect, and install it from "
            "`env.py` via install_prerequisite_bindings()"
        )
    return found


def autoload_bindings() -> bool:
    """Install bindings named by ``DOTMAC_MIGRATION_BINDINGS``, if set.

    ``module.path:ATTRIBUTE``. Returns True when bindings were installed.

    This exists because `env.py` is not the only entry point into a revision
    map. `alembic heads`, `alembic history` and `alembic show` build the map
    WITHOUT running `env.py`, so a migration that resolved its edge purely from
    `install_prerequisite_bindings` made those commands crash. An environment
    variable is the one channel available to both paths.
    """
    spec = os.environ.get(BINDINGS_ENV_VAR, "").strip()
    if not spec:
        return False
    module_path, _, attribute = spec.partition(":")
    if not module_path or not attribute:
        raise PrerequisiteError(
            f"{BINDINGS_ENV_VAR}={spec!r} must be 'module.path:ATTRIBUTE' naming "
            "this assembly's PrerequisiteBinding sequence"
        )
    module = import_module(module_path)
    try:
        bindings = getattr(module, attribute)
    except AttributeError:
        raise PrerequisiteError(
            f"{BINDINGS_ENV_VAR} names {attribute!r} in {module_path!r}, which "
            "does not define it"
        ) from None
    install_prerequisite_bindings(bindings)
    return True


def is_bound(name: str) -> bool:
    """Has this assembly bound a provider for ``name``?

    This is observation only. Provider availability never selects a module
    plane; ``ModulePlaneSelection`` owns that independent assembly decision.
    """
    prerequisite(name)
    with _lock:
        installed = bool(_bindings)
    if not installed and not autoload_bindings():
        return False
    with _lock:
        return name in _bindings


def all_bound(names: Iterable[str]) -> bool:
    """True when every name is bound. Empty means True."""
    return all(is_bound(name) for name in names)


def resolve_depends_on(
    names: Sequence[str],
    *,
    module: str | None = None,
    tenant: Sequence[str] = (),
    platform: Sequence[str] = (),
) -> tuple[str, ...]:
    """Turn a lineage's logical `requires` into real Alembic `depends_on`.

    Called at module scope by a requiring migration:

    ```python
    depends_on = resolve_depends_on(
        ("tenant_scope_catalog.v1", "module_database_roles.v1")
    )
    ```

    This is the whole point of the indirection: the physical ordering edge still
    exists and Alembic orders exactly as it always did, but the edge is authored
    by the assembly that knows the answer rather than by the module that cannot.

    ## Why an unbound assembly does not raise here

    Raising at import looked right and broke `alembic heads`, `alembic history`
    and `alembic show`, none of which run `env.py` — so none of them had any
    bindings, and merely INSPECTING the graph crashed. A revision map built for
    inspection must not explode.

    So: nothing installed at all means nobody has answered yet, which is the
    graph-inspection case, and the edge resolves empty. Something installed but
    not THIS prerequisite is a real assembly misconfiguration and still raises.

    Ordering correctness does not rest on this function refusing. It rests on
    `env.py` installing bindings before every `alembic upgrade`, on the composed
    gate rejecting an unbound requirement at build time, and on
    `require_prerequisites` proving the effects against the database before any
    DDL. Set `DOTMAC_MIGRATION_BINDINGS` to keep the inspected graph faithful too.

    ``tenant`` and ``platform`` are not optional requirements. They are strict
    requirements of an explicitly selected module plane. The assembly selects
    through ``ModulePlaneSelection``; every requirement of that selection must
    then have a binding. This keeps provider availability from becoming an
    implicit installation flag.
    """
    validate_prerequisites(names)
    validate_prerequisites(tenant)
    validate_prerequisites(platform)
    groups = (set(names), set(tenant), set(platform))
    overlap = {
        name
        for name in set().union(*groups)
        if sum(name in group for group in groups) > 1
    }
    if overlap:
        raise DuplicatePrerequisiteError(
            f"{sorted(overlap)} declared in more than one prerequisite list — "
            "an effect is common, tenant-only, or platform-only"
        )
    if (tenant or platform) and not module:
        raise PrerequisiteError(
            "plane-specific prerequisites require the module code whose "
            "ModulePlaneSelection controls them"
        )
    with _lock:
        installed = bool(_bindings)
    if not installed and not autoload_bindings():
        return ()
    selected_names = list(names)
    if module is not None:
        planes = selected_module_planes(module)
        if ModulePlane.TENANT in planes:
            selected_names.extend(tenant)
        if ModulePlane.PLATFORM in planes:
            selected_names.extend(platform)
    return tuple(binding_for(name).provider_revision for name in selected_names)


def binding_map() -> Mapping[str, str]:
    """`{prerequisite: provider_revision}` — for gates and diagnostics."""
    return {b.prerequisite: b.provider_revision for b in installed_bindings()}


__all__ = [
    "BINDINGS_ENV_VAR",
    "KERNEL_PREREQUISITES",
    "all_bound",
    "autoload_bindings",
    "MODULE_DATABASE_ROLES_V1",
    "TENANT_SCOPE_CATALOG_V1",
    "DuplicateBindingError",
    "DuplicatePrerequisiteError",
    "InvalidPrerequisiteNameError",
    "InvalidRevisionReferenceError",
    "PrerequisiteBinding",
    "PrerequisiteError",
    "PrerequisiteSpec",
    "UnboundPrerequisiteError",
    "UnknownPrerequisiteError",
    "binding_for",
    "binding_map",
    "install_prerequisite_bindings",
    "installed_bindings",
    "is_bound",
    "prerequisite",
    "register_prerequisites",
    "registered_prerequisites",
    "resolve_depends_on",
    "validate_prerequisite_name",
    "validate_prerequisites",
    "validate_revision_reference",
]
