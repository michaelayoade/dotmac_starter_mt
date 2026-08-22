"""Meta Social is a stateless provider edge, never a product runtime."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_meta_social
from dotmac_connector_meta_social import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-meta-social"
SOURCE = PACKAGE / "src" / "dotmac_connector_meta_social"


def _imports() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_connector_version_is_one_fact_on_every_public_surface() -> None:
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    version = declared["tool"]["poetry"]["version"]
    assert version == dotmac_connector_meta_social.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert "dotmac_connector_meta_social" in internal
    assert internal - {"dotmac_connector_meta_social"} == {"dotmac_integration"}


def test_ingress_slice_has_no_network_or_persistence_dependency() -> None:
    forbidden = {
        "alembic",
        "asyncpg",
        "httpx",
        "psycopg",
        "requests",
        "sqlalchemy",
        "urllib3",
    }
    assert not (_imports() & forbidden)


def test_connector_has_no_product_decision_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "subscriber_id",
        "conversation_id",
        "ticket_id",
        "assign_team",
        "entitlement",
        "permission",
    ):
        assert forbidden not in source


def test_connector_package_is_not_composed_into_the_starter_runtime() -> None:
    assembly = (PROJECT_ROOT / "app" / "assembly.py").read_text(encoding="utf-8")
    root = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = root["tool"]["poetry"]["dependencies"]
    dev_dependencies = root["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "dotmac_connector_meta_social" not in assembly
    assert "dotmac-connector-meta-social" not in runtime_dependencies
    assert "dotmac-connector-meta-social" in dev_dependencies
