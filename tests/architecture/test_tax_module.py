"""Static contract for the tenant-only tax owner."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import get_type_hints

import dotmac_tax
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_tax import contracts, models
from dotmac_tax.contracts import (
    TaxDeterminationComponentV1,
    TaxDeterminationLineV1,
    TaxDeterminationSetV1,
)
from dotmac_tax.manifest import module
from dotmac_tax.service import determine_tax_set

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-tax"
SOURCE = PACKAGE / "src/dotmac_tax"
MIGRATIONS = tuple(sorted((SOURCE / "migrations/versions").glob("tx_*.py")))


def test_manifest_allocates_one_tenant_only_tax_lineage() -> None:
    assert module.code == "tax"
    assert module.version == "0.1.0a3"
    assert module.short_code == "tax"
    assert module.migration_prefix == "tx"
    assert module.migration_branch == "tax"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["version"]
    assert declared == dotmac_tax.__version__ == module.version


def test_tax_publishes_an_orm_free_exact_money_determination_contract() -> None:
    assert get_type_hints(determine_tax_set)["return"] is TaxDeterminationSetV1
    published_contracts = {
        "TaxDeterminationComponentV1",
        "TaxDeterminationLineV1",
        "TaxDeterminationSetV1",
    }
    assert published_contracts <= set(contracts.__all__)
    assert published_contracts <= set(dotmac_tax.__all__)
    assert set(TaxDeterminationSetV1.__dataclass_fields__) == {
        "tenant_id",
        "determination_set_id",
        "jurisdiction_id",
        "occurred_on",
        "fact_kind",
        "recognition_basis_code",
        "transaction_side",
        "source_amount",
        "net_amount",
        "tax_amount",
        "gross_amount",
        "source_ref",
        "source_version",
        "source_fingerprint",
        "result_fingerprint",
        "evidence_ref",
        "counterparty_ref",
        "supply_ref",
        "place_ref",
        "determined_at",
        "components",
    }
    assert set(TaxDeterminationComponentV1.__dataclass_fields__) == {
        "determination_id",
        "determination_set_id",
        "component_sequence",
        "tax_code_id",
        "rule_id",
        "rule_version",
        "treatment_code",
        "calculation_base_code",
        "inclusive",
        "party_category",
        "supply_category",
        "place_code",
        "party_classification_id",
        "supply_classification_id",
        "place_classification_id",
        "base_amount",
        "tax_amount",
        "recoverable_amount",
        "non_recoverable_amount",
        "lines",
    }
    assert get_type_hints(TaxDeterminationSetV1)["source_amount"].__name__ == "Money"
    assert get_type_hints(TaxDeterminationComponentV1)["tax_amount"].__name__ == (
        "Money"
    )
    assert get_type_hints(TaxDeterminationLineV1)["taxable_amount"].__name__ == (
        "Money"
    )

    contracts_tree = ast.parse(
        (SOURCE / "contracts.py").read_text(encoding="utf-8"),
        filename=str(SOURCE / "contracts.py"),
    )
    imported_roots: set[str] = set()
    for node in ast.walk(contracts_tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint({"sqlalchemy", "dotmac_tax"})


def test_tax_python_range_is_compatible_with_kernel_and_erp_first_adopter() -> None:
    tax_metadata = tomllib.loads(
        (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    )
    kernel_metadata = tomllib.loads(
        (ROOT / "packages/dotmac-kernel/pyproject.toml").read_text(encoding="utf-8")
    )

    assert (
        tax_metadata["tool"]["poetry"]["dependencies"]["python"]
        == (kernel_metadata["tool"]["poetry"]["dependencies"]["python"])
    )


def test_every_tax_table_has_direct_tenant_identity() -> None:
    for model in models.TENANT_MODELS:
        columns = model.__table__.columns
        assert "tenant_id" in columns, model.__name__
        assert columns["tenant_id"].nullable is False, model.__name__
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, model.__name__


def test_tax_members_rates_calendars_and_boxes_are_data_not_code() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py")
    )
    for forbidden in (
        "class TaxType",
        "VAT =",
        "WITHHOLDING =",
        "PAYE =",
        "seed_nigeria",
        "7.5%",
        "0.075",
        "21st day",
        "FIRS",
        "NRS",
        "LIRS",
    ):
        assert forbidden not in source
    assert "tax_kind_code" in models.TaxCode.__table__.c
    assert "due_on" in models.TaxFilingObligation.__table__.c
    assert "box_code" in models.StatutoryReportBox.__table__.c


def test_tax_set_and_classification_models_keep_custom_taxes_composable() -> None:
    assert "tax_code_id" in models.TaxSubjectClassification.__table__.c
    assert "subject_kind" in models.TaxSubjectClassification.__table__.c
    assert "treatment_code" in models.TaxRule.__table__.c
    assert "calculation_sequence" in models.TaxRule.__table__.c
    assert "calculation_base_code" in models.TaxRule.__table__.c
    assert "source_amount" in models.TaxDeterminationSet.__table__.c
    assert "gross_amount" in models.TaxDeterminationSet.__table__.c
    assert "result_seal_state" in models.TaxDeterminationSet.__table__.c
    assert "result_fingerprint" in models.TaxDeterminationSet.__table__.c
    assert "determination_set_id" in models.TaxDetermination.__table__.c


def test_tax_imports_no_product_or_sibling_domain_and_no_gl_foreign_keys() -> None:
    forbidden_roots = {
        "app",
        "dotmac_accounting",
        "dotmac_banking",
        "dotmac_billing",
        "dotmac_payroll",
    }
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert (
                    not {alias.name.split(".", 1)[0] for alias in node.names}
                    & forbidden_roots
                ), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots, path
    for model in models.TENANT_MODELS:
        assert "journal_entry_id" not in model.__table__.c
        assert "fiscal_period_id" not in model.__table__.c


def test_tax_services_are_flush_only() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    for forbidden in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden not in source


def test_tax_migration_forces_rls_and_protects_filing_evidence() -> None:
    assert [path.name for path in MIGRATIONS] == [
        "tx_0001_tax.py",
        "tx_0002_multi_tax.py",
        "tx_0003_result_fingerprint.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)
    compact = re.sub(r"\s+", " ", source)
    assert "protect_tax_evidence" in source
    for table in module.tables:
        assert (
            f"ALTER TABLE {module.db_schema}.{table} ENABLE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"ALTER TABLE {module.db_schema}.{table} FORCE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"CREATE POLICY {table}_tenant_isolation " f"ON {module.db_schema}.{table}"
        ) in compact
        assert f'"{table}"' in source
    for table in (
        "tax_rules",
        "tax_rule_bands",
        "tax_subject_classifications",
        "tax_determination_sets",
        "tax_determinations",
        "tax_determination_lines",
        "statutory_reports",
        "statutory_report_values",
        "tax_return_events",
    ):
        assert (
            "CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON "
            f"{module.db_schema}.{table}"
        ) in compact


def test_tax_result_seal_is_additive_and_does_not_invent_an_a2_backfill() -> None:
    migration = SOURCE / "migrations/versions/tx_0003_result_fingerprint.py"
    source = migration.read_text(encoding="utf-8")
    assert 'down_revision = "tx_0002_multi_tax"' in source
    assert "result_fingerprint" in source
    assert "result_seal_state" in source
    assert "NOT VALID" in source
    assert "preserves those NULL/NULL rows" in source
    assert "UPDATE mod_tax.tax_determination_sets" not in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "require_building_tax_result_parent" in source


def test_every_tax_determination_reader_crosses_the_sealed_projector() -> None:
    """Inventory the evidence readers so a new report cannot bypass rv1."""

    evidence_models = {
        "TaxDetermination",
        "TaxDeterminationLine",
        "TaxDeterminationSet",
    }
    importers: dict[Path, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        source_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(source_tree)
            if isinstance(node, ast.ImportFrom) and node.module == "dotmac_tax.models"
            for alias in node.names
            if alias.name in evidence_models
        }
        if imported:
            importers[path.relative_to(SOURCE)] = imported
    assert importers == {Path("service.py"): evidence_models}

    tree = ast.parse(
        (SOURCE / "service.py").read_text(encoding="utf-8"),
        filename=str(SOURCE / "service.py"),
    )
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    def users(model_name: str) -> set[str]:
        return {
            name
            for name, node in functions.items()
            if any(
                isinstance(child, ast.Name) and child.id == model_name
                for child in ast.walk(node)
            )
        }

    assert users("TaxDetermination") == {
        "_determine_tax_set_row",
        "_verified_determination_contracts_for_period",
        "determine_tax",
    }
    assert users("TaxDeterminationLine") == {"_determine_tax_set_row"}
    assert users("TaxDeterminationSet") == {
        "_determination_set_contract",
        "_determine_tax_set_row",
        "_result_content_fingerprint",
        "_sealed_result_fingerprint",
        "_validate_persisted_result_structure",
        "_verified_determination_contracts_for_period",
        "determine_tax",
    }
    report = functions["generate_statutory_report"]
    report_names = {
        child.id for child in ast.walk(report) if isinstance(child, ast.Name)
    }
    assert {
        *evidence_models,
    }.isdisjoint(report_names)
    assert "_verified_determination_contracts_for_period" in report_names
    verified_reader_names = {
        child.id
        for child in ast.walk(functions["_verified_determination_contracts_for_period"])
        if isinstance(child, ast.Name)
    }
    assert "_determination_set_contract" in verified_reader_names
