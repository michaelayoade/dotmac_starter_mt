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

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"
LOCK = REPO_ROOT / "poetry.lock"


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


def _normalise_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _declared_runtime_dependencies(pyproject: dict) -> dict[str, object]:
    """The non-optional dependency contract Poetry records in the lock.

    Table-form requirements must not disappear merely because they are not a
    string.  Optional dependencies are represented through the lock entry's
    extras and are deliberately outside this map.
    """
    declared = pyproject["tool"]["poetry"].get("dependencies", {})
    result: dict[str, object] = {}
    for name, constraint in declared.items():
        if name == "python":
            continue
        if isinstance(constraint, str):
            result[_normalise_name(name)] = constraint
            continue
        if not isinstance(constraint, dict):
            raise AssertionError(f"unsupported dependency declaration for {name}")
        if constraint.get("optional", False):
            continue
        unsupported = set(constraint) - {"version", "extras", "markers", "optional"}
        assert not unsupported, (
            f"dependency {name} uses unnormalised keys {sorted(unsupported)}; "
            "extend the lock guard before relying on this declaration"
        )
        normalised = {
            key: constraint[key]
            for key in ("version", "extras", "markers")
            if key in constraint
        }
        result[_normalise_name(name)] = (
            normalised["version"] if set(normalised) == {"version"} else normalised
        )
    return result


def _locked_runtime_dependencies(package: dict) -> dict[str, object]:
    return {
        _normalise_name(name): constraint
        for name, constraint in package.get("dependencies", {}).items()
    }


def test_every_workspace_package_is_locked() -> None:
    """A package that is not locked is a package CI never installs from source,
    so nothing here would notice it drifting at all."""
    on_disk = {
        path.name for path in PACKAGES.iterdir() if (path / "pyproject.toml").is_file()
    }
    assert on_disk <= set(LOCKED), (
        f"workspace packages missing from poetry.lock: {sorted(on_disk - set(LOCKED))}"
        " — run `poetry lock` with the CI-pinned Poetry"
    )


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
    declared = _declared_runtime_dependencies(_package_pyproject(name))
    locked = _locked_runtime_dependencies(LOCKED[name])
    assert locked == declared, (
        f"poetry.lock has {name} runtime dependencies {locked!r}, but "
        f"packages/{name}/pyproject.toml declares {declared!r} — re-run "
        "`poetry lock` with the exact requires-poetry version"
    )


@pytest.mark.parametrize(
    ("declared", "locked"),
    [
        ({"python": ">=3.12", "httpx": "^0.28"}, {}),
        ({"python": ">=3.12"}, {"httpx": "^0.28"}),
        (
            {"python": ">=3.12", "pyjwt": {"version": ">=2", "extras": ["crypto"]}},
            {"pyjwt": {"version": ">=2"}},
        ),
    ],
)
def test_bidirectional_guard_detects_planted_path_package_drift(
    declared: dict[str, object], locked: dict[str, object]
) -> None:
    pyproject = {"tool": {"poetry": {"dependencies": declared}}}
    lock_entry = {"dependencies": locked}
    assert _declared_runtime_dependencies(pyproject) != _locked_runtime_dependencies(
        lock_entry
    )


def test_the_kernel_lock_entry_matches_its_own_version() -> None:
    """Called out separately because every other package floors against it: a
    kernel locked below its real version makes every floor above it
    unsatisfiable in the locked graph."""
    declared = _package_pyproject("dotmac-kernel")["tool"]["poetry"]["version"]
    assert LOCKED["dotmac-kernel"]["version"] == declared
