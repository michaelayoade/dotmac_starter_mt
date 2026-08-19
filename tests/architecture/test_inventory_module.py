"""Structural canaries for the reusable inventory owner (ADR-0041)."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from dotmac_inventory import models, service
from dotmac_inventory.manifest import module
from dotmac_kernel.namespaces import INVENTORY_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-inventory"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/iv_0001_inventory.py"


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert INVENTORY_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == INVENTORY_MIGRATION_OWNER.owner == "inventory"
    assert module.migration_prefix == INVENTORY_MIGRATION_OWNER.prefix == "iv"
    assert module.migration_branch == INVENTORY_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == INVENTORY_MIGRATION_OWNER.db_schema == "mod_inventory"


def test_inventory_is_tenant_only_and_declares_every_owned_table() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.tables == (
        "items",
        "warehouses",
        "stock_balances",
        "lots",
        "lot_balances",
        "serials",
        "stock_movements",
        "movement_serials",
        "stock_reservations",
        "valuation_snapshots",
    )
    assert module.platform_tables == ()


def test_every_table_is_tenant_scoped_and_internal_fks_carry_tenant() -> None:
    for model in models.TENANT_MODELS:
        tenant = model.__table__.c["tenant_id"]
        assert tenant.nullable is False, model.__name__
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.target_fullname for element in constraint.elements}
            if any(target.startswith(f"{models.SCHEMA}.") for target in targets):
                assert (
                    "tenant_id" in constraint.column_keys
                ), f"{model.__name__}.{constraint.name} omits tenant_id"


def test_stock_schema_contains_no_product_or_finance_authority() -> None:
    forbidden = {
        "supplier_id",
        "purchase_order_id",
        "sales_order_id",
        "work_order_id",
        "ticket_id",
        "project_id",
        "customer_id",
        "subscriber_id",
        "asset_id",
        "journal_entry_id",
        "fiscal_period_id",
        "account_id",
        "provider_id",
    }
    for model in models.TENANT_MODELS:
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, f"{model.__name__} owns product columns {sorted(leaked)}"


def test_balance_and_traceability_constraints_are_tenant_preserving() -> None:
    balance = models.StockBalance.__table__
    assert {"tenant_id", "item_id", "warehouse_id"} <= set(balance.c.keys())
    lot_balance = models.LotBalance.__table__
    assert {"tenant_id", "lot_id", "warehouse_id"} <= set(lot_balance.c.keys())
    movement_serial = models.MovementSerial.__table__
    assert {"tenant_id", "movement_id", "serial_id"} <= set(movement_serial.c.keys())


def test_the_package_imports_no_product_finance_or_sibling_module() -> None:
    forbidden_roots = {
        "app",
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_crm",
        "dotmac_finance",
        "dotmac_sales",
        "dotmac_orders",
        "dotmac_assets",
        "dotmac_approvals",
        "dotmac_numbering",
    }
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert (
                not roots & forbidden_roots
            ), f"{path.name}: {roots & forbidden_roots}"


def test_the_service_is_the_flush_only_projection_writer() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden not in source
    assert ".flush(" in source
    assert ".with_for_update()" in source
    assert "with conflict_savepoint(db):" in source


def test_migration_enables_and_forces_rls_on_every_table() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in models.TENANT_TABLES:
        qualified = f"mod_inventory.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;" in source
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;" in source
        assert f"ON {qualified}" in source
        assert f" ON {qualified} TO app_user;" in source


def test_inventory_evidence_tables_are_append_only_in_the_database() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE FUNCTION mod_inventory.refuse_evidence_mutation()" in source
    for table in (
        "stock_movements",
        "movement_serials",
        "valuation_snapshots",
    ):
        assert f"CREATE TRIGGER inventory_{table}_append_only " in source
        assert f"BEFORE UPDATE OR DELETE ON mod_inventory.{table} " in source
        assert f"GRANT SELECT, INSERT ON mod_inventory.{table} TO app_user;" in source


def test_root_revision_declares_and_verifies_prerequisites() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    requires = next(
        tuple(ast.literal_eval(node.value))
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "REQUIRES"
    )
    assert requires == module.requires
    source = MIGRATION.read_text(encoding="utf-8")
    assert "depends_on = resolve_depends_on(REQUIRES)" in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source


def test_distribution_manifest_and_dossier_versions_agree() -> None:
    manifest = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    declared = manifest["tool"]["poetry"]["version"]

    import dotmac_inventory

    assert dotmac_inventory.__version__ == declared == module.version
    assert dossier["package"] == "dotmac-inventory"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_erp", "dotmac_sub"]
