"""Architecture contract for the product-neutral work-order module.

The source is Sub's authoritative physical-execution path. CRM's matching
tables are retirement evidence, not a second implementation to merge. These
tests keep the extracted owner narrow: work execution and its durable facts,
not workforce rosters, routing, inventory, topology, tickets, projects or
vendor commercials.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from dotmac_kernel.namespaces import WORK_ORDERS_MIGRATION_OWNER
from dotmac_kernel.prerequisites import IDEMPOTENCY_LEDGER_V1
from dotmac_work_orders import module
from dotmac_work_orders.models import TENANT_TABLES, WorkOrder

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "packages/dotmac-work-orders"
SOURCE = PACKAGE / "src/dotmac_work_orders"
MIGRATION = SOURCE / "migrations/versions/wo_0001_work_orders.py"


def _source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert module.code == "work_orders"
    assert module.short_code == "workorders"
    assert module.migration_prefix == "wo"
    assert module.migration_branch == "work_orders"
    assert module.db_schema == "mod_workorders"
    assert WORK_ORDERS_MIGRATION_OWNER.owner == module.code
    assert WORK_ORDERS_MIGRATION_OWNER.prefix == module.migration_prefix
    assert WORK_ORDERS_MIGRATION_OWNER.branch_label == module.migration_branch
    assert WORK_ORDERS_MIGRATION_OWNER.db_schema == module.db_schema


def test_the_module_declares_one_tenant_plane_and_every_owned_table() -> None:
    assert module.platform_tables == ()
    assert module.tables == TENANT_TABLES
    assert module.tables == (
        "work_orders",
        "work_order_assignments",
        "work_order_events",
        "work_order_worklogs",
        "work_order_notes",
        "work_order_evidence",
    )
    assert IDEMPOTENCY_LEDGER_V1.name in module.requires


def test_command_replay_uses_the_kernel_ledger_not_module_columns() -> None:
    for table in WorkOrder.metadata.tables.values():
        if table.schema != "mod_workorders":
            continue
        assert "request_fingerprint" not in table.c
        assert "command_fingerprint" not in table.c
        assert "idempotency_key" not in table.c

    service = _source("service.py")
    assert "execute_once(" in service
    assert 'scope="work_orders.' in service


def test_every_table_has_not_null_tenant_and_composite_identity() -> None:
    from dotmac_work_orders.models import (
        WorkOrderAssignment,
        WorkOrderEvent,
        WorkOrderEvidence,
        WorkOrderNote,
        WorkOrderWorkLog,
    )

    for model in (
        WorkOrder,
        WorkOrderAssignment,
        WorkOrderEvent,
        WorkOrderWorkLog,
        WorkOrderNote,
        WorkOrderEvidence,
    ):
        assert model.__table__.schema == "mod_workorders"
        assert not model.__table__.c.tenant_id.nullable
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_no_foreign_key_leaves_the_module_except_for_the_tenant_catalogue() -> None:
    allowed = {"tenants", *TENANT_TABLES}
    for table in WorkOrder.metadata.tables.values():
        if table.schema != "mod_workorders":
            continue
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name in allowed


def test_migration_creates_rls_and_grants_in_the_same_revision() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "wo_0001_work_orders"' in source
    assert "down_revision = None" in source
    assert 'branch_labels = ("work_orders",)' in source
    for table in TENANT_TABLES:
        assert f'    "{table}",' in source
        assert f'op.create_table(\n        "{table}"' in source
    assert "for table in _TENANT_TABLES:" in source
    assert "_secure_tenant_table(table)" in source
    assert "ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON {qualified}" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {role}" in source


def test_lifecycle_is_pure_and_host_owns_transactions() -> None:
    lifecycle = ast.parse(_source("lifecycle.py"))
    forbidden_roots = {"sqlalchemy", "alembic", "fastapi", "dotmac_kernel"}
    for node in ast.walk(lifecycle):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {(node.module or "").split(".")[0]}
        else:
            continue
        assert not roots & forbidden_roots

    service = _source("service.py")
    assert ".commit(" not in service
    assert ".rollback(" not in service
    assert "SessionLocal" not in service
    assert "sessionmaker" not in service
    assert "HTTPException" not in service
    assert "payload: Any" not in service


def test_the_module_contains_no_product_or_provider_branch() -> None:
    forbidden = {
        "dotmac_sub",
        "dotmac_crm",
        "dotmac_erp",
        "subscriber_id",
        "ticket_id",
        "project_id",
        "installation_project",
        "vendor_id",
        "fiber",
        "topology",
        "inventory",
        "whatsapp",
    }
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*.py")
        if "migrations" not in path.parts
    ).lower()
    for term in forbidden:
        assert term not in production, f"module absorbed product concern {term!r}"


def test_the_dossier_names_sub_as_source_and_crm_as_retirement_only() -> None:
    dossier = tomllib.loads((PACKAGE / "EXTRACTION.toml").read_text("utf-8"))
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac_sub"
    assert "dotmac_sub" in dossier["source_repositories"]
    assert "dotmac_crm" in dossier["source_repositories"]
    assert "retir" in dossier["local_copy_retirement"].lower()


def test_package_version_surfaces_are_one_value() -> None:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text("utf-8"))
    declared = pyproject["tool"]["poetry"]["version"]
    assert declared == "0.1.0a1"
    assert module.version == declared
    assert f'__version__ = "{declared}"' in _source("__init__.py")
