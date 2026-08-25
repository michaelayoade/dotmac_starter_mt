"""`poetry.lock` must agree with the workspace packages it locks.

## The gap this closes

`poetry check --lock` compares the lock against the ROOT `pyproject.toml`. It
does not re-read the metadata of nested **path** packages, so editing
`packages/dotmac-files/pyproject.toml` — its version, or its `dotmac-kernel`
floor — leaves the lock stale and `poetry check --lock` still passing.

That is not cosmetic. The lock is what `poetry install` resolves from, so a
stale entry means CI and every developer install a package whose recorded
dependency floor is one the source no longer accepts. It surfaced for real:
five modules raised their kernel floor to `>=0.1.0a56` for the logical
prerequisite contract while the lock still recorded floors as low as `a13`, so
the locked graph permitted a kernel that cannot import those manifests at all.

The fix is `poetry lock` with the CI-pinned Poetry (see
`.github/bootstrap/`) — the toolchain version matters, a different one produces
a different file — and this test is the thing that says so out loud next time.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"
LOCK = REPO_ROOT / "poetry.lock"
CONNECTOR_POLICY = REPO_ROOT / ".github" / "release-connectors.json"


def _locked_path_packages() -> dict[str, dict]:
    """Every lock entry installed from a directory in this repo."""
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {
        package["name"]: package
        for package in lock["package"]
        if package.get("source", {}).get("type") == "directory"
    }


def _package_pyproject(name: str) -> dict:
    return tomllib.loads(
        (PACKAGES / name / "pyproject.toml").read_text(encoding="utf-8")
    )


LOCKED = _locked_path_packages()


def _connector_packages() -> set[str]:
    policy = json.loads(CONNECTOR_POLICY.read_text(encoding="utf-8"))
    return set(policy["connectors"]) | set(policy.get("held_connectors", {}))


def test_every_locally_composed_workspace_package_is_locked() -> None:
    """A package that is not locked is a package CI never installs from source,
    so nothing here would notice it drifting at all.

    Connector distributions are the deliberate inverse: Starter builds them
    but must not install them. Their source and release decision are governed by
    `release-connectors.json` and their own package tests instead.
    """
    on_disk = {
        path.name for path in PACKAGES.iterdir() if (path / "pyproject.toml").is_file()
    }
    composed = on_disk - _connector_packages()
    assert composed <= set(LOCKED), (
        f"composed workspace packages missing from poetry.lock: "
        f"{sorted(composed - set(LOCKED))}"
        " — run `poetry lock` with the CI-pinned Poetry"
    )


def test_a_connector_is_never_installed_into_the_starter_assembly() -> None:
    """A release package is not a runtime composition decision."""
    root_dependencies = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["dependencies"]
    connectors = _connector_packages()
    assert not connectors & set(LOCKED)
    assert not connectors & set(root_dependencies)


@pytest.mark.parametrize("name", sorted(LOCKED))
def test_locked_version_matches_the_package(name: str) -> None:
    declared = _package_pyproject(name)["tool"]["poetry"]["version"]
    assert LOCKED[name]["version"] == declared, (
        f"poetry.lock records {name} {LOCKED[name]['version']}, but "
        f"packages/{name}/pyproject.toml declares {declared}. `poetry check "
        "--lock` does not read nested path-package metadata, so it passes on "
        "this — re-run `poetry lock` with the CI-pinned Poetry version"
    )


@pytest.mark.parametrize("name", sorted(LOCKED))
def test_locked_dependency_constraints_match_the_package(name: str) -> None:
    """Constraints, not just versions.

    A stale FLOOR is the more dangerous half: the version being wrong is loud
    the moment anything reads it, while a floor that is too low silently
    permits an incompatible kernel to resolve.
    """
    declared = _package_pyproject(name)["tool"]["poetry"].get("dependencies", {})
    locked = LOCKED[name].get("dependencies", {})
    for dependency, constraint in declared.items():
        if dependency == "python" or not isinstance(constraint, str):
            continue
        if dependency not in locked:
            continue
        assert locked[dependency] == constraint, (
            f"poetry.lock has {name} requiring {dependency} "
            f"{locked[dependency]!r}, but packages/{name}/pyproject.toml says "
            f"{constraint!r} — re-run `poetry lock` with the CI-pinned Poetry"
        )


def test_the_kernel_lock_entry_matches_its_own_version() -> None:
    """Called out separately because every other package floors against it: a
    kernel locked below its real version makes every floor above it
    unsatisfiable in the locked graph."""
    declared = _package_pyproject("dotmac-kernel")["tool"]["poetry"]["version"]
    assert LOCKED["dotmac-kernel"]["version"] == declared
