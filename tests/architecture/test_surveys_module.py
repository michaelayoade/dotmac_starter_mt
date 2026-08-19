"""Structural canaries for reusable Surveys/CSAT ownership (ADR-0035)."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, SURVEYS_MIGRATION_OWNER
from dotmac_surveys import models, service
from dotmac_surveys.manifest import module

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-surveys"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/sv_0001_surveys.py"


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert SURVEYS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == SURVEYS_MIGRATION_OWNER.owner == "surveys"
    assert module.migration_prefix == SURVEYS_MIGRATION_OWNER.prefix == "sv"
    assert module.migration_branch == SURVEYS_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == SURVEYS_MIGRATION_OWNER.db_schema == "mod_surveys"


def test_the_module_is_tenant_only_and_declares_every_owned_table() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.tables == ("surveys", "survey_invitations", "survey_responses")
    assert module.platform_tables == ()


def test_every_table_is_tenant_scoped_and_every_internal_fk_carries_tenant() -> None:
    for model in models.TENANT_MODELS:
        tenant = model.__table__.c["tenant_id"]
        assert tenant.nullable is False, model.__name__
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.target_fullname for element in constraint.elements}
            if any(target.startswith(f"{models.SCHEMA}.") for target in targets):
                assert (
                    "tenant_id" in constraint.column_keys
                ), f"{model.__name__}.{constraint.name} omits tenant_id"


def test_product_subjects_and_delivery_do_not_leak_into_the_shared_schema() -> None:
    forbidden = {
        "subscriber_id",
        "customer_id",
        "person_id",
        "employee_id",
        "ticket_id",
        "work_order_id",
        "service_id",
        "conversation_id",
        "notification_id",
        "agent_id",
        "technician_id",
        "email",
        "phone",
        "channel",
        "provider",
        "delivery_status",
    }
    for model in models.TENANT_MODELS:
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, f"{model.__name__} owns product columns {sorted(leaked)}"


def test_opaque_subject_fields_have_no_product_foreign_keys() -> None:
    invitation = models.SurveyInvitation.__table__
    for column in ("recipient_ref", "source_owner", "source_event_id", "subject_ref"):
        assert column in invitation.c
        assert not invitation.c[column].foreign_keys


def test_the_package_imports_no_product_or_sibling_module() -> None:
    forbidden_roots = {
        "app",
        "dotmac_sub",
        "dotmac_erp",
        "dotmac_crm",
        "dotmac_ticketing",
        "dotmac_projects",
        "dotmac_inbox",
        "dotmac_integration",
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


def test_services_flush_but_never_own_transactions_or_delivery() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden in (
        ".commit(",
        ".rollback(",
        "SessionLocal(",
        "sessionmaker(",
        "httpx",
        "requests",
        "Notification",
        "send_message",
    ):
        assert forbidden not in source
    assert ".flush(" in source


def test_the_migration_enables_and_forces_rls_on_every_table() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in models.TENANT_TABLES:
        qualified = f"mod_surveys.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;" in source
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;" in source
        assert f"ON {qualified}" in source
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO app_user;"
            in source
        )


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


def test_the_reference_assembly_does_not_claim_candidate_adoption() -> None:
    alembic = (REPO_ROOT / "alembic.ini").read_text(encoding="utf-8")
    allowlist = (REPO_ROOT / ".github/release-modules.json").read_text(
        encoding="utf-8"
    )
    assert "dotmac-surveys" not in alembic
    assert "dotmac-surveys" not in allowlist


def test_distribution_runtime_and_dossier_versions_agree() -> None:
    manifest = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    declared = manifest["tool"]["poetry"]["version"]

    import dotmac_surveys

    assert dotmac_surveys.__version__ == declared
    assert module.version == declared
    assert dossier["package"] == "dotmac-surveys"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"] == ["dotmac_sub", "dotmac_erp"]
