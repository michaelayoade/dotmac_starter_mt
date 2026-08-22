"""Structural canaries for workforce scheduling and dispatch."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, WORKFORCE_MIGRATION_OWNER
from dotmac_workforce import models, service
from dotmac_workforce.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/wf_0001_workforce.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert WORKFORCE_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("workforce", "workforce", "wf", "mod_workforce")
    assert tuple(module.tables) == (
        "workforce_teams",
        "workforce_skills",
        "team_memberships",
        "worker_skills",
        "workforce_shifts",
        "workforce_availability",
        "dispatch_decisions",
    )
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_workforce"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_people_payroll_inbox_and_work_order_lifecycle_stay_out() -> None:
    forbidden = {
        "employee_number",
        "employment_status",
        "salary",
        "payroll_id",
        "leave_balance",
        "attendance_status",
        "inbox_presence",
        "conversation_id",
        "work_order_status",
        "route_geometry",
    }
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert not columns & forbidden


def test_service_is_flush_only_and_package_independent() -> None:
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
                if name == "app" or name.startswith("app."):
                    offenders.append(name)
                if name.startswith("dotmac_") and not name.startswith(
                    ("dotmac_workforce", "dotmac_kernel")
                ):
                    offenders.append(name)
    assert not offenders


def test_root_migration_declares_the_whole_forced_rls_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "wf_0001_workforce"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("workforce",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in module.tables:
        qualified = f"mod_workforce.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source


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
