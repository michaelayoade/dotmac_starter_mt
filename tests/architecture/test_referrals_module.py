"""Structural and ownership guards for ``dotmac-referrals``."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    REFERRALS_MIGRATION_OWNER,
    module_schema,
)
from dotmac_referrals import models
from dotmac_referrals.manifest import module

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/rf_0001_referrals.py"
LOCATION_MIGRATION = MODULE_ROOT / "migrations/versions/rf_0002_service_location_ref.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert REFERRALS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == REFERRALS_MIGRATION_OWNER.owner == "referrals"
    assert module.short_code == "referrals"
    assert module.migration_prefix == REFERRALS_MIGRATION_OWNER.prefix == "rf"
    assert module.migration_branch == "referrals"
    assert module.db_schema == module_schema("referrals") == "mod_referrals"


def test_referrals_is_tenant_only_and_every_fk_carries_tenant() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_referrals"
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None and not tenant_id.nullable
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.schema for element in constraint.elements}
            if "mod_referrals" in targets:
                assert "tenant_id" in constraint.columns


def test_migration_creates_forced_rls_and_tenant_composite_identity() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    tree = ast.parse(MIGRATION_TEXT)
    secured = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_TABLES"
            for target in node.targets
        )
    )
    assert tuple(secured) == tuple(module.tables)
    assert "ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY" in statements
    assert "CREATE POLICY {table}_tenant_isolation" in statements
    assert "UNIQUE (tenant_id, id)" in statements


def test_owner_has_no_product_provider_or_transaction_boundary() -> None:
    forbidden_imports = {
        "app",
        "httpx",
        "requests",
        "dotmac_billing",
        "dotmac_customers",
        "dotmac_sales",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots & forbidden_imports
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                assert name not in forbidden_calls
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service


def test_location_is_an_opaque_ref_with_no_coordinate_or_address_column() -> None:
    columns: set[str] = set()
    for model in models.ALL_MODELS:
        columns |= set(model.__table__.columns.keys())
    assert "service_location_ref" in columns
    forbidden = {
        "address",
        "address_line",
        "street",
        "city",
        "postal_code",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "coordinates",
        "geom",
        "pin",
    }
    assert not forbidden & columns
    referral_columns = models.Referral.__table__.columns
    assert referral_columns["service_location_ref"].nullable is True
    # Only the attribution row holds it; the reward/conversion path never does.
    holders = {
        model.__tablename__
        for model in models.ALL_MODELS
        if "service_location_ref" in model.__table__.columns
    }
    assert holders == {"referrals"}


def test_location_migration_is_a_backward_compatible_child_of_the_root() -> None:
    source = LOCATION_MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "rf_0002_service_location_ref"' in source
    assert 'down_revision = "rf_0001_referrals"' in source
    assert "branch_labels = None" in source
    assert "nullable=True" in source
    assert "op.add_column(" in source
    assert "op.drop_column(" in source

    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    repo_root = Path(__file__).resolve().parents[2]
    report = run_gate(
        [module],
        [
            repo_root / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            repo_root / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.violations
    assert report.attribution["rf_0002_service_location_ref"]["owner"] == "referrals"
