"""Structural canaries for the customer-account owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_customers import models, service
from dotmac_customers.manifest import module
from dotmac_kernel.namespaces import CUSTOMERS_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/cu_0001_customer_accounts.py"


def test_manifest_owns_only_customer_account_state() -> None:
    assert CUSTOMERS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (module.code, module.short_code, module.migration_prefix) == (
        "customers",
        "customers",
        "cu",
    )
    assert module.db_schema == "mod_customers"
    assert tuple(module.tables) == (
        "customer_accounts",
        "customer_profiles",
        "customer_party_references",
    )
    assert tuple(module.platform_tables) == ()
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    }


def test_every_table_is_directly_tenant_scoped() -> None:
    for table_name in module.tables:
        table = models.metadata_table(table_name)
        assert table.schema == "mod_customers"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, table_name


def test_customer_tables_do_not_reopen_identity_reachability_or_commercial_terms() -> (
    None
):
    forbidden = {
        "email",
        "phone",
        "password_hash",
        "address",
        "latitude",
        "longitude",
        "plan_id",
        "price",
        "currency",
        "billing_cycle",
        "subscription_status",
        "service_status",
    }
    columns = {
        column.name
        for table_name in module.tables
        for column in models.metadata_table(table_name).columns
    }
    assert not (columns & forbidden)


def test_internal_relations_are_composite_tenant_foreign_keys() -> None:
    for table_name in ("customer_profiles", "customer_party_references"):
        table = models.metadata_table(table_name)
        internal = [
            constraint
            for constraint in table.foreign_key_constraints
            if any(
                element.target_fullname.startswith("mod_customers.")
                for element in constraint.elements
            )
        ]
        assert len(internal) == 1
        assert tuple(column.name for column in internal[0].columns) == (
            "tenant_id",
            "account_id",
        )


def test_services_are_flush_only_and_package_is_product_independent() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name == "app" or name.startswith(("app.", "fastapi")):
                    offenders.append(f"{path.name}: {name}")
                if name.startswith("dotmac_") and not name.startswith(
                    ("dotmac_customers", "dotmac_kernel")
                ):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders


def test_root_migration_creates_one_forced_rls_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "cu_0001_customer_accounts"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("customers",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in module.tables:
        qualified = f"mod_customers.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source
    assert "TO platform_api" not in source
