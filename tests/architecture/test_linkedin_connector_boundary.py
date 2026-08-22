"""LinkedIn owns wire translation, never marketing or contact decisions."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_linkedin
from dotmac_connector_linkedin import MANIFEST

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-connector-linkedin"
SOURCE = PACKAGE / "src" / "dotmac_connector_linkedin"


def _imports() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_version_is_one_fact() -> None:
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["poetry"]["version"] == MANIFEST.version
    assert dotmac_connector_linkedin.__version__ == MANIFEST.version


def test_connector_imports_only_the_integration_spi_and_no_runtime_engine() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert internal - {"dotmac_connector_linkedin"} == {"dotmac_integration"}
    assert not (
        _imports() & {"httpx", "sqlalchemy", "alembic", "celery", "tenacity", "backoff"}
    )


def test_connector_has_no_product_or_sibling_provider_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "facebook",
        "instagram",
        "whatsapp",
        "tenant_id",
        "organization_id",
        "contact_id",
        "campaign_id",
        "ticket",
        "qualified",
        "assigned_to",
    ):
        assert forbidden not in source


def test_greenfield_evidence_names_the_fleet_inventory() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
