"""Inter-app sync is a stateless contract, never a fifth application."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

import dotmac_app_sync
from dotmac_app_sync import deliver_authenticated

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-app-sync"
SOURCE = PACKAGE / "src" / "dotmac_app_sync"


def _imports() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_package_is_stateless_and_imports_no_product_or_transport() -> None:
    assert not (
        _imports()
        & {
            "app",
            "alembic",
            "fastapi",
            "httpx",
            "requests",
            "sqlalchemy",
            "dotmac_kernel",
            "dotmac_integration",
        }
    )


def test_receiver_boundary_accepts_no_session_or_remote_location() -> None:
    parameters = inspect.signature(deliver_authenticated).parameters
    assert "db" not in parameters
    assert "session" not in parameters
    assert "url" not in parameters
    assert "provider" not in parameters


def test_runtime_contains_no_pairwise_application_routes_or_provider_names() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    ).lower()
    for forbidden in (
        "sub_to_erp",
        "erp_to_sub",
        "erp_to_academy",
        "academy_to_erp",
        "paystack",
        "flutterwave",
        "mono",
        "remita",
        "linkedin",
    ):
        assert forbidden not in source


def test_version_and_product_first_dossier_are_complete() -> None:
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["poetry"]["version"] == dotmac_app_sync.__version__
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8"))
    assert dossier["source_mode"] == "product-first"
    assert dossier["source_paths"]
    assert all("@" in item and ":" in item for item in dossier["source_paths"])
    assert dossier["contract_consumers"] == []
