"""Every typed package must be scanned by the canonical quality targets.

The sites Gate 2 run exposed that adding a package to Poetry, import-linter and
Ruff did not add it to the explicitly enumerated mypy or Bandit commands.
Three older typed packages were absent too. A green ``make check`` therefore
did not mean every published typed surface had been type/security checked.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
PACKAGES = REPO_ROOT / "packages"


def _typed_source_roots(packages: Path) -> set[str]:
    return {
        marker.parent.relative_to(REPO_ROOT).as_posix()
        for marker in packages.glob("*/src/*/py.typed")
    }


def _target_recipe(makefile: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n?)+)",
        makefile,
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"Makefile target {target!r} has no recipe")
    return match.group(1)


def _uncovered_sources(makefile: str, source_roots: set[str]) -> dict[str, set[str]]:
    variables = dict(
        re.findall(
            r"^([A-Z][A-Z0-9_]*_SRC) \?= (packages/[^\s]+)$",
            makefile,
            re.MULTILINE,
        )
    )
    source_families = dict(
        re.findall(
            r"^([A-Z][A-Z0-9_]*_SOURCES)\s*:?=.*\$\(wildcard\s+(packages/[^)]+)\)",
            makefile,
            re.MULTILINE,
        )
    )
    result: dict[str, set[str]] = {}
    for target in ("type-check", "security"):
        recipe = _target_recipe(makefile, target)
        covered = {
            path for variable, path in variables.items() if f"$({variable})" in recipe
        }
        for variable, pattern in source_families.items():
            if f"$({variable})" in recipe:
                covered.update(
                    path
                    for path in source_roots
                    if fnmatch.fnmatchcase(path, pattern)
                )
        result[target] = source_roots - covered
    return result


def test_every_pep561_package_is_in_mypy_and_bandit() -> None:
    typed = _typed_source_roots(PACKAGES)
    assert len(typed) >= 21, "typed-package discovery returned an implausible set"
    assert _uncovered_sources(MAKEFILE.read_text(encoding="utf-8"), typed) == {
        "type-check": set(),
        "security": set(),
    }


def test_coverage_detector_rejects_a_planted_missing_package() -> None:
    """Sensitivity proof: one omitted typed root is reported in both lanes."""
    planted = """\
ALPHA_SRC ?= packages/alpha/src/alpha
BETA_SRC ?= packages/beta/src/beta
type-check:
\tmypy $(ALPHA_SRC)
security:
\tbandit $(ALPHA_SRC)
"""
    beta = "packages/beta/src/beta"
    assert _uncovered_sources(
        planted,
        {"packages/alpha/src/alpha", beta},
    ) == {"type-check": {beta}, "security": {beta}}


def test_a_source_family_counts_only_in_targets_that_consume_it() -> None:
    """A wildcard declaration alone cannot make an unscanned family green."""
    planted = """\
ALPHA_SRC ?= packages/alpha/src/alpha
PLUGIN_SOURCES := $(sort $(wildcard packages/plugin-*/src/*))
type-check:
\tmypy $(ALPHA_SRC) $(PLUGIN_SOURCES)
security:
\tbandit $(ALPHA_SRC)
"""
    plugin = "packages/plugin-one/src/plugin_one"
    assert _uncovered_sources(
        planted,
        {"packages/alpha/src/alpha", plugin},
    ) == {"type-check": set(), "security": {plugin}}
