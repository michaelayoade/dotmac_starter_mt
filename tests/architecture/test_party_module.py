"""Structural contract for the extracted Party context owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, PARTY_MIGRATION_OWNER
from dotmac_party import models, service
from dotmac_party.manifest import module
from sqlalchemy import CheckConstraint, String, UniqueConstraint

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/pt_0001_party_context.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_matches_the_same_change_namespace_allocation() -> None:
    assert PARTY_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == "party"
    assert module.short_code == "party"
    assert module.migration_prefix == "pt"
    assert module.migration_branch == "party"
    assert module.db_schema == "mod_party"
    assert tuple(module.tables) == (
        "party_roles",
        "party_relationships",
        "party_memberships",
        "party_contact_points",
        "party_external_references",
    )
    assert tuple(module.platform_tables) == ()
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "party_person_catalog.v1",
    }


def test_every_table_has_direct_tenant_identity_and_tenant_leading_identity() -> None:
    for table_name in module.tables:
        table = models.metadata_table(table_name)
        assert table.schema == "mod_party"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("tenant_id", "id") in uniques, table_name


def test_open_vocabularies_are_plain_strings_without_database_check_lists() -> None:
    open_columns = {
        "party_roles": "role_type",
        "party_relationships": "relationship_type",
        "party_memberships": "membership_type",
        "party_contact_points": "channel_type",
    }
    for table_name, column_name in open_columns.items():
        table = models.metadata_table(table_name)
        assert isinstance(table.c[column_name].type, String)
        check_sql = " ".join(
            str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        )
        assert column_name not in check_sql


def test_relationships_link_roles_not_ambiguous_bare_parties() -> None:
    table = models.PartyRelationship.__table__
    assert "subject_role_id" in table.c
    assert "object_role_id" in table.c
    assert "subject_party_id" not in table.c
    assert "object_party_id" not in table.c
    targets = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.foreign_key_constraints
    }
    assert (
        "mod_party.party_roles.tenant_id",
        "mod_party.party_roles.id",
    ) in targets


def test_no_customer_or_contact_identity_table_is_reintroduced() -> None:
    declared = set(module.tables)
    assert "customers" not in declared
    assert "contacts" not in declared
    assert "parties" not in declared
    assert "party_persons" not in declared
    assert "party_organizations" not in declared


def test_contact_values_are_not_globally_unique_identity_keys() -> None:
    table = models.PartyContactPoint.__table__
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("normalized_value",) not in uniques
    assert (
        "tenant_id",
        "party_id",
        "channel_type",
        "normalized_value",
        "scope_key",
    ) in uniques


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
    assert ast.literal_eval(assigned["revision"]) == "pt_0001_party_context"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("party",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert isinstance(assigned["depends_on"], ast.Call)
    assert getattr(assigned["depends_on"].func, "id", None) == "resolve_depends_on"

    for table in module.tables:
        qualified = f"mod_party.{table}"
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
                    ("dotmac_party", "dotmac_kernel")
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


def test_product_first_dossier_keeps_release_and_cutover_gates_visible() -> None:
    dossier = (ROOT.parents[1] / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert 'status = "audit-complete"' in dossier
    assert "dotmac_sub:app/services/party.py" in dossier
    assert "reader cutover" in dossier
    assert "writer" in dossier
