"""Every app/features/* package must export a valid manifest and be registered."""

from __future__ import annotations

from pathlib import Path

from app.core.features import FeatureManifest, load_manifests
from app.features import FEATURE_MODULES

FEATURES_DIR = Path(__file__).resolve().parents[2] / "app" / "features"


def test_every_feature_package_is_registered() -> None:
    on_disk = {
        p.name for p in FEATURES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    registered = {m.rsplit(".", 1)[-1] for m in FEATURE_MODULES}
    assert on_disk == registered


def test_manifests_load_and_are_named_after_package() -> None:
    for module_name, manifest in zip(
        FEATURE_MODULES, load_manifests(FEATURE_MODULES), strict=True
    ):
        assert isinstance(manifest, FeatureManifest)
        assert module_name.endswith(manifest.name)
