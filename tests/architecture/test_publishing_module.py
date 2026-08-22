"""Structural canaries for the future ``dotmac-publishing`` module.

Gate 1 deliberately runs this file before the distribution exists. Gate 2 may
add only the smallest implementation satisfying the frozen product-first
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
PACKAGE_ROOT = PROJECT_ROOT / "packages" / "dotmac-publishing"
DOSSIER = PACKAGE_ROOT / "EXTRACTION.toml"
MIGRATION = (
    PACKAGE_ROOT / "src/dotmac_publishing/migrations/versions/pb_0001_publishing.py"
)


def _publishing_module(name: str) -> Any:
    try:
        return import_module(f"dotmac_publishing.{name}")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_publishing"):
            raise
        pytest.fail(
            "dotmac-publishing is intentionally absent: record this Gate 1 "
            "RED on Observer before adding the implementation"
        )


def test_the_distribution_exists_only_after_the_red_canary_is_recorded() -> None:
    assert (
        importlib.util.find_spec("dotmac_publishing") is not None
    ), "Gate 1 expected RED: dotmac-publishing has not been implemented"


def test_product_first_dossier_pins_the_qualifying_mkt_source() -> None:
    dossier = tomllib.loads(DOSSIER.read_text(encoding="utf-8"))
    assert dossier["package"] == "dotmac-publishing"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac-erp"
    assert dossier["source_revisions"] == [
        "dotmac_mkt:7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d"
    ]
    assert {
        "dotmac_mkt:app/models/post_delivery.py",
        "dotmac_mkt:app/services/publishing_service.py",
        "dotmac_mkt:app/tasks/publish_scheduled.py",
    } <= set(dossier["source_paths"])


def test_manifest_declares_exactly_the_four_tenant_tables() -> None:
    manifest = _publishing_module("manifest")
    assert set(manifest.module.tables) == {
        "publication_releases",
        "publication_deliveries",
        "publication_attempts",
        "publication_observations",
    }
    assert tuple(manifest.module.platform_tables) == ()


def test_manifest_uses_the_permanent_publishing_allocation() -> None:
    manifest = _publishing_module("manifest")
    namespaces = import_module("dotmac_kernel.namespaces")
    owner = namespaces.PUBLISHING_MIGRATION_OWNER
    assert owner in namespaces.MIGRATION_OWNER_LEDGER
    assert manifest.module.code == owner.owner == "publishing"
    assert manifest.module.short_code == "publishing"
    assert manifest.module.migration_prefix == owner.prefix == "pb"
    assert manifest.module.migration_branch == owner.branch_label == "publishing"
    assert manifest.module.db_schema == namespaces.module_schema("publishing")


def test_manifest_declares_kernel_idempotency_and_outbox_prerequisites() -> None:
    manifest = _publishing_module("manifest")
    prerequisites = import_module("dotmac_kernel.prerequisites")
    assert set(manifest.module.requires) == {
        prerequisites.TENANT_SCOPE_CATALOG_V1.name,
        prerequisites.MODULE_DATABASE_ROLES_V1.name,
        prerequisites.IDEMPOTENCY_LEDGER_V1.name,
        prerequisites.OUTBOX_RELAY_V1.name,
    }


def test_every_model_is_tenant_scoped_and_schema_qualified() -> None:
    models = _publishing_module("models")
    manifest = _publishing_module("manifest")
    for model in models.PUBLISHING_MODELS:
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


def test_same_module_relationships_include_tenant_id() -> None:
    models = _publishing_module("models")
    expected = {
        models.PublicationDelivery: {"tenant_id", "publication_release_id"},
        models.PublicationAttempt: {"tenant_id", "publication_delivery_id"},
        models.PublicationObservation: {"tenant_id", "publication_attempt_id"},
    }
    for model, local_columns in expected.items():
        assert any(
            local_columns == {column.name for column in constraint.columns}
            for constraint in model.__table__.foreign_key_constraints
        ), f"{model.__name__} lacks the required tenant-composite foreign key"


def test_external_references_are_opaque_not_foreign_keys() -> None:
    models = _publishing_module("models")
    for model in models.PUBLISHING_MODELS:
        for column_name in (
            "source_ref",
            "actor_ref",
            "target_ref",
            "outbox_event_ref",
            "receipt_ref",
            "remote_ref",
        ):
            column = model.__table__.columns.get(column_name)
            if column is not None:
                assert not column.foreign_keys, (
                    f"{model.__name__}.{column_name} must stay opaque; a module "
                    "cannot foreign-key a sibling, product or Integrator table"
                )


@pytest.mark.parametrize(
    "forbidden",
    [
        "provider",
        "credential",
        "access_token",
        "oauth",
        "channel_id",
        "content_item_id",
        "site_revision_id",
        "campaign_id",
        "person_id",
    ],
)
def test_no_foreign_owner_leaks_into_the_persistence_contract(forbidden: str) -> None:
    models = _publishing_module("models")
    published_names = {
        name
        for model in models.PUBLISHING_MODELS
        for name in [
            model.__tablename__,
            *(column.name for column in model.__table__.columns),
        ]
    }
    assert all(forbidden not in name for name in published_names)


def test_package_source_has_no_sibling_module_or_provider_import() -> None:
    source_root = PACKAGE_ROOT / "src" / "dotmac_publishing"
    forbidden = (
        "dotmac_content",
        "dotmac_sites",
        "dotmac_campaigns",
        "dotmac_integration",
        "dotmac_durable_timers",
        "googleapiclient",
        "facebook",
        "linkedin",
        "tweepy",
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


def test_public_surface_contains_no_provider_or_channel_vocabulary() -> None:
    public = import_module("dotmac_publishing")
    forbidden = ("Provider", "Channel", "Credential", "Adapter")
    assert not [
        name for name in public.__all__ if any(token in name for token in forbidden)
    ]


def test_services_never_own_transactions_or_construct_sessions() -> None:
    service = PACKAGE_ROOT / "src/dotmac_publishing/service.py"
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


def test_root_migration_declares_effects_and_creates_the_secure_plane() -> None:
    manifest = _publishing_module("manifest")
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
    assert ast.literal_eval(assigned["revision"]) == "pb_0001_publishing"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("publishing",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(manifest.module.requires)
    assert isinstance(assigned["depends_on"], ast.Call)
    assert getattr(assigned["depends_on"].func, "id", None) == "resolve_depends_on"

    for table in manifest.module.tables:
        qualified = f"mod_publishing.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in ddl
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in ddl
        assert f"CREATE POLICY {table}_tenant_isolation" in ddl
        assert f"ON {qualified}" in ddl
        assert f"ON {qualified} TO app_user" in ddl
    assert "TO platform_api" not in ddl


def test_lineage_passes_the_composed_migration_gate() -> None:
    manifest = _publishing_module("manifest")
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
