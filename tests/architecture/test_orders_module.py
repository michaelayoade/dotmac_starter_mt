"""Structural canaries for ``dotmac-orders`` (ADR-0030 §5b)."""

from __future__ import annotations

import ast
import inspect
import json
import tomllib
from pathlib import Path

import dotmac_orders
from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, ORDERS_MIGRATION_OWNER
from dotmac_orders import contracts, models, service
from dotmac_orders.manifest import module

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-orders"
MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/or_0001_orders.py"
MIGRATIONS = MIGRATION.parent


def _source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MODULE_ROOT.rglob("*.py"))
    )


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert ORDERS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == ORDERS_MIGRATION_OWNER.owner == "orders"
    assert module.migration_prefix == ORDERS_MIGRATION_OWNER.prefix == "or"
    assert module.migration_branch == ORDERS_MIGRATION_OWNER.branch_label == "orders"
    assert models.SCHEMA == ORDERS_MIGRATION_OWNER.db_schema == "mod_orders"


def test_all_three_version_surfaces_agree() -> None:
    metadata = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = metadata["tool"]["poetry"]["version"]
    assert declared == dotmac_orders.__version__ == module.version == "0.1.0a1"


def test_orders_is_declared_tenant_only() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.tables
    assert module.platform_tables == ()


def test_every_order_table_has_a_non_nullable_tenant_identity() -> None:
    for model in models.ALL_MODELS:
        tenant = model.__table__.c["tenant_id"]
        assert tenant.nullable is False
        constraints = {
            tuple(constraint.columns.keys())
            for constraint in model.__table__.constraints
            if hasattr(constraint, "columns")
        }
        assert any("tenant_id" in columns for columns in constraints), model.__name__


def test_every_module_foreign_key_is_tenant_composite() -> None:
    for model in models.ALL_MODELS:
        for constraint in model.__table__.foreign_key_constraints:
            targets = tuple(element.target_fullname for element in constraint.elements)
            if all(target.endswith("tenants.id") for target in targets):
                continue
            assert "tenant_id" in constraint.columns
            assert any(target.endswith(".tenant_id") for target in targets), (
                model.__name__,
                targets,
            )


def test_accepted_lines_and_evidence_are_structurally_append_only() -> None:
    for model in models.IMMUTABLE_MODELS:
        assert "updated_at" not in model.__table__.c
        assert "is_active" not in model.__table__.c

    source = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE" in source
    assert "refuse_immutable_mutation" in source
    for table in models.IMMUTABLE_TABLES:
        assert f'"{table}"' in source
    assert "protect_frozen_order_snapshot" in source
    assert "protect_coverage_gate" in source
    assert "protect_fulfillment_request" in source
    assert "orders_require_complete_snapshot" in source
    assert "coverage_gates_require_consistency" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "UPDATE, DELETE ON mod_orders" not in source


def test_line_snapshots_carry_both_values_and_version_provenance() -> None:
    columns = models.OrderLineSnapshot.__table__.c
    for required in (
        "description",
        "quantity",
        "unit_price",
        "discount_amount",
        "tax_amount",
        "tax_snapshot",
        "line_total",
        "price_version_ref",
        "terms_ref",
        "terms_snapshot",
        "specification_ref",
        "snapshot_fingerprint",
    ):
        assert required in columns


def test_the_specification_reference_is_opaque() -> None:
    source = _source()
    for dereference in (
        "urlparse(",
        "requests.",
        "httpx.",
        "urllib.",
        "json.loads(specification",
    ):
        assert dereference not in source


