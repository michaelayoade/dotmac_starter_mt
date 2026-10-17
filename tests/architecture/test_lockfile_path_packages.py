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
from collections.abc import Mapping
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


def _table(value: object, label: str) -> Mapping[str, object]:
    assert isinstance(value, dict), f"{label} must be a table"
    assert all(isinstance(key, str) for key in value), f"{label} keys must be strings"
    return value


def _relative_directory(base: Path, value: object, label: str) -> Path:
    assert isinstance(value, str) and value, f"{label} must be a nonempty path"
    relative = Path(value)
    assert not relative.is_absolute(), f"{label} must be relative"
    resolved = (base / relative).resolve()
    assert resolved.is_dir(), f"{label} does not identify an existing directory"
    return resolved


def _validate_nested_kernel_provider(
    repo_root: Path,
    consumer_dir: Path,
    name: str,
    requirement: Mapping[str, object],
    locked_provider: Mapping[str, object],
) -> str:
    """Prove the nested edge and root lock identify the same in-tree Kernel.

    This source qualification contract is deliberately narrow: editable
    Kernel directory dependencies only, with no guessed published floor.
    """
    assert _normalise_name(name) == "dotmac-kernel", "only in-tree Kernel is supported"
    unsupported = set(requirement) - {"path", "develop", "extras", "markers"}
    assert not unsupported, (
        f"nested Kernel uses unnormalised keys {sorted(unsupported)}"
    )
    assert requirement.get("develop") is True, "nested Kernel must be editable"
    if "extras" in requirement:
        extras = requirement["extras"]
        assert isinstance(extras, list) and all(
            isinstance(extra, str) and extra for extra in extras
        ), "nested Kernel extras must be a list of names"
    if "markers" in requirement:
        assert isinstance(requirement["markers"], str), (
            "nested Kernel marker must be text"
        )

    root = repo_root.resolve()
    consumer = consumer_dir.resolve()
    assert consumer.is_dir() and consumer.is_relative_to(root), (
        "consumer must be in-tree"
    )
    expected = (root / "packages" / "dotmac-kernel").resolve()
    assert expected.is_relative_to(root), "Kernel provider must be in-tree"
    nested_provider = _relative_directory(
        consumer, requirement.get("path"), "nested path"
    )
    assert nested_provider == expected, "nested path repoints the Kernel provider"

    root_project = _table(tomllib.loads((root / "pyproject.toml").read_text()), "root")
    root_tool = _table(root_project.get("tool"), "root tool")
    root_poetry = _table(root_tool.get("poetry"), "root Poetry")
    root_dependencies = _table(root_poetry.get("dependencies"), "root dependencies")
    root_requirement = _table(root_dependencies.get("dotmac-kernel"), "root Kernel")
    assert set(root_requirement) == {"path", "develop"}, "root Kernel options drifted"
    assert root_requirement["develop"] is True, "root Kernel must be editable"
    assert (
        _relative_directory(root, root_requirement["path"], "root Kernel path")
        == expected
    ), "root path repoints the Kernel provider"

    provider_project = _table(
        tomllib.loads((expected / "pyproject.toml").read_text()), "provider"
    )
    provider_tool = _table(provider_project.get("tool"), "provider tool")
    provider_poetry = _table(provider_tool.get("poetry"), "provider Poetry")
    provider_name = provider_poetry.get("name")
    assert isinstance(provider_name, str) and (
        _normalise_name(provider_name) == "dotmac-kernel"
    ), "provider distribution is not Kernel"
    version = provider_poetry.get("version")
    assert isinstance(version, str) and version, "provider version must be explicit"
    assert locked_provider.get("name") == "dotmac-kernel", (
        "locked provider name drifted"
    )
    assert locked_provider.get("version") == version, "locked provider version drifted"
    assert locked_provider.get("develop") is True, "locked provider must be editable"
    source = _table(locked_provider.get("source"), "locked provider source")
    assert set(source) == {"type", "url"}, "locked provider source options drifted"
    assert source["type"] == "directory", "locked provider must use directory source"
    assert (
        _relative_directory(root, source["url"], "locked provider source") == expected
    ), "locked provider source repoints Kernel"
    return version


