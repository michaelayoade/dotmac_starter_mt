"""Structural boundary canaries for ``dotmac-durable-timers``."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from dotmac_durable_timers import models, service
from dotmac_durable_timers.manifest import module
from dotmac_kernel.namespaces import (
    DURABLE_TIMERS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)
from dotmac_kernel.planes import ModulePlane

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-durable-timers"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/dt_0001_durable_timers.py"


def _module_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MODULE_ROOT.rglob("*.py"))
    )


def _clock_reads(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            rendered = f"{func.value.id}.{func.attr}"
            if rendered in {
                "date.today",
                "datetime.today",
                "datetime.now",
                "datetime.utcnow",
                "time.time",
            }:
                found.add(rendered)
    return found


def test_manifest_matches_the_permanent_namespace_allocation() -> None:
    assert DURABLE_TIMERS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == DURABLE_TIMERS_MIGRATION_OWNER.owner
    assert module.short_code == "timers"
    assert module.migration_prefix == DURABLE_TIMERS_MIGRATION_OWNER.prefix == "dt"
    assert module.migration_branch == DURABLE_TIMERS_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == DURABLE_TIMERS_MIGRATION_OWNER.db_schema == "mod_timers"


def test_manifest_declares_both_selectable_planes_and_the_relay_prerequisite() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == models.PLATFORM_TABLES
    assert module.requires == ("module_database_roles.v1", "outbox_relay.v1")
    assert module.tenant_requires == ("tenant_scope_catalog.v1",)
    assert {frozenset(planes) for planes in module.supported_plane_sets} == {
        frozenset((ModulePlane.TENANT,)),
        frozenset((ModulePlane.PLATFORM,)),
        frozenset((ModulePlane.TENANT, ModulePlane.PLATFORM)),
    }


def test_migration_declares_exactly_the_manifest_prerequisites() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assignments: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(
            node.targets[0], ast.Name
        ):
            continue
        if node.targets[0].id in {
            "COMMON_REQUIRES",
            "TENANT_REQUIRES",
            "PLATFORM_REQUIRES",
        }:
            assignments[node.targets[0].id] = tuple(ast.literal_eval(node.value))
    assert assignments["COMMON_REQUIRES"] == module.requires
    assert assignments["TENANT_REQUIRES"] == module.tenant_requires
    assert assignments["PLATFORM_REQUIRES"] == module.platform_requires


def test_planes_are_declared_disjoint_and_never_cross_linked() -> None:
    assert module.tables and module.platform_tables
    assert not set(module.tables) & set(module.platform_tables)
    for model in models.ALL_MODELS:
        if model.__tablename__.startswith("platform_"):
            assert "tenant_id" not in model.__table__.c
        else:
            tenant = model.__table__.c["tenant_id"]
            assert tenant.nullable is False
        for foreign_key in model.__table__.foreign_keys:
            target = str(foreign_key.target_fullname)
            if model.__tablename__.startswith("platform_"):
                if target.startswith("mod_timers."):
                    assert target.startswith("mod_timers.platform_")
                assert not target.startswith("mod_timers.timers")
            else:
                assert not target.startswith("mod_timers.platform_")


def test_module_has_no_clock_due_scan_claim_loop_or_transaction_authority() -> None:
    source = _module_source()
    assert _clock_reads(source) == set()
    for forbidden in (
        "claim_outbox_batch",
        "claim_platform_outbox_batch",
        "SKIP LOCKED",
        "skip_locked",
        "SessionLocal(",
        "sessionmaker(",
        ".commit(",
        ".rollback(",
    ):
        assert forbidden not in source


def test_clock_guard_has_a_sensitivity_proof() -> None:
    assert _clock_reads("from datetime import datetime\ndatetime.now()") == {
        "datetime.now"
    }
    assert _clock_reads("import time\ntime.time()") == {"time.time"}


def test_no_product_vocabulary_or_native_status_enum_is_shipped() -> None:
    source = _module_source()
    for product_word in (
        "subscriber",
        "invoice",
        "collections_case",
        "support_ticket",
        "cx_handoff",
    ):
        assert product_word not in source
    assert "sqlalchemy.Enum" not in source
    assert "sa.Enum" not in source


def test_extraction_dossier_is_the_reconciled_product_first_contract() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    assert dossier["package"] == "dotmac-durable-timers"
    assert dossier["classification"] == "optional-module"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert "dotmac_sub" in dossier["source_repositories"]
    assert dossier["contract_consumers"] == []
    assert "dotmac_sub" in dossier["candidate_consumers"]