def test_the_module_imports_no_product_provider_or_sibling_owner() -> None:
    forbidden_roots = {
        "app",
        "dotmac_billing",
        "dotmac_collections",
        "dotmac_fulfillment",
        "dotmac_subscriptions",
        "vendor_cp",
    }
    for path in sorted(MODULE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            for imported in imports:
                assert imported.split(".")[0] not in forbidden_roots, (
                    path.name,
                    imported,
                )

    lowered = _source().lower()
    for provider in ("splynx", "blesta", "erpnext", "paystack", "stripe"):
        assert provider not in lowered


def test_services_obey_the_kernel_transaction_and_delivery_owners() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "execute_once(" in source
    assert "enqueue_event(" in source
    for forbidden in (
        ".commit(",
        ".rollback(",
        "SessionLocal(",
        "sessionmaker(",
        "create_engine(",
    ):
        assert forbidden not in source
    for second_owner in (
        "class IdempotencyRecord",
        "class OutboxEvent",
        "request_fingerprint = mapped_column",
    ):
        assert second_owner not in source


def test_importing_orders_does_not_build_the_kernel_database_engine() -> None:
    for path in (MODULE_ROOT / "engine.py", MODULE_ROOT / "service.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_level = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "dotmac_kernel.idempotency" not in module_level, path.name
        assert "dotmac_kernel.messaging" not in module_level, path.name


def test_persisted_totals_have_structural_balance_checks() -> None:
    model_checks = {
        constraint.name
        for model in (models.Order, models.OrderLineSnapshot)
        for constraint in model.__table__.constraints
        if constraint.name is not None
    }
    assert "ck_orders_totals_balance" in model_checks
    assert "ck_order_line_snapshots_totals_balance" in model_checks

    migration = MIGRATION.read_text(encoding="utf-8")
    assert "subtotal_amount - discount_amount + tax_amount = total_amount" in migration
    assert "extended_price - discount_amount + tax_amount = line_total" in migration


def test_lifecycle_and_refusal_audit_actions_are_declared_and_consumed() -> None:
    expected = {
        "orders.submitted",
        "orders.accepted",
        "orders.cancelled",
        "orders.cancellation_refused",
        "orders.coverage_observed",
        "orders.fulfillment_acknowledged",
        "orders.fulfillment_reconciled",
    }
    assert set(module.audit_actions) == expected
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "write_audit_event(" in source
    for action in expected:
        assert f'"{action}"' in source


def test_public_contracts_are_frozen_and_typed() -> None:
    source = (MODULE_ROOT / "contracts.py").read_text(encoding="utf-8")
    assert "Mapping[" not in source
    assert "dict[" not in source
    for name in contracts.__all__:
        value = getattr(contracts, name)
        if inspect.isclass(value) and hasattr(value, "__dataclass_fields__"):
            params = value.__dataclass_params__
            assert params.frozen is True, name
            for field in value.__dataclass_fields__.values():
                assert field.type not in {dict, object}, (name, field.name, field.type)


def test_the_migration_declares_the_manifest_prerequisites() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    literals: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"COMMON_REQUIRES", "TENANT_REQUIRES"}:
                literals[name] = tuple(ast.literal_eval(node.value))
    assert literals["COMMON_REQUIRES"] == module.requires
    assert literals["TENANT_REQUIRES"] == module.tenant_requires


def test_the_lineage_passes_the_composed_migration_gate() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATIONS,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, f"composed gate violations: {report.violations}"


def test_the_gate_refuses_orders_when_an_assembly_binds_nothing() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATIONS,
        ],
        bindings=(),
    )
    assert not report.ok
    assert any("binds no provider" in violation for violation in report.violations)


def test_extraction_dossier_preserves_the_product_first_source() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())
    assert dossier["package"] == "dotmac-orders"
    assert dossier["status"] == "audit-complete"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"][0] == "dotmac_sub"
    assert "docs/inventories/orders-sources.md" in dossier["inventory_evidence"]


def test_an_unadopted_candidate_is_not_release_allowlisted() -> None:
    allowlist = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]
    assert "dotmac-orders" not in allowlist


def test_no_native_enum_or_mutable_snapshot_field_reopens_the_source_defect() -> None:
    source = _source()
    assert "postgresql.ENUM" not in source
    assert "onupdate=" not in source
    assert "SalesOrderStatus" not in source
    assert "SalesOrderPaymentStatus" not in source
    assert "CoverageResolutionRegistry" not in source