def _normalise_nested_kernel_dependency(
    name: str,
    requirement: Mapping[str, object],
    repo_root: Path | None,
    consumer_dir: Path | None,
    locked_packages: Mapping[str, dict] | None,
) -> dict[str, object]:
    """Admit the observed Poetry path shape only after provider validation."""
    assert repo_root is not None and consumer_dir is not None, (
        "nested path dependency requires repository and consumer context"
    )
    assert locked_packages is not None, (
        "nested path dependency requires locked providers"
    )
    provider = _table(locked_packages.get("dotmac-kernel"), "locked Kernel provider")
    _validate_nested_kernel_provider(
        repo_root, consumer_dir, name, requirement, provider
    )
    # Both sides resolve independently before comparing. Marker/extras stay
    # byte-for-byte data; they must never disappear during path normalisation.
    return {
        "path": "packages/dotmac-kernel",
        "develop": True,
        **{
            key: requirement[key] for key in ("extras", "markers") if key in requirement
        },
    }


def _declared_runtime_dependencies(
    pyproject: dict,
    *,
    repo_root: Path | None = None,
    consumer_dir: Path | None = None,
    locked_packages: Mapping[str, dict] | None = None,
) -> dict[str, object]:
    """The non-optional dependency contract Poetry records in the lock.

    Table-form requirements must not disappear merely because they are not a
    string.  Optional dependencies are represented through the lock entry's
    extras and are deliberately outside this map.
    """
    declared = pyproject["tool"]["poetry"].get("dependencies", {})
    result: dict[str, object] = {}
    names: set[str] = set()
    for name, constraint in declared.items():
        normalised_name = _normalise_name(name)
        assert normalised_name not in names, f"duplicate declared dependency {name}"
        names.add(normalised_name)
        if name == "python":
            continue
        if isinstance(constraint, str):
            result[_normalise_name(name)] = constraint
            continue
        if not isinstance(constraint, dict):
            raise AssertionError(f"unsupported dependency declaration for {name}")
        if "path" in constraint or "develop" in constraint:
            result[_normalise_name(name)] = _normalise_nested_kernel_dependency(
                name, constraint, repo_root, consumer_dir, locked_packages
            )
            continue
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


