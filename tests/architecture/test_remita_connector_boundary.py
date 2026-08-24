"""Remita is a transport; ERP remains the payment and accounting owner."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import dotmac_connector_remita
from dotmac_connector_remita import MANIFEST

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "packages" / "dotmac-connector-remita"
SOURCE = PACKAGE / "src" / "dotmac_connector_remita"


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


def test_version_is_one_fact_on_every_public_surface() -> None:
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["poetry"]["version"] == MANIFEST.version
    assert dotmac_connector_remita.__version__ == MANIFEST.version


def test_connector_imports_only_the_spi_among_dotmac_packages() -> None:
    internal = {name for name in _imports() if name.startswith("dotmac_")}
    assert internal - {"dotmac_connector_remita"} == {"dotmac_integration"}


def test_connector_owns_no_product_or_execution_machinery() -> None:
    forbidden_imports = {
        "alembic",
        "apscheduler",
        "asyncpg",
        "backoff",
        "celery",
        "psycopg",
        "sqlalchemy",
        "tenacity",
    }
    assert not (_imports() & forbidden_imports)
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "tenant_id",
        "organization_id",
        "source_type",
        "source_id",
        "rrrstatus",
        "ledger",
        "journal",
        "receivable",
        "payroll",
        "procurement",
    ):
        assert forbidden not in source


def test_the_declared_capability_set_is_a_reviewed_diff() -> None:
    """Status stays a POLL concern. Remita has no push channel at all, so an
    ingress capability here would be a channel the provider does not have, and
    pinning the SET makes inventing one a line in a diff."""
    assert MANIFEST.capability_ids == {
        "payments.reference.status.observation.v1",
        "payments.reference.issuance.v1",
    }


def test_the_two_provider_hash_orders_live_in_separate_functions() -> None:
    """Remita hashes `rrr + apiKey + merchantId` for a status read and
    `merchantId + serviceTypeId + orderId + amount + apiKey` for issuance. One
    shared "remita hash" helper is precisely how those two get merged into a
    single wrong one, and the provider's only feedback for that is a rejected
    request. This requires them to stay apart."""
    definitions = {
        node.name
        for path in sorted(SOURCE.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
    }
    assert "issuance_hash" in definitions
    assert not {name for name in definitions if name in {"remita_hash", "api_hash"}}


def test_product_first_evidence_is_revision_pinned() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
    assert dossier["ported_behaviour"]
    assert dossier["port_deltas"]
    assert dossier["provider_idempotency"]
