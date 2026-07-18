"""Every app/features/* package must export a valid manifest and be registered."""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.core.features import FeatureManifest, NavItem, load_manifests
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


def _extract_route_paths(routers: list) -> set[str]:
    """Extract all route paths from a sequence of APIRouter objects."""
    paths: set[str] = set()
    for router in routers:
        for route in router.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
    return paths


def _manifest_nav_coherence_failures(manifest: FeatureManifest) -> list[str]:
    """Return one message per `NavItem` in `manifest.nav` that has no matching
    route in `manifest.web_routers`.

    A manifest with nav entries but an EMPTY `web_routers` is NOT skipped
    here — every one of its nav items is guaranteed to have no matching
    route (there are no routes to match), i.e. a guaranteed dead sidebar
    link, and must be reported just like a partial mismatch would be. Only
    a manifest with no `nav` at all (nothing to check) is skipped, by the
    caller.

    Shared by the production check
    (`test_nav_items_paths_exist_in_web_routers`) and the sensitivity/
    negative-case check (`test_nav_paths_coherence_detects_bogus_entry`) so
    both exercise the exact same logic — a fix to one can't silently
    diverge from the other.
    """
    route_paths = _extract_route_paths(list(manifest.web_routers))
    failures: list[str] = []
    for nav_item in manifest.nav:
        if nav_item.path not in route_paths:
            available = sorted(route_paths)
            failures.append(
                f"{manifest.name}: NavItem({nav_item.label!r}, "
                f"{nav_item.path!r}) has no matching route in web_routers. "
                f"Available: {available}"
            )
    return failures


def test_nav_items_paths_exist_in_web_routers() -> None:
    """F5 preventive: every `NavItem.path` in a manifest must match the path
    of at least one route in that same manifest's `web_routers`. This prevents
    dead sidebar links (e.g. a nav entry pointing to an unmounted route).

    Only a manifest with NO `nav` at all is skipped — a manifest with `nav`
    entries but an EMPTY `web_routers` must still be checked (and will fail):
    that combination is a guaranteed dead sidebar link, not a no-op.
    `test_nav_paths_coherence_detects_bogus_entry` below proves this
    specific case is caught, by calling the exact same helper.
    """
    manifests = load_manifests(FEATURE_MODULES)
    failures: list[str] = []

    for manifest in manifests:
        if not manifest.nav:
            continue
        failures.extend(_manifest_nav_coherence_failures(manifest))

    assert not failures, "NavItem paths must exist in web_routers:\n" + "\n".join(
        failures
    )


def test_nav_paths_coherence_detects_bogus_entry() -> None:
    """RED: prove the nav↔web_routers coherence check catches dead links,
    INCLUDING the previously-skipped case of a manifest with `nav` set but
    `web_routers` EMPTY (a guaranteed dead sidebar link — there are no
    routes at all to match against). Calls the same
    `_manifest_nav_coherence_failures` helper the production test uses, so
    this is a genuine sensitivity test of the real check, not a
    reimplementation that could drift from it.
    """
    nav_item = NavItem("Bogus Link", "/admin/bogus")
    bogus = FeatureManifest(
        name="bogus_feature",
        core=False,
        web_routers=[],  # Empty — no routes at all, guaranteed dead link
        nav=[nav_item],
    )

    failures = _manifest_nav_coherence_failures(bogus)

    assert failures, (
        "expected the coherence check to flag a nav item with no matching "
        "route when web_routers is empty — this is the previously-skipped "
        "loophole (a nav-with-empty-web_routers manifest silently passed)"
    )
    assert "bogus_feature" in failures[0]
    assert "/admin/bogus" in failures[0]