def _locked_runtime_dependencies(
    package: dict,
    *,
    repo_root: Path | None = None,
    consumer_dir: Path | None = None,
    locked_packages: Mapping[str, dict] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, constraint in package.get("dependencies", {}).items():
        assert _normalise_name(name) not in result, (
            f"duplicate locked dependency {name}"
        )
        if isinstance(constraint, dict) and (
            "path" in constraint or "develop" in constraint
        ):
            constraint = _normalise_nested_kernel_dependency(
                name, constraint, repo_root, consumer_dir, locked_packages
            )
        result[_normalise_name(name)] = constraint
    return result


def _assert_runtime_dependencies_match(
    repo_root: Path,
    consumer_dir: Path,
    pyproject: dict,
    package: dict,
    locked_packages: Mapping[str, dict],
) -> None:
    declared = _declared_runtime_dependencies(
        pyproject,
        repo_root=repo_root,
        consumer_dir=consumer_dir,
        locked_packages=locked_packages,
    )
    locked = _locked_runtime_dependencies(
        package,
        repo_root=repo_root,
        consumer_dir=consumer_dir,
        locked_packages=locked_packages,
    )
    assert locked == declared, (
        f"poetry.lock has {consumer_dir.name} runtime dependencies {locked!r}, but "
        f"{consumer_dir}/pyproject.toml declares {declared!r} — re-run "
        "`poetry lock` with the exact requires-poetry version"
    )


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
    _assert_runtime_dependencies_match(
        REPO_ROOT, PACKAGES / name, _package_pyproject(name), LOCKED[name], LOCKED
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


def _nested_kernel_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    """Only provider identity fixtures; these do not invent a Poetry edge shape."""
    consumer = tmp_path / "packages" / "dotmac-learning"
    consumer.mkdir(parents=True)
    kernel = tmp_path / "packages" / "dotmac-kernel"
    kernel.mkdir()
    (kernel / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "dotmac-kernel"\nversion = "0.1.0a1"\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\n"
        'dotmac-kernel = {path = "packages/dotmac-kernel", develop = true}\n',
        encoding="utf-8",
    )
    requirement: dict[str, object] = {"path": "../dotmac-kernel", "develop": True}
    provider: dict[str, object] = {
        "name": "dotmac-kernel",
        "version": "0.1.0a1",
        "develop": True,
        "source": {"type": "directory", "url": "packages/dotmac-kernel"},
    }
    return tmp_path, consumer, requirement, provider


def test_nested_kernel_provider_identity_contract(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    assert (
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )
        == "0.1.0a1"
    )


def test_nested_kernel_provider_preserves_requirement_options(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    requirement.update(extras=["testing"], markers="python_version >= '3.12'")
    before = requirement.copy()
    _validate_nested_kernel_provider(
        root, consumer, "dotmac-kernel", requirement, provider
    )
    assert requirement == before


def test_nested_kernel_provider_rejects_other_distribution(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    with pytest.raises(AssertionError, match="only in-tree Kernel"):
        _validate_nested_kernel_provider(
            root, consumer, "other-kernel", requirement, provider
        )


def test_nested_kernel_provider_rejects_missing_develop(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    del requirement["develop"]
    with pytest.raises(AssertionError, match="must be editable"):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


def test_nested_kernel_provider_rejects_absolute_path(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    requirement["path"] = str(root / "packages/dotmac-kernel")
    with pytest.raises(AssertionError, match="must be relative"):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


def test_nested_kernel_provider_rejects_symlink_escape(tmp_path: Path) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path / "repo")
    kernel = root / "packages/dotmac-kernel"
    (kernel / "pyproject.toml").unlink()
    kernel.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    kernel.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AssertionError, match="provider must be in-tree"):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("path", "../elsewhere"),
        ("path", "../../../outside"),
        ("develop", False),
        ("develop", 1),
        ("develop", "true"),
        ("unknown", True),
        ("optional", True),
        ("version", ">=0.1.0a1"),
        ("extras", "testing"),
        ("markers", ["python_version >= '3.12'"]),
    ],
)
def test_nested_kernel_provider_rejects_requirement_drift(
    tmp_path: Path, key: str, value: object
) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    requirement[key] = value
    with pytest.raises(AssertionError):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "other-kernel"),
        ("version", "0.1.0a2"),
        ("develop", False),
        ("develop", 1),
        ("source", {"type": "directory", "url": "packages/elsewhere"}),
        ("source", {"type": "git", "url": "packages/dotmac-kernel"}),
        (
            "source",
            {"type": "directory", "url": "packages/dotmac-kernel", "unknown": True},
        ),
    ],
)
def test_nested_kernel_provider_rejects_lock_identity_drift(
    tmp_path: Path, key: str, value: object
) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    provider[key] = value
    with pytest.raises(AssertionError):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


@pytest.mark.parametrize(
    "metadata",
    [
        '[tool.poetry]\nname = "other-kernel"\nversion = "0.1.0a1"\n',
        '[tool.poetry]\nname = "dotmac-kernel"\nversion = "0.1.0a2"\n',
        '[tool.poetry]\nname = "dotmac-kernel"\n',
    ],
)
def test_nested_kernel_provider_rejects_provider_metadata_drift(
    tmp_path: Path, metadata: str
) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    (root / "packages/dotmac-kernel/pyproject.toml").write_text(
        metadata, encoding="utf-8"
    )
    with pytest.raises(AssertionError):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


