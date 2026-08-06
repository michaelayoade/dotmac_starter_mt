"""ProductAssemblySpec — the declaration of what a product assembly IS
(kernel-boundary Task 3A).

A product (the reference `app/`, `dotmac_sub`, the vendor control plane, ...) is
a thin assembly over the kernel: a frozen spec pins its modules, its
configuration, and its own template/static/migration directories, and
`dotmac_kernel.create_app(spec)` turns it into a running FastAPI app. The spec
is the single declaration point — a product's `main.py` shrinks to building one
spec and calling `create_app`.

`modules` carries the assembly's manifests — `ModuleManifest`s and/or
not-yet-migrated `FeatureManifest`s, freely mixed. `create_app` validates them
into a `ModuleRegistry` (`dotmac_kernel.modules`) before mounting anything, so
an incoherent module set (duplicate code, unsupported contract version, missing
dependency, cycle) fails at startup rather than at first request.
`settings_overrides`, `branding`, and `providers` are declared here so a
downstream assembly can supply them even where the reference assembly leaves
them empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from dotmac_kernel.modules import AnyManifest


@dataclass(frozen=True)
class ProductAssemblySpec:
    """Immutable description of a product assembly. Constructed by the
    assembly's own composition module (e.g. `app/assembly.py`) and passed to
    `create_app`. Frozen: the spec cannot be mutated after construction, and
    its collection fields are normalized to immutable containers in
    `__post_init__`."""

    name: str
    # The assembly's module manifests (already loaded, e.g. via
    # `load_manifests(FEATURE_MODULES)`). FEATURE_MODULES stays assembly-owned.
    # Either shape is accepted: a `ModuleManifest` (versioned, with declared
    # dependencies) or a not-yet-migrated `FeatureManifest`, which `create_app`
    # adapts when it builds the `ModuleRegistry`. The two may be MIXED in one
    # assembly — that is what makes migrating feature packages incremental.
    modules: Sequence[AnyManifest] = ()
    # Per-deployment setting overrides applied on top of env/defaults. Declared
    # for downstream assemblies; the reference assembly leaves it empty.
    settings_overrides: Mapping[str, object] = field(default_factory=dict)
    # Deployment-static branding (a BrandSpec or None). None = the kernel's
    # env/`brand.json` resolution (see `dotmac_kernel.branding`).
    branding: object | None = None
    # Provider implementations keyed by interface (e.g. a ProvisioningProvider).
    # Empty for the reference assembly.
    providers: Mapping[str, object] = field(default_factory=dict)
    # Whole-portal surface switch — mount the admin/HTML surface or run API-only.
    web_enabled: bool = True
    # Feature/module names to disable (JSON API + web together), per deployment.
    disabled_modules: frozenset[str] = frozenset()
    # The assembly's own template directory — layered OVER the kernel templates
    # (ChoiceLoader precedence) when set. None = kernel templates only.
    assembly_template_dir: Path | None = None
    # The assembly's own static directory — layered OVER the kernel static when
    # set. None = the kernel's packaged static only.
    assembly_static_dir: Path | None = None
    # The assembly's own Alembic version directory (composed with the kernel
    # base migrations via `version_locations`). Consumed by the assembly's
    # Alembic config, not by `create_app`; carried here for a complete spec.
    assembly_migrations: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", tuple(self.modules))
        object.__setattr__(
            self, "settings_overrides", MappingProxyType(dict(self.settings_overrides))
        )
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))
        object.__setattr__(self, "disabled_modules", frozenset(self.disabled_modules))


__all__ = ["ProductAssemblySpec"]
