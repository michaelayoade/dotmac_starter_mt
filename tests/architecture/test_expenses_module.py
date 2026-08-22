"""Static contract for the product-first Expenses owner."""

from __future__ import annotations

import ast
from pathlib import Path

import dotmac_expenses
from dotmac_expenses.manifest import module
from dotmac_expenses.models import TENANT_TABLES, metadata_table
from dotmac_kernel.namespaces import EXPENSES_MIGRATION_OWNER, module_schema

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-expenses"
SOURCE = PACKAGE / "src/dotmac_expenses"
MIGRATION = SOURCE / "migrations/versions/ex_0001_expenses.py"

EXPECTED_TABLES = (
    "expense_categories",
    "expense_policies",
    "expense_policy_rules",
    "expense_requests",
    "expense_request_lines",
    "expense_claims",
    "expense_claim_lines",
    "expense_receipts",
    "expense_policy_evaluations",
    "expense_lifecycle_events",
)


def test_manifest_allocates_one_tenant_only_lineage() -> None:
    assert dotmac_expenses.__version__ == module.version == "0.1.0a1"
    assert module.code == EXPENSES_MIGRATION_OWNER.owner == "expenses"
    assert module.short_code == "expenses"
    assert module.db_schema == module_schema("expenses") == "mod_expenses"
    assert module.migration_prefix == EXPENSES_MIGRATION_OWNER.prefix == "ex"
    assert module.migration_branch == "expenses"
    assert module.tables == EXPECTED_TABLES
    assert module.platform_tables == ()
    assert TENANT_TABLES == EXPECTED_TABLES
    assert module.requires == (
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "party_person_catalog.v1",
    )


def test_every_table_has_non_nullable_tenant_and_composite_identity() -> None:
    for table_name in TENANT_TABLES:
        table = metadata_table(table_name)
        assert table.schema == "mod_expenses"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns, table_name


def test_internal_foreign_keys_carry_the_tenant() -> None:
    for table_name in TENANT_TABLES:
        table = metadata_table(table_name)
        for constraint in table.foreign_key_constraints:
            remote_schema = next(iter(constraint.elements)).column.table.schema
            if remote_schema != "mod_expenses":
                continue
            assert "tenant_id" in constraint.column_keys, (
                table_name,
                constraint.name,
            )


def test_table_creating_revision_installs_rls_and_append_only_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "ex_0001_expenses"' in source
    assert "down_revision = None" in source
    assert 'branch_labels = ("expenses",)' in source
    assert "require_prerequisites" in source
    for table_name in TENANT_TABLES:
        assert f'"{table_name}"' in source
        assert (
            f"ALTER TABLE mod_expenses.{table_name} ENABLE ROW LEVEL SECURITY" in source
        )
        assert (
            f"ALTER TABLE mod_expenses.{table_name} FORCE ROW LEVEL SECURITY" in source
        )
        assert f"{table_name}_tenant_isolation" in source
    assert "expense_policy_evaluations_are_append_only" in source
    assert "expense_lifecycle_events_are_append_only" in source
    assert "published_expense_policy_is_immutable" in source
    assert "submitted_expense_lines_are_immutable" in source
    assert "expense_receipt_metadata_is_immutable" in source


def test_service_has_no_transaction_or_sibling_module_authority() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "SessionLocal(" not in source
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert not {
        name
        for name in imported_roots
        if name.startswith("dotmac_")
        and name not in {"dotmac_kernel", "dotmac_expenses"}
    }


def test_product_first_evidence_ships_with_the_package() -> None:
    dossier = (PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert 'source_repositories = ["dotmac_erp", "dotmac_crm", "dotmac_sub"' in dossier
    assert "b969a889e8aba7255e32aa466960c22347c02fd8" in dossier
    assert "60daaa2dd305696636632f48505ab784110a55d2" in dossier
    assert "510b80ca7fab4f54a57f261872f94b5e972c8eb6" in dossier
    assert "docs/inventories/expenses-sources.md" in dossier
