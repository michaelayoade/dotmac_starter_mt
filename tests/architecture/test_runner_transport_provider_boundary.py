"""Runner provider and cloud vocabulary stays behind the adapter boundary."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CORE = ROOT / "packages/dotmac-runner-transport/src/dotmac_runner_transport"
ADAPTER = ROOT / "packages/dotmac-runner-transport-github-actions"
FOUNDATION = ROOT / "packages/dotmac-deployment-foundation"


def _assert_provider_neutral(root: Path) -> None:
    forbidden_literals = {
        "github",
        "azure",
        "blob.core",
        "actions.githubusercontent",
    }
    forbidden_imports = {
        "dotmac_runner_transport_github_actions",
        "httpx",
        "requests",
        "sqlalchemy",
        "fastapi",
    }
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = node.value.lower()
                assert not any(item in lowered for item in forbidden_literals), path
            if isinstance(node, ast.Import):
                names = {item.name.split(".", 1)[0] for item in node.names}
                assert not names & forbidden_imports, path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_imports, path


def test_core_has_no_provider_or_cloud_specific_literal_or_import() -> None:
    _assert_provider_neutral(CORE)


def test_provider_neutral_scan_reaches_nested_modules(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "provider.py"
    nested.parent.mkdir()
    nested.write_text('HOST = "actions.githubusercontent.com"\n', encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_provider_neutral(tmp_path)


def test_provider_vocabulary_is_present_in_the_adapter_not_the_core() -> None:
    adapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(ADAPTER.rglob("*.py"))
    ).lower()
    assert "actions.githubusercontent.com" in adapter_text
    assert "blob.core.windows.net" in adapter_text


def test_runner_transport_is_not_a_foundation_package_dependency() -> None:
    """Transport stays adjacent to Foundation, never inside its wheel inputs.

    A historical-revision diff cannot express this boundary: any later,
    authorized Foundation change would make such a test fail for every
    unrelated feature.  The durable property is that Foundation neither
    declares nor imports either runner-transport distribution.
    """

    forbidden = {
        "dotmac-runner-transport",
        "dotmac-runner-transport-github-actions",
        "dotmac_runner_transport",
        "dotmac_runner_transport_github_actions",
    }
    project = tomllib.loads((FOUNDATION / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(project["tool"]["poetry"]["dependencies"])
    assert dependencies.isdisjoint(forbidden)

    imported_roots: set[str] = set()
    for path in sorted((FOUNDATION / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(item.name.split(".", 1)[0] for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(forbidden)