@pytest.mark.parametrize(
    "declaration",
    [
        'dotmac-kernel = {path = "packages/elsewhere", develop = true}',
        'dotmac-kernel = {path = "packages/dotmac-kernel", develop = false}',
        'dotmac-kernel = {path = "packages/dotmac-kernel", develop = true, unknown = true}',
        'dotmac-kernel = ">=0.1.0a1"',
        'other-kernel = {path = "packages/dotmac-kernel", develop = true}',
    ],
)
def test_nested_kernel_provider_rejects_root_declaration_drift(
    tmp_path: Path, declaration: str
) -> None:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    (root / "pyproject.toml").write_text(
        f"[tool.poetry.dependencies]\n{declaration}\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError):
        _validate_nested_kernel_provider(
            root, consumer, "dotmac-kernel", requirement, provider
        )


def _nested_kernel_comparison_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict, dict, dict]:
    root, consumer, requirement, provider = _nested_kernel_fixture(tmp_path)
    (root / "packages/elsewhere").mkdir()
    pyproject = {
        "tool": {
            "poetry": {
                "dependencies": {
                    "python": ">=3.11,<3.14",
                    "dotmac-kernel": requirement,
                    "sqlalchemy": "^2.0",
                    "pydantic": "^2.9",
                    "alembic": ">=1.13",
                }
            }
        }
    }
    # Exact nested edge observed in hosted producer run 39029665304,
    # artifact 12099633709 (Poetry 2.4.1), rather than an invented floor.
    package = {
        "dependencies": {
            "dotmac-kernel": {"path": "../dotmac-kernel", "develop": True},
            "sqlalchemy": "^2.0",
            "pydantic": "^2.9",
            "alembic": ">=1.13",
        }
    }
    return root, consumer, pyproject, package, {"dotmac-kernel": provider}


