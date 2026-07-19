"""Feature-manifest registry.

Each package under app/features/ exports `feature: FeatureManifest` from its
feature.py. Core features fail hard at startup; non-core features are fault-
isolated (a broken optional feature logs and is skipped). Loading uses
importlib so app.core never statically imports app.features (import-linter
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
  `app.core.templating.install_surface_globals` — the manifests are the ONE
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

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavItem:
    """A single sidebar entry a feature manifest contributes.

    `feature` is left blank by the declaring `feature.py` (see e.g.
    `app.features.custom_fields.feature`) — the registry stamps it in when
    collecting nav items across manifests
    (`app.core.templating.install_surface_globals`), so a feature never has
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
    manifests: Sequence[FeatureManifest],
    disabled: set[str],
    web_enabled: bool,
) -> None:
    """Mount every enabled feature's `routers` (always) and `web_routers`
    (only when `web_enabled` — the F1 surface switch; see module docstring).
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
