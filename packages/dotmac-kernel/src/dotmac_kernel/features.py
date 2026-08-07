"""Feature-manifest registry.

**Superseded, not removed:** `dotmac_kernel.modules.ModuleManifest` is the
versioned expansion of `FeatureManifest` (module control-plane directive step 2)
and adds `version`, `contract_version`, and `dependencies` on top of everything
here. `FeatureManifest` remains fully supported — the registry adapts one
automatically (`ModuleManifest.from_feature`) — so an assembly migrates its
feature packages one at a time, or not at all. Everything below still describes
the shared surface both manifests present.

Each package under app/features/ exports `feature: FeatureManifest` from its
feature.py. Core features fail hard at startup; non-core features are fault-
isolated (a broken optional feature logs and is skipped). Loading uses
importlib so dotmac_kernel never statically imports app.features (import-linter
enforces this).

**Capability model (phase 2b.1 Task 1, findings F1 + F5).** A manifest
declares TWO independent router groups, not one:

- `routers`: JSON API + anything that must survive API-only mode. Mounted
  for every ENABLED feature, always — `web_enabled` has no say here.
- `web_routers`: HTML/HTMX admin-portal routes (`web.py`). Mounted for an
  enabled feature ONLY when `web_enabled` is True. This is where every
  feature's login/dashboard/admin-screen router lives now, including
  `auth`'s login/logout — there is no meaningful cookie-auth flow without a
  web surface to authenticate into, so `auth`'s web router moves here too,
  not `routers`.
- `nav`: the sidebar entries this feature contributes (`NavItem`s), derived
  into the process-static `nav_items` Jinja global by
  `dotmac_kernel.templating.install_surface_globals` — the manifests are the ONE
  place a feature declares "I have a sidebar link", never a parallel
  hardcoded list in a template.

**Two independent on/off switches — do not conflate them:**

- `DISABLED_FEATURES` (`Settings.disabled_feature_set`) turns off ONE named
  feature entirely (both its `routers` and `web_routers`, JSON API and
  admin screens together — one feature, one switch). `DISABLED_FEATURES=web`
  disables the `web` package specifically — that's the admin DASHBOARD
  SHELL only (`GET /admin`); every other feature's own `/admin/*` screens
  and JSON routes are unaffected.
- `WEB_ENABLED` (`Settings.web_enabled`, default True) is the SURFACE
  switch: `WEB_ENABLED=false` mounts NO feature's `web_routers` at all
  (zero `/admin` routes across every feature) and also drops the
  `/static` mount (`app/main.py`) — a pure JSON API deployment. Every
  feature's `routers` (JSON API) keeps working unchanged. This is
  independent of which individual features are enabled/disabled via
  `DISABLED_FEATURES`.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI

from dotmac_kernel.permissions import PermissionSpec

if TYPE_CHECKING:  # avoids a runtime cycle: `modules` imports this module
    from dotmac_kernel.capabilities import CapabilitySpec
    from dotmac_kernel.modules import ModuleManifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavItem:
    """A single sidebar entry a feature manifest contributes.

    `feature` is left blank by the declaring `feature.py` (see e.g.
    `app.features.custom_fields.feature`) — the registry stamps it in when
    collecting nav items across manifests
    (`dotmac_kernel.templating.install_surface_globals`), so a feature never has
    to repeat its own name.
    """

    label: str
    path: str
    feature: str = ""


@dataclass(frozen=True)
class FeatureManifest:
    name: str
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    # HTML/HTMX admin-portal routers — mounted only when `web_enabled` is
    # True (see module docstring's capability-model section above).
    web_routers: Sequence[APIRouter] = field(default_factory=tuple)
    # Sidebar entries this feature contributes — see `NavItem` above.
    nav: Sequence[NavItem] = field(default_factory=tuple)
    core: bool = True
    enabled_by_default: bool = True
    # Optional startup hook (e.g. seeding the feature's own default data).
    # `app.main`'s lifespan calls this for every ENABLED manifest that
    # declares one, gated by `settings.seed_on_startup` — added so main.py
    # never has to hard-import a specific feature's seed function (final-
    # review Group 3). Most features have no seed data and leave this None.
    seed: Callable[[], None] | None = None
    # Capability codes this module DECLARES (WS1 capability catalogue). A
    # capability like "inventory.use" is the code-authoritative statement that a
    # module's capability physically exists; downstream authorities (entitlement
    # grants, deployment profiles) may only reference DECLARED codes — they may
    # never invent one. The manifest is the single declaration point; see
    # `dotmac_kernel.capabilities.CapabilityCatalogue`. Carried through
    # unchanged by `ModuleManifest.from_feature`.
    # Accepts a bare code or a `CapabilitySpec` (which additionally declares
    # whether a NEWLY PROVISIONED tenant gets it). A bare string means
    # `default_granted=True` — what these declarations meant before
    # enforcement existed.
    capabilities: Sequence[str | CapabilitySpec] = field(default_factory=tuple)
    # Permissions this module DECLARES and OWNS (module control-plane directive
    # step 3). A `PermissionSpec` is the code-authoritative statement that an
    # authorization decision exists; `dotmac_kernel.deps.require_permission`
    # may only REFERENCE a declared code, and `create_app` refuses to boot when
    # a mounted route references one nothing declares. See
    # `dotmac_kernel.permissions.PermissionCatalogue`.
    permissions: Sequence[PermissionSpec] = field(default_factory=tuple)
    # Audit actions this module DECLARES and OWNS (same step). An action is a
    # bare code — the trail records, it decides nothing — and
    # `dotmac_kernel.audit.write_audit_event` rejects one no module declares.
    # See `dotmac_kernel.audit_actions.AuditActionRegistry`.
    audit_actions: Sequence[str] = field(default_factory=tuple)


def load_manifests(module_names: Sequence[str]) -> list[FeatureManifest]:
    manifests: list[FeatureManifest] = []
    for module_name in module_names:
        module = importlib.import_module(f"{module_name}.feature")
        manifest = module.feature
        if not isinstance(manifest, FeatureManifest):
            raise TypeError(f"{module_name}.feature.feature must be a FeatureManifest")
        manifests.append(manifest)
    return manifests


def mount_features(
    app: FastAPI,
    *,
    manifests: Sequence[FeatureManifest | ModuleManifest],
    disabled: set[str],
    web_enabled: bool,
) -> None:
    """Mount every enabled feature's `routers` (always) and `web_routers`
    (only when `web_enabled` — the F1 surface switch; see module docstring).

    Accepts a `ModuleManifest` too: its `name`/`routers` compatibility aliases
    (see `dotmac_kernel.modules`) mean this consumer needed no other change when
    the module registry landed. `create_app` now passes the registry's
    dependency-ordered, enabled-only sequence; the per-manifest checks below
    stay as the belt-and-braces guarantee for a direct caller.
    """
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            logger.info("Feature %s disabled — skipping", manifest.name)
            continue
        try:
            for router in manifest.routers:
                app.include_router(router)
            if web_enabled:
                for router in manifest.web_routers:
                    app.include_router(router)
        except Exception:
            if manifest.core:
                raise
            logger.exception("Optional feature %s failed to mount", manifest.name)


__all__ = [
    "FeatureManifest",
    "NavItem",
    "load_manifests",
    "mount_features",
]
