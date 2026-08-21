"""Flutterwave ingress is a stateless provider edge, never a money owner."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_flutterwave
from dotmac_connector_flutterwave import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-flutterwave"
SOURCE = PACKAGE / "src" / "dotmac_connector_flutterwave"


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
    assert version == dotmac_connector_flutterwave.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert "dotmac_connector_flutterwave" in internal
    assert internal - {"dotmac_connector_flutterwave"} == {"dotmac_integration"}


def test_network_is_v4_provider_io_and_no_persistence_or_private_retry_exists() -> None:
    forbidden = {
        "alembic",
        "apscheduler",
        "asyncpg",
        "backoff",
        "celery",
        "psycopg",
        "requests",
        "sqlalchemy",
        "tenacity",
        "urllib3",
    }
    assert "httpx" in _imports()
    assert not (_imports() & forbidden)


def test_connector_has_no_sibling_provider_or_product_decision_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "paystack",
        "invoice_id",
        "billing_account_id",
        "subscription_id",
        "tenant_id",
        "net_amount",
        "paymentstatus",
        "balance_due",
    ):
        assert forbidden not in source
    assert "float(" not in source


def test_connector_package_is_not_composed_into_the_starter_runtime() -> None:
    assembly = (PROJECT_ROOT / "app" / "assembly.py").read_text(encoding="utf-8")
    root = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = root["tool"]["poetry"]["dependencies"]
    dev_dependencies = root["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert "dotmac_connector_flutterwave" not in assembly
    assert "dotmac-connector-flutterwave" not in runtime_dependencies
    assert "dotmac-connector-flutterwave" in dev_dependencies


def test_product_first_evidence_is_immutable() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
