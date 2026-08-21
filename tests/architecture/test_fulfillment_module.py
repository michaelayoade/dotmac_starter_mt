"""Structural canaries for the reusable fulfillment owner (ADR-0030 §5d)."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from dotmac_fulfillment import models, service
from dotmac_fulfillment.manifest import module
from dotmac_kernel.namespaces import (
    FULFILLMENT_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-fulfillment"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/fu_0001_fulfillment.py"


def test_manifest_matches_the_immutable_namespace_and_timer_dependency() -> None:
    assert FULFILLMENT_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == FULFILLMENT_MIGRATION_OWNER.owner == "fulfillment"
    assert module.migration_prefix == FULFILLMENT_MIGRATION_OWNER.prefix == "fu"
    assert module.migration_branch == FULFILLMENT_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == "mod_fulfillment"
    assert module.dependencies == ("durable_timers",)


def test_module_is_tenant_only_and_declares_every_owned_table() -> None:
    assert tuple(module.tables) == models.TENANT_TABLES
    assert module.tables
    assert module.platform_tables == ()


def test_every_table_is_tenant_scoped_and_internal_foreign_keys_carry_tenant() -> None:
    for model in models.TENANT_MODELS:
        assert model.__table__.c.tenant_id.nullable is False
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.target_fullname for element in constraint.elements}
            if any(target.startswith(f"{models.SCHEMA}.") for target in targets):
                assert "tenant_id" in constraint.column_keys


def test_attempt_and_receipt_evidence_has_no_transport_retry_state() -> None:
    forbidden = {
        "attempt_count",
        "attempts",
        "next_attempt_at",
        "available_at",
        "leased_until",
        "lease_owner",
        "dead_lettered_at",
        "connector_id",
        "connector_health",
        "provider_endpoint",
        "provider_payload",
    }
    for model in models.TENANT_MODELS:
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, f"{model.__name__} owns transport state {sorted(leaked)}"


def _concrete_participant_branches(source: str) -> list[str]:
    concrete = {"domain", "hosting", "radius", "epp", "blesta", "mikrotik"}
    findings: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare | ast.MatchValue | ast.MatchSingleton):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value.lower() in concrete:
                    findings.append(child.value)
    return findings


def test_engine_control_flow_names_no_concrete_participant_and_guard_is_sensitive() -> (
    None
):
    for path in MODULE_ROOT.rglob("*.py"):
        assert not _concrete_participant_branches(path.read_text(encoding="utf-8"))

    planted = """
def dispatch(participant_code):
    if participant_code == 'domain':
        return 'special path'
"""
    assert _concrete_participant_branches(planted) == ["domain"]


def test_package_imports_no_product_integration_or_sibling_module() -> None:
    forbidden_roots = {
        "app",
        "dotmac_sub",
        "dotmac_erp",
        "dotmac_crm",
        "dotmac_integration",
        "dotmac_domains",
        "dotmac_hosting",
        "dotmac_orders",
        "dotmac_subscriptions",
        "dotmac_durable_timers",
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


def test_services_flush_but_never_own_a_transaction_or_wall_clock() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden in (
        ".commit(",
        ".rollback(",
        "SessionLocal(",
        "sessionmaker(",
        "datetime.now(",
        "utcnow(",
    ):
        assert forbidden not in source
    assert ".flush(" in source


def test_operator_repair_is_explicit_authorized_audited_and_append_only() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    expected = {
        "fulfillment.repair.attempt_redriven",
        "fulfillment.repair.compensation_requested",
        "fulfillment.repair.outcome_terminalized",
    }
    assert set(module.audit_actions) == expected
    assert "authorize(" in source
    assert "write_audit_event(" in source
    assert "record_reviewed_terminal_outcome" in source
    assert "list_repair_attention" in source
    assert ".reviewed_by_id =" not in source


def test_migration_enables_forces_rls_and_makes_evidence_append_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in models.TENANT_TABLES:
        qualified = f"mod_fulfillment.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;" in source
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;" in source
        assert f"ON {qualified}" in source

    for table in models.APPEND_ONLY_TABLES:
        qualified = f"mod_fulfillment.{table}"
        assert f"CREATE TRIGGER {table}_append_only" in source
        assert f"BEFORE UPDATE OR DELETE ON {qualified}" in source
        assert f"GRANT SELECT, INSERT ON {qualified} TO app_user;" in source
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO app_user;"
            not in source
        )


def test_root_revision_declares_and_verifies_its_prerequisites() -> None:
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


def test_distribution_runtime_and_greenfield_dossier_versions_agree() -> None:
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    import dotmac_fulfillment

    assert dotmac_fulfillment.__version__ == pyproject["tool"]["poetry"]["version"]
    assert dossier["package"] == "dotmac-fulfillment"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_cloud", "dotmac_sub"]
