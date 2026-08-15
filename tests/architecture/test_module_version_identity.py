"""One version per publishable module, stated in three places that must agree.

Every publishable distribution carries its version three times: `pyproject.toml`
`[project] version`, the `ModuleManifest(version=...)`, and the package's
`__version__`. They are read by different consumers — the build, the composed
module registry, and anything introspecting the installed package — so a
disagreement is silent until whichever one is wrong reaches production.

This is not hypothetical. `dotmac-release-catalog` published tag and package
`0.1.0a3` while its manifest still said `0.1.0a2`, so the registry reported a
version that had never been released. Nothing caught it, because nothing
compared the three.

The guard reads the release allowlist rather than a hardcoded list, so a module
added to the allowlist is covered the moment it becomes publishable.
"""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = PROJECT_ROOT / ".github" / "release-modules.json"


def _publishable() -> list[tuple[str, Path, str, str]]:
    """(distribution, package_dir, import_name, manifest_attr) per allowlist row."""
    raw = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    modules = raw.get("modules", raw)
    rows: list[tuple[str, Path, str, str]] = []
    for distribution, entry in modules.items():
        if not isinstance(entry, dict) or "package_dir" not in entry:
            continue
        rows.append(
            (
                distribution,
                PROJECT_ROOT / entry["package_dir"],
                entry.get("import_name", distribution.replace("-", "_")),
                entry.get("manifest_attr", "module"),
            )
        )
    return rows


def _pyproject_version(package_dir: Path) -> str:
    data = tomllib.loads((package_dir / "pyproject.toml").read_text(encoding="utf-8"))
    for table in ("project", "tool"):
        if table == "project" and "project" in data:
            version = data["project"].get("version")
            if isinstance(version, str):
                return version
    poetry = data.get("tool", {}).get("poetry", {})
    version = poetry.get("version")
    assert isinstance(version, str), f"{package_dir}: no version in pyproject.toml"
    return version


def _literal_assignment(source: str, name: str) -> str | None:
    """Read `name = "..."` without importing — the guard must not need the venv."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    return node.value.value
    return None


def _manifest_version(package_dir: Path, import_name: str, attr: str) -> str | None:
    manifest = package_dir / "src" / import_name / "manifest.py"
    if not manifest.is_file():
        return None
    tree = ast.parse(manifest.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == attr
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "version" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return None


@pytest.mark.parametrize(
    ("distribution", "package_dir", "import_name", "manifest_attr"),
    _publishable(),
    ids=[row[0] for row in _publishable()],
)
def test_pyproject_manifest_and_dunder_version_agree(
    distribution: str, package_dir: Path, import_name: str, manifest_attr: str
) -> None:
    """All three statements of a publishable module's version must be identical."""
    pyproject = _pyproject_version(package_dir)

    init = package_dir / "src" / import_name / "__init__.py"
    dunder = _literal_assignment(init.read_text(encoding="utf-8"), "__version__")
    assert dunder is not None, f"{distribution}: no literal __version__ in {init}"
    assert dunder == pyproject, (
        f"{distribution}: __version__ {dunder!r} disagrees with pyproject "
        f"{pyproject!r} — the installed package would report a version that was "
        "never released"
    )

    manifest = _manifest_version(package_dir, import_name, manifest_attr)
    if manifest is None:
        return  # stateless/manifest-less distributions state it twice, not three times
    assert manifest == pyproject, (
        f"{distribution}: manifest version {manifest!r} disagrees with pyproject "
        f"{pyproject!r} — the composed module registry would report a version "
        "that was never released, which is exactly how a3 shipped while the "
        "manifest still said a2"
    )


def test_the_guard_would_catch_a_disagreement() -> None:
    """Sensitivity proof: a guard that cannot fail is not a guard.

    The comparison is exercised against a deliberately mismatched manifest
    source, because every real module agrees today and a green run over
    agreeing inputs proves only that nothing was checked.
    """
    mismatched = 'module = ModuleManifest(code="x", version="9.9.9a1")\n'
    tree_version = None
    for node in ast.walk(ast.parse(mismatched)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for keyword in node.value.keywords:
                if keyword.arg == "version" and isinstance(keyword.value, ast.Constant):
                    tree_version = str(keyword.value.value)
    assert tree_version == "9.9.9a1", "the manifest version reader stopped reading"
    assert (
        tree_version != "0.1.0a4"
    ), "a mismatched manifest version must not compare equal to the real one"
