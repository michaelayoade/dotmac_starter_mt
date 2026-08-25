"""Structural canaries for the reusable dual-plane subscriptions module."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import tomllib
from pathlib import Path

import dotmac_subscriptions
import pytest
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
ROOT_MIGRATION = SOURCE_ROOT / "migrations/versions/su_0001_subscriptions.py"
PRICING_MIGRATION = SOURCE_ROOT / "migrations/versions/su_0002_offer_pricing.py"
TREATMENT_MIGRATION = SOURCE_ROOT / "migrations/versions/su_0003_billing_treatments.py"

_TREATMENT_TENANT_TABLES = {
    "subscription_billing_arrangements",
    "subscription_billing_grants",
}
_TREATMENT_PLATFORM_TABLES = {
    "platform_subscription_billing_arrangements",
    "platform_subscription_billing_grants",
}
_ROOT_TENANT_TABLES = set(TENANT_TABLES) - _TREATMENT_TENANT_TABLES
_ROOT_PLATFORM_TABLES = set(PLATFORM_TABLES) - _TREATMENT_PLATFORM_TABLES

_ROOT_IMMUTABLE_TABLES = {
    "offer_versions",
    "offer_version_prices",
    "subscription_contract_versions",
    "subscription_contract_lines",
    "recurring_charge_occurrences",
    "platform_offer_versions",
    "platform_offer_version_prices",
    "platform_subscription_contract_versions",
    "platform_subscription_contract_lines",
    "platform_recurring_charge_occurrences",
}
_TREATMENT_IMMUTABLE_TABLES = {
    "subscription_billing_arrangements",
    "subscription_billing_grants",
    "platform_subscription_billing_arrangements",
    "platform_subscription_billing_grants",
}

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


def _created_tables(source: str) -> set[str]:
    tables: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            tables.add(node.args[0].value)
    return tables


def _migration_contract_violations(
    source: str,
    *,
    tenant_tables: set[str],
    platform_tables: set[str],
    immutable_tables: set[str],
) -> set[str]:
    """Check each table against the revision that creates it.

    Reading a concatenated lineage would let a later migration satisfy a token
    that the creating migration omitted. Hard rules 11 and 27 require the
    isolation contract in the SAME migration, so this helper receives one
    revision at a time and also asserts its exact table-creation coverage.
    """
    violations: set[str] = set()
    expected_tables = tenant_tables | platform_tables
    created_tables = _created_tables(source)
    if created_tables != expected_tables:
        violations.add(
            "created_tables:"
            f"expected={sorted(expected_tables)}:actual={sorted(created_tables)}"
        )

    for table in tenant_tables:
        qualified = f"mod_subscriptions.{table}"
        required = {
            f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY": "enable_rls",
            f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY": "force_rls",
            f"CREATE POLICY {table}_tenant_isolation ON {qualified}": "policy",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO app_user": (
                "tenant_dml"
            ),
        }
        for token, contract in required.items():
            if token not in source:
                violations.add(f"{table}:{contract}")

    for table in platform_tables:
        qualified = f"mod_subscriptions.{table}"
        required = {
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO platform_api": (
                "platform_dml"
            ),
            f"REVOKE ALL PRIVILEGES ON {qualified} FROM app_user": "tenant_revoke",
        }
        for token, contract in required.items():
            if token not in source:
                violations.add(f"{table}:{contract}")
        if f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in source:
            violations.add(f"{table}:platform_rls")

    for table in immutable_tables:
        token = f"BEFORE UPDATE OR DELETE ON mod_subscriptions.{table}"
        if token not in source:
            violations.add(f"{table}:immutability")

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
    assert pyproject["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a94"
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
    assert len(TENANT_TABLES) == 9
    assert len(PLATFORM_TABLES) == 9
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


def test_each_migration_declares_its_own_isolation_and_immutability() -> None:
    root = ROOT_MIGRATION.read_text()
    treatment = TREATMENT_MIGRATION.read_text()

    assert 'revision = "su_0001_subscriptions"' in root
    assert 'branch_labels = ("subscriptions",)' in root
    assert "resolve_depends_on" in root
    assert "require_prerequisites" in root
    assert not _migration_contract_violations(
        root,
        tenant_tables=_ROOT_TENANT_TABLES,
        platform_tables=_ROOT_PLATFORM_TABLES,
        immutable_tables=_ROOT_IMMUTABLE_TABLES,
    )

    assert 'revision = "su_0003_billing_treatments"' in treatment
    assert 'down_revision = "su_0002_offer_pricing"' in treatment
    assert not _migration_contract_violations(
        treatment,
        tenant_tables=_TREATMENT_TENANT_TABLES,
        platform_tables=_TREATMENT_PLATFORM_TABLES,
        immutable_tables=_TREATMENT_IMMUTABLE_TABLES,
    )
    # su_0003 also protects the already-released contract-version tables from
    # being superseded around an open arrangement. That guard belongs to the
    # treatment revision that introduced the premise, not to su_0001.
    assert "contract_versions_treatment_term_freeze" in treatment
    assert "platform_contract_versions_treatment_term_freeze" in treatment


def test_per_revision_migration_guard_is_sensitive() -> None:
    planted = """
