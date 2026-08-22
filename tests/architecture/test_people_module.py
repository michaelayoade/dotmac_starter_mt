"""Structural contract for the extracted tenant employment-directory owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, PEOPLE_MIGRATION_OWNER
from dotmac_people import models, service
from dotmac_people.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/pe_0001_people_directory.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_matches_the_same_change_namespace_allocation() -> None:
    assert PEOPLE_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == "people"
    assert module.short_code == "people"
    assert module.migration_prefix == "pe"
    assert module.migration_branch == "people"
    assert module.db_schema == "mod_people"
    assert tuple(module.tables) == (
        "employees",
        "departments",
        "designations",
        "employment_types",
        "positions",
        "position_assignments",
    )
    assert tuple(module.platform_tables) == ()
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "party_person_catalog.v1",
    }


def test_every_table_has_direct_tenant_identity_and_tenant_leading_uniqueness() -> None:
    for table_name in module.tables:
        table = models.metadata_table(table_name)
        assert table.schema == "mod_people"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, table_name


def test_every_internal_relation_is_a_composite_tenant_foreign_key() -> None:
    for table_name in module.tables:
        table = models.metadata_table(table_name)
        for constraint in table.foreign_key_constraints:
            targets = tuple(element.target_fullname for element in constraint.elements)
            if not any(target.startswith("mod_people.") for target in targets):
                continue
            assert next(column.name for column in constraint.columns) == "tenant_id"
            assert targets[0].endswith(".tenant_id"), (table_name, targets)


def test_employee_reuses_kernel_person_identity_and_carries_no_wide_erp_fields() -> (
    None
):
    table = models.Employee.__table__
    targets = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.foreign_key_constraints
    }
    assert ("parties.tenant_id", "parties.id") in targets
    assert ("party_persons.party_id",) in targets

    forbidden = {
        "first_name",
        "last_name",
        "email",
        "password_hash",
        "reports_to_id",
        "expense_approver_id",
        "bank_account_number",
        "ctc",
        "cost_center_id",
        "location_id",
        "shift_type_id",
        "payroll_account_id",
        "dotmac_sub_account_id",
    }
    assert not (forbidden & set(table.c.keys()))


def test_position_tree_is_the_only_reporting_and_vacancy_authority() -> None:
    department = models.Department.__table__
    position = models.Position.__table__
    assert "head_id" not in department.c
    assert "is_vacant" not in position.c
    assert position.c.is_department_head.nullable is False


def test_services_never_own_transactions_or_construct_sessions() -> None:
    tree = ast.parse(Path(inspect.getfile(service)).read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"commit", "rollback"} & calls)
    assert not ({"Session", "SessionLocal", "sessionmaker"} & names)


def test_root_migration_declares_effects_and_creates_the_whole_secure_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "pe_0001_people_directory"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("people",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert isinstance(assigned["depends_on"], ast.Call)
    assert getattr(assigned["depends_on"].func, "id", None) == "resolve_depends_on"

    for table in module.tables:
        qualified = f"mod_people.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified}" in source
        assert f"ON {qualified} TO app_user" in source
    assert "TO platform_api" not in source


def test_package_has_no_product_web_or_sibling_module_dependency() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
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
                    ("dotmac_people", "dotmac_kernel")
                ):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders


def test_lineage_passes_the_composed_migration_gate() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.violations


def test_the_product_first_dossier_ships_with_the_package() -> None:
    dossier = (ROOT.parents[1] / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert 'source_repositories = ["dotmac_erp"' in dossier
    assert "131 foreign-key" in dossier
    assert "compatibility projection" in dossier
