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
`setting_defaults`, `branding`, and `providers` are declared here so a
downstream assembly can supply them even where the reference assembly leaves
them empty.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from dotmac_kernel.modules import AnyManifest
from dotmac_kernel.planes import (
    ModulePlaneSelection,
    validate_module_plane_selections,
)
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    WebFacetMount,
)

StartupCheck = Callable[[], Sequence[str]]
StartupHook = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class ProductSecurityPolicy:
    """Product-owned defaults for browser response policy.

    The kernel still owns the middleware and its invariant baseline headers.
    This value declares only the policy that genuinely varies by product:
    content sources and cross-origin isolation. Environment configuration wins
    over ``content_security_policy`` so an operator can tighten a product
    default without mutating the assembly. A raw CSP must retain every computed
    baseline directive and may only remove sources; it is refused entirely when
    an active browser capability has typed CSP requirements. New mechanics use
    the typed capability vocabulary, never a raw policy string.
    """

    content_security_policy: str = ""
    cross_origin_opener_policy: str = ""
    cross_origin_resource_policy: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "content_security_policy",
            "cross_origin_opener_policy",
            "cross_origin_resource_policy",
        ):
            value = getattr(self, field_name)
            if "\r" in value or "\n" in value:
                raise ValueError(f"{field_name} must be a single HTTP header value")
            try:
                value.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"{field_name} must be representable as an HTTP header value"
                ) from exc


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
    # Explicit per-module plane intent for lineages that declare more than one
    # supported plane combination. This is distinct from migration bindings:
    # a product may physically have a tenant catalogue and still select only a
    # module's platform plane. Omitting a required selection fails construction.
    module_planes: Sequence[ModulePlaneSelection] = ()
    # The deployment's DECLARED DEFAULTS, keyed "<domain>/<key>".
    #
    # Named `setting_defaults` and not `settings_overrides` because the
    # direction matters and the old name stated the wrong one. These LOSE to
    # every stored row and WIN over the module's own fallback:
    #
    #     scope chain  ->  profile default  ->  spec fallback
    #
    # A module declares the QUESTION — that a setting exists, its type, its
    # constraints, whether it inherits. A deployment declares the ANSWER OF
    # LAST RESORT, because that is what genuinely varies by region, regime and
    # topology, and it is currently hardcoded in module code where a deployment
    # cannot reach it.
    #
    # The inverse — a profile value beating an operator's stored row — is the
    # defect ADR-0011 removed from `env_var`: it makes the settings screen lie
    # about what is in effect. Nothing here overrides a row.
    #
    # A default for a key no spec declares is rejected at startup: that is how
    # settings with no reader appear.
    setting_defaults: Mapping[str, object] = field(default_factory=dict)
    # Deployment-static branding (a BrandSpec or None). None = the kernel's
    # env/`brand.json` resolution (see `dotmac_kernel.branding`).
    branding: object | None = None
    # Provider implementations keyed by interface (e.g. a ProvisioningProvider).
    # Empty for the reference assembly.
    providers: Mapping[str, object] = field(default_factory=dict)
    # The deployment's tenancy TOPOLOGY, declared rather than inferred.
    #
    # This creates NO code path. ADR-0003 is explicit that a single-tenant
    # deployment "keeps Tenant, request tenant context, composite tenant
    # constraints, and RLS — it is a topology, not a second schema or code
    # path", and scattering `if tenancy == ...` through features is exactly
    # what that forbids. Nothing branches on this.
    #
    # What it buys is that the intent becomes checkable and answerable:
    #
    # * `create_app` asserts it — a deployment declaring `single` that grows a
    #   second tenant row is a misconfiguration someone should hear about,
    #   and today nothing would notice.
    # * Provisioning knows whether a second tenant is expected or a mistake.
    # * Settings scope stops being guesswork. `dotmac_erp` has six identifier
    #   settings that cannot safely be marked non-inheriting because nobody
    #   knows whether its rows are global or per-organisation — a question a
    #   declared topology answers instead of leaving to inspection.
    tenancy: str = "multi"
    # Whether the kernel's online platform-control-plane routers are exposed.
    # Dedicated and on-prem products commonly keep tenant/bootstrap
    # administration offline; that is a product surface decision and must not
    # require deleting FastAPI routes after the factory has validated them.
    platform_surface_enabled: bool = True
    # Whole-portal surface switch — mount the admin/HTML surface or run API-only.
    web_enabled: bool = True
    # Interactive-browser composition (ADR-0006, 2026-08-25 amendment).  The
    # assembly chooses one exact design-system generation without importing it
    # from the kernel, declares audience facets, binds authentication providers,
    # and supplies versioned browser capabilities.  API-only assemblies leave
    # all four empty/None.
    ui_contract_version: int | None = None
    web_facets: Sequence[WebFacetMount] = ()
    authentication_profiles: Sequence[AuthenticationProfileBinding] = ()
    browser_capabilities: Sequence[BrowserCapabilityProvision] = ()
    # Product-specific configuration checks. Each returns human-readable
    # errors and follows the kernel's existing environment policy: warnings in
    # development, fatal startup errors in production.
    startup_checks: Sequence[StartupCheck] = ()
    # Product initialization that belongs inside the FastAPI lifespan (for
    # example telemetry/error tracking). Hooks run in declaration order after
    # configuration checks; sync and async callables are both accepted, and an
    # exception fails startup rather than producing a partially initialized app.
    startup_hooks: Sequence[StartupHook] = ()
    # Product-owned browser policy defaults, consumed by the one kernel-owned
    # security-header middleware writer.
    security_policy: ProductSecurityPolicy = field(
        default_factory=ProductSecurityPolicy
    )
    # Feature/module names to disable (JSON API + web together), per deployment.
    disabled_modules: frozenset[str] = frozenset()
    # The assembly's own template directory — layered OVER the kernel templates
    # (ChoiceLoader precedence) when set. None = kernel templates only.
    assembly_template_dir: Path | None = None
    # The assembly's own static directory — layered OVER the kernel static when
    # set. None = the kernel's packaged static only.
    assembly_static_dir: Path | None = None
    # Static directories belonging to INSTALLED PRESENTATION PACKAGES (a
    # `dotmac-ui` release, a `dotmac-theme-*`), layered UNDER the assembly's own
    # dir and OVER the kernel's, in declaration order. Kept separate from
    # `assembly_static_dir` because they are different authorities: the assembly
    # dir is this product's own source, these are versioned package data the
    # product composes and must not edit. The kernel never learns which package
    # supplied one — it is handed a path (ADR-0006 § 2: the kernel never imports
    # `dotmac-ui`).
    packaged_static_dirs: Sequence[Path] = ()
    # Template directories belonging to INSTALLED PACKAGES — an installable
    # MODULE's own admin screens, a packaged theme's overrides — layered UNDER
    # the assembly's own dir and OVER the kernel's, in declaration order. The
    # template counterpart of `packaged_static_dirs`, and the reason a stateful
    # module can ship a `/admin/...` surface at all: a module is a pip-installed
    # package, so its Jinja files are package data outside any assembly's
    # template root, and until this slot existed the one ChoiceLoader could hold
    # exactly one assembly directory. Same anonymity rule as the static slot —
    # the kernel is handed paths and never learns which package supplied one.
    packaged_template_dirs: Sequence[Path] = ()
    # Extra stylesheet URLs rendered into every page's <head>, after the
    # kernel's own, in declaration order. The companion to
    # `packaged_static_dirs`: that field makes a presentation package's assets
    # reachable, this one makes them LOADED. URLs, not paths — the assembly owns
    # the mapping from a package's static dir to a URL, and a consumer that
    # serves assets from a CDN-less external mount can point here instead.
    # Ignored entirely when `web_enabled` is False (no HTML surface, no <head>).
    stylesheets: Sequence[str] = ()
    # The assembly's own Alembic version directory (composed with the kernel
    # base migrations via `version_locations`). Consumed by the assembly's
    # Alembic config, not by `create_app`; carried here for a complete spec.
    assembly_migrations: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", tuple(self.modules))
        object.__setattr__(
            self,
            "module_planes",
            validate_module_plane_selections(self.modules, self.module_planes),
        )
        object.__setattr__(
            self, "setting_defaults", MappingProxyType(dict(self.setting_defaults))
        )
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))
        object.__setattr__(self, "disabled_modules", frozenset(self.disabled_modules))
        object.__setattr__(self, "startup_checks", tuple(self.startup_checks))
        object.__setattr__(self, "startup_hooks", tuple(self.startup_hooks))
        object.__setattr__(self, "web_facets", tuple(self.web_facets))
        object.__setattr__(
            self, "authentication_profiles", tuple(self.authentication_profiles)
        )
        object.__setattr__(
            self, "browser_capabilities", tuple(self.browser_capabilities)
        )
        object.__setattr__(
            self, "packaged_static_dirs", tuple(self.packaged_static_dirs)
        )
        object.__setattr__(
            self, "packaged_template_dirs", tuple(self.packaged_template_dirs)
        )
        object.__setattr__(self, "stylesheets", tuple(self.stylesheets))


__all__ = [
    "ModulePlaneSelection",
    "ProductAssemblySpec",
    "ProductSecurityPolicy",
    "StartupCheck",
    "StartupHook",
]
