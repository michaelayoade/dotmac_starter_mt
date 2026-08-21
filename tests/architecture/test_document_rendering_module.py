"""Architecture boundary for the stateless document-rendering module."""

from __future__ import annotations

import ast
import tomllib
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-document-rendering"
SOURCE_ROOT = PACKAGE_ROOT / "src" / "dotmac_document_rendering"


def _python_sources() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _forbidden_import_findings(source: str, *, label: str) -> list[str]:
    forbidden_roots = {
        "app",
        "sqlalchemy",
        "dotmac_files",
        "dotmac_billing",
        "dotmac_numbering",
    }
    forbidden_kernel_modules = {"db", "idempotency", "models", "messaging"}
    findings: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in forbidden_roots:
                    findings.append(f"{label}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in forbidden_roots:
                findings.append(f"{label}:{node.lineno}: from {node.module}")
            if node.module == "dotmac_kernel" and any(
                alias.name in forbidden_kernel_modules for alias in node.names
            ):
                findings.append(f"{label}:{node.lineno}: from dotmac_kernel")
            if (
                node.module.startswith("dotmac_kernel.")
                and node.module.split(".")[1] in forbidden_kernel_modules
            ):
                findings.append(f"{label}:{node.lineno}: from {node.module}")
    return findings


def _purity_findings(source: str, *, label: str) -> list[str]:
    forbidden_imports = {
        "httpx",
        "requests",
        "aiohttp",
        "socket",
        "os",
        "random",
        "secrets",
    }
    forbidden_calls = {"now", "today", "utcnow", "getenv", "environ", "uuid4"}
    findings: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in forbidden_imports:
                    findings.append(f"{label}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".", 1)[0] in forbidden_imports:
                findings.append(f"{label}:{node.lineno}: from {node.module}")
        elif isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            if name in forbidden_calls:
                findings.append(f"{label}:{node.lineno}: {name}()")
    return findings


def _money_findings(source: str, *, label: str) -> list[str]:
    money_names = ("amount", "total", "tax", "balance", "due")
    findings: list[str] = []
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"float", "sum"}:
                findings.append(f"{label}:{node.lineno}: {node.func.id}()")
        if isinstance(node, ast.BinOp):
            names = {n.id.lower() for n in ast.walk(node) if isinstance(n, ast.Name)}
            if any(any(token in name for token in money_names) for name in names):
                findings.append(f"{label}:{node.lineno}: money arithmetic")
    return findings


def _public_operation_offenders(names: list[str]) -> list[str]:
    forbidden_prefixes = (
        "update_",
        "amend_",
        "patch_",
        "overwrite_",
        "edit_",
        "send_",
        "deliver_",
        "notify_",
        "email_",
        "allocate_",
        "next_number",
    )
    return sorted(name for name in names if name.lower().startswith(forbidden_prefixes))


def test_distribution_is_a_stateless_optional_module_with_no_namespace() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    from dotmac_document_rendering import module

    assert dossier["package"] == "dotmac-document-rendering"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert pyproject["tool"]["poetry"]["name"] == dossier["package"]
    assert module.code == "document_rendering"
    assert module.short_code is None
    assert module.migration_prefix is None
    assert module.db_schema is None
    assert module.tables == ()
    assert module.platform_tables == ()
    assert not (SOURCE_ROOT / "models.py").exists()
    assert not (SOURCE_ROOT / "migrations").exists()


def test_document_rendering_imports_no_persistence_storage_billing_or_assembly() -> (
    None
):
    findings: list[str] = []
    for path in _python_sources():
        findings.extend(
            _forbidden_import_findings(
                path.read_text(encoding="utf-8"), label=path.name
            )
        )

    assert not findings, (
        "document rendering must remain transaction-free:\n" + "\n".join(findings)
    )
    assert _forbidden_import_findings(
        "import dotmac_files\n", label="planted_storage_import.py"
    ), "sensitivity: the forbidden-import detector no longer bites"
    assert _forbidden_import_findings(
        "from dotmac_kernel.idempotency import fingerprint_of\n",
        label="planted_ledger_import.py",
    ), "sensitivity: a database-backed kernel import is no longer detected"


def test_public_surface_contains_no_mutation_numbering_or_delivery_operation() -> None:
    public = __import__("dotmac_document_rendering")
    assert not _public_operation_offenders(public.__all__)
    assert _public_operation_offenders(["update_rendered_document"]) == [
        "update_rendered_document"
    ]


def test_rendering_has_no_clock_network_environment_or_random_source() -> None:
    findings: list[str] = []
    for path in _python_sources():
        findings.extend(
            _purity_findings(path.read_text(encoding="utf-8"), label=path.name)
        )
    assert not findings, "renderer purity violations:\n" + "\n".join(findings)
    assert _purity_findings(
        "from datetime import datetime\nvalue = datetime.now()\n",
        label="planted_clock.py",
    ), "sensitivity: the clock detector no longer bites"


def test_money_is_never_float_or_recomputed_in_rendering_code() -> None:
    findings: list[str] = []
    for path in _python_sources():
        findings.extend(
            _money_findings(path.read_text(encoding="utf-8"), label=path.name)
        )
    assert not findings, (
        "rendering must format exact facts, never derive money:\n" + "\n".join(findings)
    )
    assert _money_findings(
        "grand_total = subtotal + tax_total\n", label="planted_total.py"
    ), "sensitivity: the money-arithmetic detector no longer bites"


def test_outputs_do_not_create_a_second_copy_of_invoice_truth() -> None:
    from dotmac_document_rendering import DocumentProjectionV1, RenderedDocumentV1

    forbidden = {
        "amount",
        "amount_paid",
        "balance",
        "balance_due",
        "coverage",
        "document_state",
        "payment_status",
        "subtotal",
        "tax_total",
        "total",
    }

    def violations(names: set[str]) -> set[str]:
        return names & forbidden

    output_fields = {item.name for item in fields(DocumentProjectionV1)} | {
        item.name for item in fields(RenderedDocumentV1)
    }
    assert not violations(output_fields)
    assert violations({"projection_digest", "total"}) == {"total"}


def test_package_is_strict_typed_and_in_the_independence_contract() -> None:
    root_pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        "dotmac-document-rendering = { path = "
        '"packages/dotmac-document-rendering", develop = true }' in root_pyproject
    )
    assert '"dotmac_document_rendering.*"' in root_pyproject
    assert '"dotmac_document_rendering"' in root_pyproject
    assert (SOURCE_ROOT / "py.typed").is_file()


def test_public_surface_and_distribution_versions_agree() -> None:
    package = __import__("dotmac_document_rendering")
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    assert package.__version__ == pyproject["tool"]["poetry"]["version"]
    assert package.module.version == package.__version__
    assert package.SUPPORTED_MODULES
    assert len(package.__all__) == len(set(package.__all__))
    assert set(package.INTERNAL_MODULES).isdisjoint(package.SUPPORTED_MODULES)


def test_checked_in_dossier_records_the_completed_erp_and_sub_audit() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    assert set(dossier["source_repositories"]) >= {"dotmac_erp", "dotmac_sub"}
    assert any("document_generator.py" in path for path in dossier["source_paths"])
    assert any("generated_document.py" in path for path in dossier["source_paths"])
    assert any(
        "test_document_generator.py" in path for path in dossier["preserved_tests"]
    )
    assert "NOT READ" not in dossier["next_action"]
    assert "moratorium holds" not in dossier["next_action"]