def test_nested_kernel_generated_edge_passes_the_dependency_guard(
    tmp_path: Path,
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    _assert_runtime_dependencies_match(*args)


def test_learning_consumer_passes_the_same_dependency_guard() -> None:
    """The real package is checked through the same guard as planted drift."""
    assert "dotmac-learning" in LOCKED
    _assert_runtime_dependencies_match(
        REPO_ROOT,
        PACKAGES / "dotmac-learning",
        _package_pyproject("dotmac-learning"),
        LOCKED["dotmac-learning"],
        LOCKED,
    )


@pytest.mark.parametrize("side", ["declared", "locked"])
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("path", "../elsewhere"),
        ("path", "../../../outside"),
        ("develop", False),
        ("develop", 1),
        ("source", "another-index"),
        ("version", ">=0.1.0a1"),
        ("optional", True),
        ("unknown", True),
        ("extras", ["testing"]),
        ("markers", "python_version >= '3.12'"),
    ],
)
def test_nested_kernel_guard_rejects_edge_drift(
    tmp_path: Path, side: str, key: str, value: object
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    dependencies = (
        args[2]["tool"]["poetry"]["dependencies"]
        if side == "declared"
        else args[3]["dependencies"]
    )
    dependencies["dotmac-kernel"][key] = value
    with pytest.raises(AssertionError):
        _assert_runtime_dependencies_match(*args)


@pytest.mark.parametrize("side", ["declared", "locked"])
@pytest.mark.parametrize("key", ["path", "develop", "extras", "markers"])
def test_nested_kernel_guard_rejects_missing_options(
    tmp_path: Path, side: str, key: str
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    declared = args[2]["tool"]["poetry"]["dependencies"]["dotmac-kernel"]
    locked = args[3]["dependencies"]["dotmac-kernel"]
    for edge in (declared, locked):
        edge.update(extras=["testing"], markers="python_version >= '3.12'")
    del (declared if side == "declared" else locked)[key]
    with pytest.raises(AssertionError):
        _assert_runtime_dependencies_match(*args)


def test_nested_kernel_guard_preserves_matching_extras_and_marker(
    tmp_path: Path,
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    declared = args[2]["tool"]["poetry"]["dependencies"]["dotmac-kernel"]
    locked = args[3]["dependencies"]["dotmac-kernel"]
    for edge in (declared, locked):
        edge.update(extras=["testing"], markers="python_version >= '3.12'")
    _assert_runtime_dependencies_match(*args)


@pytest.mark.parametrize("side", ["declared", "locked"])
@pytest.mark.parametrize("operation", ["remove", "add", "rename", "absolute"])
def test_nested_kernel_guard_is_bidirectional(
    tmp_path: Path, side: str, operation: str
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    dependencies = (
        args[2]["tool"]["poetry"]["dependencies"]
        if side == "declared"
        else args[3]["dependencies"]
    )
    if operation == "remove":
        del dependencies["dotmac-kernel"]
    elif operation == "add":
        dependencies["httpx"] = "^0.28"
    elif operation == "rename":
        dependencies["other-kernel"] = dependencies.pop("dotmac-kernel")
    else:
        dependencies["dotmac-kernel"]["path"] = str(args[0] / "packages/dotmac-kernel")
    with pytest.raises(AssertionError):
        _assert_runtime_dependencies_match(*args)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "other-kernel"),
        ("version", "0.1.0a2"),
        ("develop", False),
        ("source", {"type": "directory", "url": "packages/dotmac-learning"}),
        ("source", {"type": "git", "url": "packages/dotmac-kernel"}),
    ],
)
def test_nested_kernel_guard_checks_the_locked_provider(
    tmp_path: Path, key: str, value: object
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    args[4]["dotmac-kernel"][key] = value
    with pytest.raises(AssertionError):
        _assert_runtime_dependencies_match(*args)


def test_nested_kernel_guard_refuses_missing_provider(tmp_path: Path) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    args[4].clear()
    with pytest.raises(AssertionError):
        _assert_runtime_dependencies_match(*args)


def test_nested_kernel_guard_rejects_symlink_repoint(tmp_path: Path) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    alias = args[1] / "kernel-alias"
    alias.symlink_to(args[1], target_is_directory=True)
    for dependencies in (
        args[2]["tool"]["poetry"]["dependencies"],
        args[3]["dependencies"],
    ):
        dependencies["dotmac-kernel"]["path"] = "kernel-alias"
    with pytest.raises(AssertionError, match="repoints"):
        _assert_runtime_dependencies_match(*args)


@pytest.mark.parametrize("side", ["declared", "locked"])
def test_nested_kernel_guard_rejects_duplicate_normalised_name(
    tmp_path: Path, side: str
) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path)
    dependencies = (
        args[2]["tool"]["poetry"]["dependencies"]
        if side == "declared"
        else args[3]["dependencies"]
    )
    dependencies["dotmac_kernel"] = dependencies["dotmac-kernel"].copy()
    with pytest.raises(AssertionError, match="duplicate"):
        _assert_runtime_dependencies_match(*args)


def test_nested_kernel_guard_rejects_existing_outside_target(tmp_path: Path) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path / "repo")
    (tmp_path / "outside").mkdir()
    for dependencies in (
        args[2]["tool"]["poetry"]["dependencies"],
        args[3]["dependencies"],
    ):
        dependencies["dotmac-kernel"]["path"] = "../../../outside"
    with pytest.raises(AssertionError, match="repoints"):
        _assert_runtime_dependencies_match(*args)


def test_nested_kernel_guard_rejects_symlink_escape(tmp_path: Path) -> None:
    args = _nested_kernel_comparison_fixture(tmp_path / "repo")
    kernel = args[0] / "packages/dotmac-kernel"
    (kernel / "pyproject.toml").unlink()
    kernel.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    kernel.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AssertionError, match="provider must be in-tree"):
        _assert_runtime_dependencies_match(*args)
