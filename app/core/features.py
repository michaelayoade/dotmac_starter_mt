"""Feature-manifest registry.

Each package under app/features/ exports `feature: FeatureManifest` from its
feature.py. Core features fail hard at startup; non-core features are fault-
isolated (a broken optional feature logs and is skipped). Loading uses
importlib so app.core never statically imports app.features (import-linter
enforces this).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureManifest:
    name: str
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    core: bool = True
    enabled_by_default: bool = True


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
) -> None:
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            logger.info("Feature %s disabled — skipping", manifest.name)
            continue
        try:
            for router in manifest.routers:
                app.include_router(router)
        except Exception:
            if manifest.core:
                raise
            logger.exception("Optional feature %s failed to mount", manifest.name)
