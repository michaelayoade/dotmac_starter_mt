"""Every app/features/* package must export a valid manifest and be registered."""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.core.features import FeatureManifest, load_manifests
from app.features import FEATURE_MODULES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = PROJECT_ROOT / "app" / "features"

INDEPENDENCE_CONTRACT = "Features are independent of each other"


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


def _independence_contract_modules() -> list[str]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    contracts = data["tool"]["importlinter"]["contracts"]
    matches = [c for c in contracts if c.get("name") == INDEPENDENCE_CONTRACT]
    assert len(matches) == 1, f"expected exactly one {INDEPENDENCE_CONTRACT!r} contract"
    return list(matches[0]["modules"])


def test_importlinter_independence_contract_matches_feature_modules() -> None:
    """A feature registered in FEATURE_MODULES but missing from the import-linter
    independence contract would silently escape `make lint-imports`."""
    assert sorted(_independence_contract_modules()) == sorted(FEATURE_MODULES)


def test_contract_comparison_detects_drift() -> None:
    """Sanity-check the comparison is order-insensitive but content-sensitive."""
    contract_modules = _independence_contract_modules()
    reordered = list(contract_modules)
    reordered.reverse()
    assert sorted(reordered) == sorted(FEATURE_MODULES)
    mutated = [*FEATURE_MODULES, "app.features.ghost_feature"]
    assert sorted(contract_modules) != sorted(mutated)


def test_main_does_not_hard_import_a_specific_feature_package() -> None:
    """Final-review Group 3: `app/main.py` seeds features generically via each
    manifest's optional `seed` hook (`app.core.features.FeatureManifest.seed`,
    dispatched by `_run_enabled_seeds` over `load_manifests(FEATURE_MODULES)`)
    instead of hard-importing e.g. `app.features.settings.seed.
    seed_platform_defaults` — deleting or disabling a feature package must not
    break `import app.main`. Deleting a package can't be exercised in-tree
    (this repo always has all six on disk), so this is a static proxy: the
    only allowed `app.features.` reference is the `FEATURE_MODULES` registry
    import (`from app.features import FEATURE_MODULES`), which names no
    specific feature. Any OTHER `app.features.<name>` import (a dotted
    reference into a feature subpackage) fails the build.
    """
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text()
    offending = [
        line.strip()
        for line in main_source.splitlines()
        if "app.features." in line
        and "from app.features import FEATURE_MODULES" not in line
    ]
    assert not offending, (
        "app/main.py must not hard-import a specific feature package:\n"
        + "\n".join(offending)
    )