from alembic import op

def upgrade():
    op.create_table("tenant_facts")
    op.create_table("platform_facts")
"""

    violations = _migration_contract_violations(
        planted,
        tenant_tables={"tenant_facts"},
        platform_tables={"platform_facts"},
        immutable_tables={"tenant_facts", "platform_facts"},
    )

    assert violations == {
        "tenant_facts:enable_rls",
        "tenant_facts:force_rls",
        "tenant_facts:policy",
        "tenant_facts:tenant_dml",
        "tenant_facts:immutability",
        "platform_facts:platform_dml",
        "platform_facts:tenant_revoke",
        "platform_facts:immutability",
    }


def test_offer_pricing_evolves_in_an_additive_composable_revision() -> None:
    source = PRICING_MIGRATION.read_text()

    assert 'revision = "su_0002_offer_pricing"' in source
    assert 'down_revision = "su_0001_subscriptions"' in source
    assert "selected_module_planes" in source
    assert "ModulePlane.TENANT" in source
    assert "ModulePlane.PLATFORM" in source
    assert "sa.func.count(sa.distinct(price_table.c.charge_model_code)) != 1" in source
    assert "contains a non-positive price" in source
    assert "contains a non-positive contract price" in source
    assert 'pricing_mode="catalog_price"' in source
    assert 'version_table.c.pricing_mode == "contract_price"' in source


def test_offer_pricing_revision_loads_through_alembics_module_loader() -> None:
    from alembic.util.pyfiles import load_python_file

    migration = load_python_file(
        str(PRICING_MIGRATION.parent),
        PRICING_MIGRATION.name,
    )

    assert migration.revision == "su_0002_offer_pricing"
    assert migration.down_revision == "su_0001_subscriptions"


# ── G2/G3: complimentary and sponsored treatment ─────────────────────────────


def test_the_registry_still_constructs_the_way_0_1_0a2_published_it() -> None:
    """`a3` added a field to a RELEASED dataclass; it must not become required.

    `0.1.0a2` published `SubscriptionVocabularyRegistry` with exactly two
    fields, so `SubscriptionVocabularyRegistry(charge_models,
    obligation_sources)` is a construction that exists in consumers today. A
    third REQUIRED field breaks every one of them on upgrade — a breaking
    change, which a pre-release series does not make silently. Defaulted, the
    registry simply declares no treatment reasons, and asking it for one is
    refused rather than answered wrongly.
    """
    from dotmac_subscriptions import (
        SubscriptionDataError,
        SubscriptionVocabularyRegistry,
    )

    released = SubscriptionVocabularyRegistry(
        {"recurring_access": "product"}, {"accepted_order_line": "product"}
    )

    assert released.billing_treatment_reasons == {}
    assert released.require_charge_model("recurring_access") == "product"
    with pytest.raises(SubscriptionDataError):
        released.require_billing_treatment_reason("internal_service")
    assert SubscriptionVocabularyRegistry.from_manifests(()).billing_treatment_reasons


def test_the_reason_vocabulary_is_a_declared_registry_and_never_an_enum() -> None:
    """ADR-0008 is fleet-wide: a module-owned vocabulary is a registry.

    An enum would put the seven reasons in this package's source, so a product
    needing an eighth would need a module release; a CHECK constraint would do
    the same in the database, costing a migration per consuming product.
    """
    from dotmac_subscriptions import PORTED_BILLING_TREATMENT_REASONS
    from dotmac_subscriptions.models import (
        SubscriptionBillingArrangement,
        SubscriptionBillingGrant,
    )

    assert len(PORTED_BILLING_TREATMENT_REASONS) == 7
    for source in (SOURCE_ROOT / "lifecycle.py", SOURCE_ROOT / "vocabulary.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ClassDef):
                members = {
                    child.targets[0].id
                    for child in node.body
                    if isinstance(child, ast.Assign)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                }
                assert not members & set(PORTED_BILLING_TREATMENT_REASONS), node.name
    for model in (SubscriptionBillingArrangement, SubscriptionBillingGrant):
        column = model.__table__.columns["reason_code"]
        assert column.type.python_type is str
    checks = {
        constraint.name
        for constraint in SubscriptionBillingArrangement.__table__.constraints
        if constraint.name is not None
    }
    assert not [name for name in checks if "reason" in name]


def test_reason_registry_guard_is_sensitive_to_a_reclosed_vocabulary() -> None:
    from dotmac_subscriptions import PORTED_BILLING_TREATMENT_REASONS

    closed = (
        "from enum import Enum\n"
        "class Reason(Enum):\n"
        "    internal_service = 'internal_service'\n"
    )
    members = {
        child.targets[0].id
        for node in ast.walk(ast.parse(closed))
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, ast.Assign)
        and len(child.targets) == 1
        and isinstance(child.targets[0], ast.Name)
    }
    assert members & set(PORTED_BILLING_TREATMENT_REASONS)


def test_a_treatment_is_never_a_zero_price_anywhere_in_the_schema() -> None:
    """G3, read straight off the tables.

    The contract line and the offer price both stay strictly positive, and the
    grant relates the contracted amount, the approved ceiling and the foregone
    amount in one constraint — so no combination of rows can express "free"
    without also recording exactly how much was given away.
    """
    from dotmac_subscriptions.models import (
        OfferVersionPrice,
        PlatformSubscriptionBillingGrant,
        SubscriptionBillingGrant,
        SubscriptionContractLine,
    )

    def _sql(model: object, name: str) -> str:
        for constraint in model.__table__.constraints:  # type: ignore[attr-defined]
            if constraint.name == name:
                return str(constraint.sqltext)
        raise AssertionError(f"{name} is missing")

    assert "unit_price > 0" in _sql(
        SubscriptionContractLine, "ck_contract_lines_amounts"
    )
    assert "amount > 0" in _sql(OfferVersionPrice, "ck_offer_version_prices_amounts")
    for model, name in (
        (SubscriptionBillingGrant, "ck_billing_grants_bounded_non_cash_value"),
        (
            PlatformSubscriptionBillingGrant,
            "ck_platform_billing_grants_bounded_non_cash_value",
        ),
    ):
        sql = _sql(model, name)
        assert "contracted_amount > 0" in sql
        assert "foregone_amount > 0" in sql
        assert "foregone_amount <= contracted_amount" in sql
        assert "foregone_amount <= approved_maximum_amount" in sql


def test_the_grant_tables_carry_no_customer_money_column() -> None:
    """A grant is non-cash: there is nowhere on it to record a receivable."""
    from dotmac_subscriptions.models import (
        PlatformSubscriptionBillingGrant,
        SubscriptionBillingGrant,
    )

    forbidden = {
        "invoice_id",
        "payment_id",
        "receivable_id",
        "amount_funded",
        "amount_paid",
        "balance",
        "outstanding",
        "settlement_id",
        "credit_note_id",
    }
    for model in (SubscriptionBillingGrant, PlatformSubscriptionBillingGrant):
        assert not set(model.__table__.columns.keys()) & forbidden


def test_the_mandatory_end_date_is_not_nullable_on_either_plane() -> None:
    from dotmac_subscriptions.models import (
        PlatformSubscriptionBillingArrangement,
        SubscriptionBillingArrangement,
    )

    for model in (
        SubscriptionBillingArrangement,
        PlatformSubscriptionBillingArrangement,
    ):
        assert model.__table__.columns["ends_at"].nullable is False
        assert model.__table__.columns["starts_at"].nullable is False


def test_treatment_evolves_in_an_additive_dual_plane_revision() -> None:
    source = TREATMENT_MIGRATION.read_text()

    assert 'revision = "su_0003_billing_treatments"' in source
    assert 'down_revision = "su_0002_offer_pricing"' in source
    assert "selected_module_planes" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "REVOKE ALL PRIVILEGES" in source
    assert "platform_api" in source
    assert "foregone_amount <= approved_maximum_amount" in source
    assert "refuse_immutable_row" in source
    assert "protect_tenant_treatment_terms" in source
    assert "protect_platform_treatment_terms" in source
    for table in (
        "subscription_billing_arrangements",
        "subscription_billing_grants",
        "platform_subscription_billing_arrangements",
        "platform_subscription_billing_grants",
    ):
        assert table in source


def test_the_released_a2_revisions_are_untouched_by_this_slice() -> None:
    """`su_0001` and `su_0002` both shipped in 0.1.0a2; their bytes are history.

    `tests/architecture/test_released_migrations.py` holds the digests; this
    repeats the claim locally so a reader of THIS file sees why the new
    behaviour arrived as `su_0003` rather than as an edit.
    """
    assert 'down_revision = "su_0001_subscriptions"' in PRICING_MIGRATION.read_text()
    assert "billing_arrangement" not in ROOT_MIGRATION.read_text()
    assert "billing_arrangement" not in PRICING_MIGRATION.read_text()


def test_treatment_revision_loads_through_alembics_module_loader() -> None:
    from alembic.util.pyfiles import load_python_file

    migration = load_python_file(
        str(TREATMENT_MIGRATION.parent),
        TREATMENT_MIGRATION.name,
    )

    assert migration.revision == "su_0003_billing_treatments"
    assert migration.down_revision == "su_0002_offer_pricing"


def test_the_treatment_owner_names_no_billing_entitlement_or_accounting_symbol() -> (
    None
):
    """Sub's consequences stay with their owners (extraction dossier, G4).

    The module publishes `NonCashGrantOutputV1` and stops: it never creates an
    entitlement, moves a billing anchor, suppresses an invoice, or posts a
    sponsor receivable. Checked over IDENTIFIERS rather than raw text, so the
    docstring that explains the boundary does not trip the guard describing it.
    """
    source = (SOURCE_ROOT / "treatments.py").read_text()
    tree = ast.parse(source)
    identifiers = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
    } | {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    forbidden = {"entitlement", "invoice", "receivable", "posting", "anchor", "payment"}

    assert not _import_roots(source) & (_SIBLINGS | _PRODUCTS)
    assert not [
        name for name in identifiers if any(word in name.lower() for word in forbidden)
    ]


def test_the_consequence_guard_is_sensitive_to_a_leaked_owner() -> None:
    leaked = ast.parse("def create_service_entitlement():\n    pass\n")
    names = {
        node.name for node in ast.walk(leaked) if isinstance(node, ast.FunctionDef)
    }

    assert [name for name in names if "entitlement" in name.lower()]


def test_dossier_source_paths_still_exist_at_pinned_revisions() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    assert dossier["source_repositories"] == [
        "dotmac_sub",
        "dotmac_erp",
        "dotmac_vendor_control_plane",
    ]
    assert dossier["source_revisions"] == [
        "dotmac_sub:27c76aaeebb792f089000af764d80f4dfe45c104",
        "dotmac_erp:0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf",
        "dotmac_vendor_control_plane:89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8",
    ]
    assert len(dossier["source_paths"]) >= 11
    assert len(dossier["preserved_tests"]) >= 14
