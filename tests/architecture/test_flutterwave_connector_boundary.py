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


def test_the_declared_capability_set_is_a_reviewed_diff() -> None:
    """The outbound ratchet. Flutterwave v4 has a transfers/payouts surface and
    this connector deliberately does not carry it: no product consumer exists,
    and an outbound money-movement command whose first execution is also its
    first review must not arrive quietly. Pinning the SET means adding one is a
    line in a diff a reviewer has to approve."""
    assert MANIFEST.capability_ids == {
        "payments.settlement.observation.v1",
        "payments.intent.v1",
        "payments.refund.v1",
    }


def test_no_outbound_path_constant_targets_api_v3() -> None:
    """SENSITIVITY for "v4 only". The ingress leg has no URL to check, so a
    version fallback could only ever appear as a path or host constant on the
    poll or outbound legs."""
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith("/v3"), path
                assert "flutterwave.com/v3" not in node.value, path


def test_product_first_evidence_is_immutable() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
    assert dossier["status"] == "audit-complete"
    # Product-first for the OUTBOUND leg means recording what was and was not
    # ported. There is no v4 client in the fleet, so the dossier has to say what
    # it carried across instead of implying a port that did not happen.
    assert dossier["ported_behaviour"]
    assert dossier["port_deltas"]
    assert dossier["provider_idempotency"]
    assert dossier["withheld_capabilities"]
