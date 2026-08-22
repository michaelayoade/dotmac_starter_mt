"""Mono is a stateless bank-data protocol adapter, never a banking owner."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_mono
from dotmac_connector_mono import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-mono"
SOURCE = PACKAGE / "src" / "dotmac_connector_mono"


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
    assert version == dotmac_connector_mono.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert internal - {"dotmac_connector_mono"} == {"dotmac_integration"}


def test_connector_owns_http_but_no_persistence_scheduler_or_retry_engine() -> None:
    assert "httpx" in _imports()
    forbidden = {
        "alembic",
        "apscheduler",
        "asyncpg",
        "backoff",
        "celery",
        "psycopg",
        "sqlalchemy",
        "tenacity",
    }
    assert not (_imports() & forbidden)


def test_connector_has_no_product_decision_or_sibling_provider_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "paystack",
        "flutterwave",
        "remita",
        "tenant_id",
        "organization_id",
        "bank_statement",
        "reconciliation_status",
        "ledger",
        "allocation",
    ):
        assert forbidden not in source


def test_connector_package_is_not_composed_into_the_starter_runtime() -> None:
    assembly = (PROJECT_ROOT / "app" / "assembly.py").read_text(encoding="utf-8")
    root = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = root["tool"]["poetry"]["dependencies"]
    dev_dependencies = root["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "dotmac_connector_mono" not in assembly
    assert "dotmac-connector-mono" not in runtime_dependencies
    assert "dotmac-connector-mono" in dev_dependencies


def test_product_first_evidence_is_revision_pinned() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
