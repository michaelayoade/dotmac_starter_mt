"""Structural canaries for the future ``dotmac-content`` module.

The file is deliberately present before the distribution. Gate 1 records the
resulting RED on Observer; Gate 2 adds the smallest implementation satisfying
this contract.
"""

from __future__ import annotations

import importlib.util
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "packages" / "dotmac-content"


def _content_module(name: str) -> Any:
    try:
        return import_module(f"dotmac_content.{name}")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_content"):
            raise
        pytest.fail(
            "dotmac-content is intentionally absent: record this Gate 1 RED "
            "on Observer before adding the implementation"
        )


def test_the_distribution_exists_only_after_the_red_canary_is_recorded() -> None:
    assert importlib.util.find_spec("dotmac_content") is not None, (
        "Gate 1 expected RED: dotmac-content has not been implemented"
    )


def test_manifest_declares_exactly_the_five_tenant_tables() -> None:
    manifest = _content_module("manifest")
    assert set(manifest.module.tables) == {
        "content_plans",
        "content_items",
        "content_variants",
        "content_plan_creatives",
        "content_item_creatives",
    }
    assert tuple(manifest.module.platform_tables) == ()


def test_manifest_uses_the_permanent_content_allocation() -> None:
    manifest = _content_module("manifest")
    namespaces = import_module("dotmac_kernel.namespaces")
    owner = namespaces.CONTENT_MIGRATION_OWNER
    assert owner in namespaces.MIGRATION_OWNER_LEDGER
    assert manifest.module.code == owner.owner == "content"
    assert manifest.module.short_code == "content"
    assert manifest.module.migration_prefix == owner.prefix == "ct"
    assert manifest.module.migration_branch == owner.branch_label == "content"
    assert manifest.module.db_schema == namespaces.module_schema("content")


def test_every_model_is_tenant_scoped_and_schema_qualified() -> None:
    models = _content_module("models")
    manifest = _content_module("manifest")
    for model in models.CONTENT_MODELS:
        assert model.__table__.schema == manifest.module.db_schema
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None, f"{model.__name__} has no tenant_id"
        assert not tenant_id.nullable, f"{model.__name__}.tenant_id must be NOT NULL"


def test_same_module_relationships_include_tenant_id() -> None:
    models = _content_module("models")
    expected = {
        models.ContentItem: {"tenant_id", "content_plan_id"},
        models.ContentVariant: {"tenant_id", "content_item_id"},
        models.ContentPlanCreative: {"tenant_id", "content_plan_id"},
        models.ContentItemCreative: {"tenant_id", "content_item_id"},
    }
    for model, local_columns in expected.items():
        assert any(
            local_columns == {column.name for column in constraint.columns}
            for constraint in model.__table__.foreign_key_constraints
        ), f"{model.__name__} lacks the required tenant-composite foreign key"


def test_opaque_external_references_are_not_foreign_keys() -> None:
    models = _content_module("models")
    for model in models.CONTENT_MODELS:
        for column_name in ("created_by_ref", "file_ref"):
            column = model.__table__.columns.get(column_name)
            if column is not None:
                assert not column.foreign_keys, (
                    f"{model.__name__}.{column_name} must stay opaque; modules "
                    "do not foreign-key sibling or product tables"
                )


@pytest.mark.parametrize(
    "forbidden",
    [
        "campaign_member",
        "person_id",
        "role_id",
        "channel_id",
        "provider",
        "credential",
        "external_post_id",
        "published_at",
        "delivery_status",
        "task_id",
        "drive_file_id",
        "drive_url",
    ],
)
def test_no_foreign_owner_leaks_into_the_persistence_contract(forbidden: str) -> None:
    models = _content_module("models")
    published_names = {
        name
        for model in models.CONTENT_MODELS
        for name in [
            model.__tablename__,
            *(column.name for column in model.__table__.columns),
        ]
    }
    assert all(forbidden not in name for name in published_names)


def test_package_source_has_no_sibling_module_or_provider_import() -> None:
    source_root = PACKAGE_ROOT / "src" / "dotmac_content"
    forbidden = (
        "dotmac_files",
        "dotmac_publishing",
        "dotmac_campaigns",
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
