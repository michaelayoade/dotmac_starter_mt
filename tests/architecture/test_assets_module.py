"""Architecture contract for the reusable durable-asset owner."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from dotmac_assets import models, module
from sqlalchemy import CheckConstraint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages/dotmac-assets"
MODULE_ROOT = PACKAGE_ROOT / "src/dotmac_assets"
MIGRATION = MODULE_ROOT / "migrations/versions/as_0001_assets.py"


def test_the_extraction_dossier_names_the_erp_source_and_unproven_reuse() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    assert dossier["package"] == "dotmac-assets"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_erp"]
    assert set(dossier["source_repositories"]) >= {
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_crm",
        "dotmac_vendor_control_plane",
    }
    assert any(
        "dotmac_erp:app/models/fixed_assets/asset.py" == path
        for path in dossier["source_paths"]
    )
    assert any(
        "dotmac_erp:app/models/fleet/vehicle.py" == path
        for path in dossier["source_paths"]
    )


def test_manifest_declares_one_tenant_plane_and_its_immutable_lineage() -> None:
    assert module.code == "assets"
    assert module.short_code == "assets"
    assert module.migration_prefix == "as"
    assert module.migration_branch == "assets"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert module.db_schema == "mod_assets"


def test_every_table_is_tenant_scoped_and_every_relation_keeps_tenant_in_the_fk() -> (
    None
):
    assert models.TENANT_TABLES == (
        "assets",
        "asset_assignments",
        "asset_maintenance",
        "asset_disposals",
        "asset_lifecycle_events",
    )
    for model in models.TENANT_MODELS:
        columns = model.__table__.c
        assert "tenant_id" in columns
        assert columns.tenant_id.nullable is False
        constraints = {
            tuple(constraint.columns.keys())
            for constraint in model.__table__.constraints
        }
        assert ("tenant_id", "id") in constraints

        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.target_fullname for element in constraint.elements}
            if any(target.startswith("mod_assets.") for target in targets):
                assert "tenant_id" in constraint.column_keys


def test_closed_lifecycle_values_are_database_invariants() -> None:
    expected = {
        models.Asset: {"ck_assets_state", "ck_assets_condition"},
        models.AssetAssignment: {
            "ck_asset_assignments_status",
            "ck_asset_assignments_issue_condition",
            "ck_asset_assignments_return_condition",
        },
        models.AssetMaintenance: {
            "ck_asset_maintenance_kind",
            "ck_asset_maintenance_status",
            "ck_asset_maintenance_prior_state",
        },
        models.AssetDisposal: {
            "ck_asset_disposals_method",
            "ck_asset_disposals_status",
        },
    }
    for model, required_names in expected.items():
        names = {
            constraint.name
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert required_names <= names


def test_product_and_finance_meaning_do_not_leak_into_the_shared_schema() -> None:
    forbidden = {
        "organization_id",
        "employee_id",
        "department_id",
        "project_id",
        "vehicle_id",
        "subscriber_id",
        "customer_id",
        "warehouse_id",
        "item_id",
        "fiscal_period_id",
        "journal_entry_id",
        "acquisition_cost",
        "depreciation_method",
        "net_book_value",
        "latitude",
        "longitude",
        "gps_device_id",
        "fuel_type",
        "odometer",
        "incident_id",
        "reservation_id",
    }
    for model in models.TENANT_MODELS:
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, f"{model.__name__} owns product columns {sorted(leaked)}"


def test_the_package_imports_no_product_or_sibling_module() -> None:
    forbidden_roots = {
        "app",
        "dotmac_sub",
        "dotmac_erp",
        "dotmac_crm",
        "dotmac_positioning",
        "dotmac_projects",
        "dotmac_files",
        "dotmac_inventory",
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


def test_services_flush_but_never_own_the_transaction() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden in (
        ".commit(",
        ".rollback(",
        "SessionLocal(",
        "sessionmaker(",
    ):
        assert forbidden not in source
    assert ".flush(" in source


def test_the_migration_forces_rls_and_makes_lifecycle_evidence_append_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in models.TENANT_TABLES:
        qualified = f"mod_assets.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;" in source
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;" in source
        assert f"ON {qualified}" in source

    for table in models.TENANT_TABLES[:-1]:
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.{table} TO app_user;"
            in source
        )
    assert (
        "GRANT SELECT, INSERT ON mod_assets.asset_lifecycle_events TO app_user;"
        in source
    )
    assert "assets_refuse_lifecycle_rewrite" in source


def test_the_root_revision_declares_and_verifies_its_prerequisites() -> None:
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


def test_distribution_runtime_and_dossier_versions_agree() -> None:
    manifest = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    declared = manifest["tool"]["poetry"]["version"]

    import dotmac_assets

    assert dotmac_assets.__version__ == declared
    assert dossier["package"] == "dotmac-assets"
