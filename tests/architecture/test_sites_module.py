"""Structural canaries for the future ``dotmac-sites`` module.

Gate 1 deliberately runs this file while the distribution is absent. Gate 2
may add only the smallest implementation satisfying the frozen greenfield
contract.
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages" / "dotmac-sites"
DOSSIER = PACKAGE_ROOT / "EXTRACTION.toml"
MIGRATION = PACKAGE_ROOT / "src/dotmac_sites/migrations/versions/si_0001_sites.py"


def _sites_module(name: str) -> Any:
    try:
        return import_module(f"dotmac_sites.{name}")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_sites"):
            raise
        pytest.fail(
            "dotmac-sites is intentionally absent: record this Gate 1 RED "
            "on Observer before adding the implementation"
        )


def test_the_distribution_exists_only_after_the_red_canary_is_recorded() -> None:
    assert (
        importlib.util.find_spec("dotmac_sites") is not None
    ), "Gate 1 expected RED: dotmac-sites has not been implemented"


def test_greenfield_dossier_pins_the_complete_six_repository_census() -> None:
    dossier = tomllib.loads(DOSSIER.read_text(encoding="utf-8"))
    assert dossier["package"] == "dotmac-sites"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac_backoffice"
    assert dossier["source_revisions"] == [
        "dotmac_starter_mt:c6ef6cd7b13105bd95c3faf354ffee9032077625",
        "dotmac_mkt:7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d",
        "dotmac_sub:510b80ca7fab4f54a57f261872f94b5e972c8eb6",
        "dotmac_erp:dd6416cd981ffdf48564e2770b87d3cd7201186c",
        "dotmac_crm:60daaa2dd305696636632f48505ab784110a55d2",
        "dotmac_backoffice:fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d",
    ]


def test_manifest_declares_exactly_the_five_tenant_tables() -> None:
    manifest = _sites_module("manifest")
    assert set(manifest.module.tables) == {
        "sites",
        "pages",
        "page_revisions",
        "site_revisions",
        "site_revision_pages",
    }
    assert tuple(manifest.module.platform_tables) == ()


def test_manifest_uses_the_permanent_sites_allocation() -> None:
    manifest = _sites_module("manifest")
    namespaces = import_module("dotmac_kernel.namespaces")
    owner = namespaces.SITES_MIGRATION_OWNER
    assert owner in namespaces.MIGRATION_OWNER_LEDGER
    assert manifest.module.code == owner.owner == "sites"
    assert manifest.module.short_code == "sites"
    assert manifest.module.migration_prefix == owner.prefix == "si"
    assert manifest.module.migration_branch == owner.branch_label == "sites"
    assert manifest.module.db_schema == namespaces.module_schema("sites")


def test_manifest_requires_only_the_kernel_tenant_plane() -> None:
    manifest = _sites_module("manifest")
    prerequisites = import_module("dotmac_kernel.prerequisites")
    assert set(manifest.module.requires) == {
        prerequisites.TENANT_SCOPE_CATALOG_V1.name,
        prerequisites.MODULE_DATABASE_ROLES_V1.name,
    }


def test_every_model_is_tenant_scoped_and_schema_qualified() -> None:
    models = _sites_module("models")
    manifest = _sites_module("manifest")
    for model in models.SITES_MODELS:
        assert model.__table__.schema == manifest.module.db_schema
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None, f"{model.__name__} has no tenant_id"
        assert not tenant_id.nullable, f"{model.__name__}.tenant_id must be NOT NULL"
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, model.__name__


def test_same_module_relationships_include_tenant_and_site_identity() -> None:
    models = _sites_module("models")
    expected = {
        models.Page: {"tenant_id", "site_id"},
        models.PageRevision: {"tenant_id", "site_id", "page_id"},
        models.SiteRevision: {"tenant_id", "site_id"},
        models.SiteRevisionPage: {
            "tenant_id",
            "site_id",
            "site_revision_id",
        },
    }
    for model, required in expected.items():
        assert any(
            required <= {column.name for column in constraint.columns}
            for constraint in model.__table__.foreign_key_constraints
        ), f"{model.__name__} lacks a tenant/site-composite foreign key"

    membership = models.SiteRevisionPage.__table__
    assert any(
        {"tenant_id", "site_id", "page_id", "page_revision_id"}
        == {column.name for column in constraint.columns}
        for constraint in membership.foreign_key_constraints
    )


def test_external_references_are_opaque_not_foreign_keys() -> None:
    models = _sites_module("models")
    for model in models.SITES_MODELS:
        for column_name in ("created_by_ref", "file_refs", "form_refs"):
            column = model.__table__.columns.get(column_name)
            if column is not None:
                assert not column.foreign_keys


@pytest.mark.parametrize(
    "forbidden",
    [
        "provider",
        "credential",
        "access_token",
        "oauth",
        "deployment_id",
        "remote_url",
        "dns",
        "certificate",
        "publication_status",
        "lead_id",
        "submission_id",
        "analytics",
    ],
)
def test_no_foreign_owner_leaks_into_the_persistence_contract(forbidden: str) -> None:
    models = _sites_module("models")
    published_names = {
        name
        for model in models.SITES_MODELS
        for name in [
            model.__tablename__,
            *(column.name for column in model.__table__.columns),
        ]
    }
    assert all(forbidden not in name for name in published_names)


def test_package_source_has_no_sibling_module_provider_or_network_import() -> None:
    source_root = PACKAGE_ROOT / "src" / "dotmac_sites"
    forbidden = (
        "dotmac_files",
        "dotmac_forms",
        "dotmac_publishing",
        "dotmac_integration",
        "boto3",
        "googleapiclient",
        "httpx",
        "requests",
    )
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert not violations, "foreign owner/provider imports found:\n" + "\n".join(
        violations
    )


def test_services_never_own_transactions_or_construct_sessions() -> None:
    service = PACKAGE_ROOT / "src/dotmac_sites/service.py"
    tree = ast.parse(service.read_text(encoding="utf-8"))
    attribute_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    name_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"commit", "rollback"} & attribute_calls)
    assert not ({"Session", "SessionLocal", "sessionmaker"} & name_calls)


def test_root_migration_creates_the_secure_immutable_plane() -> None:
    manifest = _sites_module("manifest")
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ddl = "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "si_0001_sites"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("sites",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(manifest.module.requires)
    assert isinstance(assigned["depends_on"], ast.Call)
    assert getattr(assigned["depends_on"].func, "id", None) == "resolve_depends_on"

    for table in manifest.module.tables:
        qualified = f"mod_sites.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in ddl
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in ddl
        assert f"CREATE POLICY {table}_tenant_isolation" in ddl
        assert f"ON {qualified}" in ddl
        assert f"ON {qualified} TO app_user" in ddl
    assert "TO platform_api" not in ddl
    for table in ("page_revisions", "site_revision_pages"):
        assert f"{table}_append_only" in ddl
    assert "site_revisions_immutable_snapshot" in ddl


def test_lineage_passes_the_composed_migration_gate() -> None:
    manifest = _sites_module("manifest")
    gate = import_module("dotmac_kernel.migrations.gate")
    bindings = import_module("app.migration_bindings")
    report = gate.run_gate(
        [manifest.module],
        [
            PROJECT_ROOT
            / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            PROJECT_ROOT / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=bindings.ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.violations
