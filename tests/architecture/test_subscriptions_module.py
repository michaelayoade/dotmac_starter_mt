"""Structural canaries for the reusable dual-plane subscriptions module."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import tomllib
from pathlib import Path

import dotmac_subscriptions
from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    SUBSCRIPTIONS_MIGRATION_OWNER,
)
from dotmac_kernel.planes import ModulePlane
from dotmac_subscriptions import module
from dotmac_subscriptions.models import PLATFORM_TABLES, TENANT_TABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-subscriptions"
SOURCE_ROOT = PACKAGE_ROOT / "src/dotmac_subscriptions"
MIGRATION = SOURCE_ROOT / "migrations/versions/su_0001_subscriptions.py"

_SIBLINGS = {
    "dotmac_billing",
    "dotmac_collections",
    "dotmac_durable_timers",
    "dotmac_orders",
}
_PRODUCTS = {"app", "vendor_cp", "dotmac_sub", "dotmac_erp"}
_TIMING_WORDS = {"advance", "arrears", "prepaid", "postpaid"}
_PRESET_WORDS = {"monthly", "quarterly", "annual", "biannual", "yearly"}
_FORBIDDEN_LITERAL_DEFAULTS = {
    "NGN",
    "USD",
    "paystack",
    "flutterwave",
    "stripe",
    "remita",
    "splynx",
    "vendor_cp",
    "dotmac_sub",
}
_FORBIDDEN_MODEL_COLUMNS = {
    "tax_amount",
    "gross_amount",
    "resolved_amount",
    "accounting_treatment",
    "resolution_kind",
    "opened_at",
    "resolved_at",
    "due_at",
    "reversed_by_id",
    "tax_rate_id",
    "tax_rate_percent",
    "tax_inclusive",
    "invoice_id",
    "payment_id",
    "balance",
    "outstanding",
    "subscriber_id",
    "account_id",
    "sales_order_id",
    "deployment_id",
    "capability_code",
    "plan_name",
}


def _python_files() -> tuple[Path, ...]:
    return tuple(
        path for path in SOURCE_ROOT.rglob("*.py") if "migrations" not in path.parts
    )


def _import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _preset_enum_violations(source: str) -> set[str]:
    violations: set[str] = set()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        members = {
            child.targets[0].id.lower()
            for child in node.body
            if isinstance(child, ast.Assign)
            and len(child.targets) == 1
            and isinstance(child.targets[0], ast.Name)
        }
        if members & _PRESET_WORDS:
            violations.add(node.name)
    return violations


def _timing_shape_violations(source: str) -> set[str]:
    violations: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            parts = set(re.split(r"[^a-z]+", node.name.lower()))
            if parts & _TIMING_WORDS:
                violations.add(node.name)
    return violations


def _model_column_violations() -> set[str]:
    from dotmac_subscriptions import models

    violations: set[str] = set()
    for class_name in models.__all__:
        value = getattr(models, class_name)
        table = getattr(value, "__table__", None)
        if table is not None:
            violations.update(column.name for column in table.columns)
    return violations & _FORBIDDEN_MODEL_COLUMNS


def _float_literals(source: str) -> list[float]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]


def _forbidden_defaults(source: str) -> set[str]:
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _FORBIDDEN_LITERAL_DEFAULTS
    }


def _scope_shape_violations(source: str) -> set[str]:
    violations: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.arg) and node.arg == "tenant_id":
            annotation = ast.unparse(node.annotation) if node.annotation else ""
            if "None" in annotation:
                violations.add("nullable_tenant_id")
        if isinstance(node, ast.Name) and node.id == "scope_kind":
            violations.add("scope_kind")
        identifier = (
            node.id
            if isinstance(node, ast.Name)
            else node.arg
            if isinstance(node, ast.arg)
            else ""
        )
        if "sentinel_tenant" in identifier:
            violations.add("sentinel_tenant")
    return violations


def test_package_identity_and_dossier_exist() -> None:
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    assert pyproject["tool"]["poetry"]["name"] == "dotmac-subscriptions"
    assert pyproject["tool"]["poetry"]["version"] == dotmac_subscriptions.__version__
    assert module.version == dotmac_subscriptions.__version__
    assert dossier["package"] == "dotmac-subscriptions"
    assert dossier["source_mode"] == "product-first"
    assert dossier["contract_consumers"] == []


def test_manifest_declares_one_dual_plane_owner() -> None:
    assert module.code == "subscriptions"
    assert module.short_code == "subscriptions"
    assert module.migration_prefix == "su"
    assert module.migration_branch == "subscriptions"
    assert module.tables == TENANT_TABLES
    assert module.platform_tables == PLATFORM_TABLES
    assert module.requires == (
        "module_database_roles.v1",
        "idempotency_ledger.v1",
    )
    assert len(TENANT_TABLES) == 7
    assert len(PLATFORM_TABLES) == 7
    assert set(TENANT_TABLES).isdisjoint(PLATFORM_TABLES)
    assert module.supported_plane_sets == (
        (ModulePlane.TENANT,),
        (ModulePlane.PLATFORM,),
        (ModulePlane.PLATFORM, ModulePlane.TENANT),
    )
    assert SUBSCRIPTIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.migration_owner() == SUBSCRIPTIONS_MIGRATION_OWNER


def test_package_imports_no_sibling_or_product_implementation() -> None:
    violations: dict[str, set[str]] = {}
    for path in _python_files():
        forbidden = _import_roots(path.read_text()) & (_SIBLINGS | _PRODUCTS)
        if forbidden:
            violations[str(path.relative_to(REPO_ROOT))] = forbidden

    assert not violations, violations


def test_import_guard_sensitivity_proof() -> None:
    assert _import_roots("import dotmac_billing") & _SIBLINGS
    assert not (_import_roots("import dotmac_kernel") & _SIBLINGS)


def test_cadence_has_no_product_preset_enum() -> None:
    violations: dict[str, set[str]] = {}
    for path in _python_files():
        found = _preset_enum_violations(path.read_text())
        if found:
            violations[str(path.relative_to(REPO_ROOT))] = found

    assert not violations, violations


def test_preset_guard_discriminates_calendar_units() -> None:
    assert _preset_enum_violations(
        "from enum import Enum\nclass BillingCycle(Enum):\n    monthly = 'monthly'\n"
    ) == {"BillingCycle"}
    assert not _preset_enum_violations(
        "from enum import Enum\nclass IntervalUnit(Enum):\n"
        "    day = 'day'\n    week = 'week'\n    month = 'month'\n    year = 'year'\n"
    )


def test_collection_timing_is_a_field_not_a_parallel_shape() -> None:
    violations: dict[str, set[str]] = {}
    for path in _python_files():
        found = _timing_shape_violations(path.read_text())
        if found:
            violations[str(path.relative_to(REPO_ROOT))] = found

    assert not violations, violations


def test_timing_guard_targets_symbols_not_local_decisions() -> None:
    assert _timing_shape_violations("def run_prepaid_cycle():\n    pass\n") == {
        "run_prepaid_cycle"
    }
    assert not _timing_shape_violations(
        "def generate():\n    prepaid = timing == 'advance'\n    return prepaid\n"
    )


def test_models_contain_no_billing_or_product_consequence_columns() -> None:
    assert not _model_column_violations()
    from dotmac_subscriptions import models

    occurrence_columns = set(models.RecurringChargeOccurrence.__table__.columns.keys())
    assert {"pre_tax_amount", "period_start", "rating_proration_factor"} <= (
        occurrence_columns
    )


def test_financial_column_guard_is_bidirectionally_sensitive() -> None:
    original = _FORBIDDEN_MODEL_COLUMNS.copy()
    assert "tax_amount" in original
    assert "pre_tax_amount" not in original


def test_package_has_no_float_literal() -> None:
    files = _python_files() + tuple(
        path
        for path in (REPO_ROOT / "tests").rglob("test_subscriptions*.py")
        if path.name != Path(__file__).name
    )
    assert not {
        str(path.relative_to(REPO_ROOT)): _float_literals(path.read_text())
        for path in files
        if _float_literals(path.read_text())
    }


def test_float_literal_guard_is_sensitive() -> None:
    assert _float_literals("value = 1.5") == [1.5]
    assert not _float_literals("value = Decimal('1.5')")


def test_package_has_no_provider_product_or_currency_literal_default() -> None:
    assert not {
        str(path.relative_to(REPO_ROOT)): _forbidden_defaults(path.read_text())
        for path in _python_files()
        if _forbidden_defaults(path.read_text())
    }


def test_literal_default_guard_is_sensitive() -> None:
    assert _forbidden_defaults("currency = mapped_column(default='NGN')") == {"NGN"}
    assert not _forbidden_defaults("currency: str")


def test_public_surface_has_no_nullable_or_polymorphic_scope() -> None:
    assert not {
        str(path.relative_to(REPO_ROOT)): _scope_shape_violations(path.read_text())
        for path in _python_files()
        if _scope_shape_violations(path.read_text())
    }


def test_scope_shape_guard_is_sensitive() -> None:
    assert _scope_shape_violations("def load(tenant_id: UUID | None = None): pass") == {
        "nullable_tenant_id"
    }
    assert _scope_shape_violations("scope_kind = 'tenant'") == {"scope_kind"}


def test_commercial_inputs_have_no_implicit_defaults() -> None:
    from dotmac_subscriptions import (
        BillingCadence,
        ContractLineInput,
        ExactAmount,
        PublishOfferVersionCommand,
        RecordSubscriptionContractVersionCommand,
    )

    cadence_defaults = {
        field.name
        for field in dataclasses.fields(BillingCadence)
        if field.default is not dataclasses.MISSING
    }
    assert cadence_defaults == {"anchor_day"}
    for value_type, names in (
        (ExactAmount, {"amount", "currency", "scale"}),
        (ContractLineInput, {"product_link_ref", "unit_price", "quantity"}),
        (
            PublishOfferVersionCommand,
            {"charge_model_code", "pricing_mode", "prices"},
        ),
        (
            RecordSubscriptionContractVersionCommand,
            {"currency", "cadence", "source_code"},
        ),
    ):
        signature = inspect.signature(value_type)
        assert all(
            signature.parameters[name].default is inspect.Parameter.empty
            for name in names
        )


def test_occurrence_state_is_exactly_the_subscription_lifecycle() -> None:
    from dotmac_subscriptions.lifecycle import OccurrenceState

    assert {member.value for member in OccurrenceState} == {
        "scheduled",
        "due",
        "emitted",
        "cancelled",
    }


def test_services_never_commit_rollback_or_raise_transport_errors() -> None:
    violations: dict[str, list[str]] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text())
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"commit", "rollback"}:
                    found.append(node.func.attr)
            if isinstance(node, ast.Import | ast.ImportFrom):
                if "fastapi" in ast.unparse(node):
                    found.append("fastapi")
        if found:
            violations[str(path.relative_to(REPO_ROOT))] = found

    assert not violations, violations


def test_package_has_no_calendar_day_count_shortcuts() -> None:
    violations: list[str] = []
    pattern = re.compile(r"timedelta\s*\(\s*days\s*=\s*(?:28|30|90|365)\s*\)")
    for path in _python_files():
        if pattern.search(path.read_text()):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, violations


def test_migration_declares_both_isolation_contracts_and_immutability() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "su_0001_subscriptions"' in source
    assert 'branch_labels = ("subscriptions",)' in source
    assert "resolve_depends_on" in source
    assert "require_prerequisites" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "REVOKE ALL PRIVILEGES" in source
    assert "platform_api" in source
    assert "CREATE TRIGGER" in source
    for table in TENANT_TABLES:
        assert table in source
    for table in PLATFORM_TABLES:
        assert table in source


def test_dossier_source_paths_still_exist_at_pinned_revisions() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    assert dossier["source_repositories"] == [
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_vendor_control_plane",
    ]
    assert dossier["source_revisions"] == [
        "dotmac_erp:0f4b1698ddbf",
        "dotmac_sub:943bc59f8e4ca0849c7de578bc9dbc17c57b116f",
        "dotmac_vendor_control_plane:0c77e85c7f54538e69061614c8de42ad0f6d2332",
    ]
    assert len(dossier["source_paths"]) >= 14
    assert len(dossier["preserved_tests"]) >= 14
