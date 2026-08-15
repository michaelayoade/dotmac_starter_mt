"""Every distribution states its version once, in three places that must agree.

A package in `packages/` can carry the same version number up to three times:

- `pyproject.toml`'s `[tool.poetry] version` — what the wheel is PUBLISHED as;
- `ModuleManifest(version=...)` — what the composed registry reports, what an
  assembly logs at boot, and what a compatibility check reads;
- `__version__` — what a consumer prints in a support bundle or branches on
  when working around a known defect.

Nothing kept them in sync, and the drift is not cosmetic. A stale literal makes
a deployment LIE about which release it is running, and the lie survives exactly
as long as nobody diffs two files by hand. `dotmac-files` grew a version-sync
guard after that failure; `dotmac-ticketing` grew a second one after the same
failure, having sat at `__version__ = "0.1.0a1"` across three published
releases; `dotmac-release-catalog` had a manifest a release behind its
`pyproject`, and `dotmac-integration` had the same.

Four packages, four instances, three of them found only when someone happened to
look. So this file replaces the per-package habit with ONE generic rule over
every distribution in `packages/`. A new package is covered the day it is
created, with nothing to remember — which is the property a per-package guard
structurally cannot have, because the guard that is missing is the one nobody
wrote.

## Read statically, never imported

`tomllib` and `ast`, no `import`, and deliberately not
`importlib.metadata.version`: in an editable install the installed metadata can
itself be stale, so asserting against it would let both values be wrong
together — which is the failure this file exists to catch. `dotmac-kernel` keeps
its own dedicated version test (`test_kernel_version_sync.py`) for its floor
maps and PEP 440 shape; the overlap on the plain equality is deliberate and
cheap.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2] / "packages"


def _distributions() -> list[Path]:
    """Every checked-in distribution directory, name-sorted."""
    return sorted(
        path
        for path in PACKAGES.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )


def _declared_version(distribution: Path) -> str:
    data = tomllib.loads((distribution / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["tool"]["poetry"]["version"])


def _import_root(distribution: Path) -> Path | None:
    """The single package directory under `src/`.

    Returns None rather than guessing if the layout is not the one every
    distribution here uses — a wrong guess would silently skip the checks.
    """
    src = distribution / "src"
    if not src.is_dir():
        return None
    roots = [path for path in sorted(src.iterdir()) if (path / "__init__.py").is_file()]
    return roots[0] if len(roots) == 1 else None


def _string_literal_assignment(path: Path, name: str) -> str | None:
    """The value of a module-level `name = "..."` (or annotated form)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _runtime_version(distribution: Path) -> str | None:
    root = _import_root(distribution)
    if root is None:
        return None
    return _string_literal_assignment(root / "__init__.py", "__version__")


def _manifest_version(distribution: Path) -> str | None:
    """The `version=` keyword of the manifest module's `ModuleManifest(...)`."""
    root = _import_root(distribution)
    if root is None:
        return None
    manifest = root / "manifest.py"
    if not manifest.is_file():
        return None
    tree = ast.parse(manifest.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called != "ModuleManifest":
            continue
        for keyword in node.keywords:
            if keyword.arg != "version":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(
                keyword.value.value, str
            ):
                return keyword.value.value
    return None


_DISTRIBUTIONS = _distributions()
_IDS = [path.name for path in _DISTRIBUTIONS]


@pytest.mark.parametrize("distribution", _DISTRIBUTIONS, ids=_IDS)
def test_runtime_version_matches_the_distribution_version(distribution: Path) -> None:
    """`__version__` is what a support bundle reports. A stale one makes a
    deployment misidentify itself."""
    runtime = _runtime_version(distribution)
    if runtime is None:
        pytest.skip(f"{distribution.name} declares no `__version__`")
    declared = _declared_version(distribution)
    assert runtime == declared, (
        f"{distribution.name}: __version__ is {runtime!r} but pyproject declares "
        f"{declared!r} — bump BOTH, in the same change"
    )


@pytest.mark.parametrize("distribution", _DISTRIBUTIONS, ids=_IDS)
def test_manifest_version_matches_the_distribution_version(distribution: Path) -> None:
    """`ModuleManifest.version` is what the composed registry reports. A stale
    one makes an assembly's boot log and its compatibility answers disagree with
    the wheel it actually installed."""
    manifest = _manifest_version(distribution)
    if manifest is None:
        pytest.skip(f"{distribution.name} declares no ModuleManifest version")
    declared = _declared_version(distribution)
    assert manifest == declared, (
        f"{distribution.name}: manifest declares version {manifest!r} but "
        f"pyproject declares {declared!r} — bump BOTH, in the same change"
    )


# ── Sensitivity ──────────────────────────────────────────────────────────────


def test_the_sweep_reaches_every_checked_in_distribution() -> None:
    """A parametrised sweep that discovered nothing passes silently, and reads
    exactly like one that checked everything."""
    found = {path.name for path in _DISTRIBUTIONS}
    on_disk = {path.name for path in PACKAGES.iterdir() if path.is_dir()}
    assert found == on_disk, f"distributions not swept: {sorted(on_disk - found)}"
    assert len(found) >= 10, found


def test_both_readers_answer_for_the_packages_that_declare_the_value() -> None:
    """SENSITIVITY PROOF for the two extractors.

    A reader that returned `None` on a layout it did not recognise would turn
    every assertion above into a skip, and the file would stay green while
    checking nothing. So: whenever the source TEXT contains the declaration, the
    reader must produce a value for it.
    """
    checked_runtime = 0
    checked_manifest = 0
    for distribution in _DISTRIBUTIONS:
        root = _import_root(distribution)
        assert root is not None, f"{distribution.name}: no single src package root"

        if "__version__" in (root / "__init__.py").read_text(encoding="utf-8"):
            assert (
                _runtime_version(distribution) is not None
            ), f"{distribution.name} declares __version__ but the reader missed it"
            checked_runtime += 1

        manifest = root / "manifest.py"
        if manifest.is_file() and "ModuleManifest(" in manifest.read_text(
            encoding="utf-8"
        ):
            assert (
                _manifest_version(distribution) is not None
            ), f"{distribution.name} builds a ModuleManifest but the reader missed it"
            checked_manifest += 1

    assert checked_runtime >= 10, checked_runtime
    assert checked_manifest >= 8, checked_manifest


def test_a_drifted_version_is_actually_detected(tmp_path: Path) -> None:
    """The guard bites. Both readers are driven over a synthetic package whose
    three surfaces disagree, so "everything agrees" is a finding rather than the
    only outcome the code can produce."""
    distribution = tmp_path / "dotmac-probe"
    root = distribution / "src" / "dotmac_probe"
    root.mkdir(parents=True)
    (distribution / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "dotmac-probe"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    (root / "__init__.py").write_text('__version__ = "1.1.1"\n', encoding="utf-8")
    (root / "manifest.py").write_text(
        'module = ModuleManifest(code="probe", version="2.2.2")\n', encoding="utf-8"
    )

    assert _declared_version(distribution) == "9.9.9"
    assert _runtime_version(distribution) == "1.1.1"
    assert _manifest_version(distribution) == "2.2.2"
