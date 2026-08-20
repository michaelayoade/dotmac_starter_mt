"""Structural canaries for qualification evidence and decisions."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    QUALIFICATION_MIGRATION_OWNER,
)
from dotmac_qualification import models, service
from dotmac_qualification.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/qu_0001_qualification_evidence.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_tenant_plane_are_exact() -> None:
    assert QUALIFICATION_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("qualification", "qual", "qu", "mod_qual")
    assert tuple(module.tables) == (
        "qualification_cases",
        "qualification_evidence",
        "qualification_decisions",
    )
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_qual"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_boundary_keeps_observation_separate_from_decision_and_network_state() -> None:
    evidence = set(models.QualificationEvidence.__table__.c.keys())
    decision = set(models.QualificationDecision.__table__.c.keys())
    assert {"observed_at", "valid_until", "source_type", "facts"} <= evidence
    assert {"outcome", "decided_at", "expires_at", "rationale"} <= decision
    forbidden = {
        "coverage_status",
        "network_status",
        "latitude",
        "longitude",
        "device_id",
        "nas_id",
        "service_status",
    }
    assert not forbidden & (evidence | decision)


def test_service_is_flush_only_and_sibling_independent() -> None:
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for imported in modules:
                assert imported != "app" and not imported.startswith("app.")
                assert not (
                    imported.startswith("dotmac_")
                    and not imported.startswith(
                        ("dotmac_qualification", "dotmac_kernel")
                    )
                )


def test_root_migration_is_a_forced_rls_lineage_and_passes_the_gate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in module.tables:
        qualified = f"mod_qual.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source
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
