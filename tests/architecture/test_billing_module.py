"""Architecture canaries for the one operational-receivables owner.

These are deliberately derived from the Billing manifest and public surface.
A new table or public contract is therefore inside the guard automatically;
there is no hand-maintained exemption list that can silently miss it.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import tomllib
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import dotmac_billing
import pytest
from dotmac_billing import authority, contracts, linking, models
from dotmac_billing.commands import CreateDraftDocument
from dotmac_billing.manifest import module
from dotmac_kernel.migrations.gate import run_gate
from dotmac_kernel.namespaces import BILLING_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-billing"
MODULE_ROOT = PACKAGE_ROOT / "src/dotmac_billing"
MIGRATION = MODULE_ROOT / "migrations/versions/bi_0001_billing.py"
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)


def _source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MODULE_ROOT.rglob("*.py"))
    )


def test_namespace_lineage_and_release_identity_are_allocated_together() -> None:
    assert BILLING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == BILLING_MIGRATION_OWNER.owner == "billing"
    assert module.short_code == "billing"
    assert module.migration_prefix == BILLING_MIGRATION_OWNER.prefix == "bi"
    assert module.migration_branch == BILLING_MIGRATION_OWNER.branch_label == "billing"
    assert models.SCHEMA == BILLING_MIGRATION_OWNER.db_schema == "mod_billing"
    assert MIGRATION.is_file()


def test_the_dossier_pins_timer_evidence_without_importing_a_timer_owner() -> None:
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    assert dossier["revalidation_revisions"] == [
        "dotmac_starter_mt:c6b403faa5136e06f8219bbbb7cf5e4ddfed724f",
        "dotmac_sub:a9da920926a9d9212a8cf03a4744b48a1d4e14f2",
        "dotmac_erp:2749ec5396cbbd7a1132b394e85855a1d133a7cd",
        "dotmac_vendor_control_plane:f8f8c3fd636e663e4a17275c19e82fc1667aa52a",
        "dotmac_integrator:35167813c83ab0ec29c683259ad31479503d812f",
    ]
    assert dossier["dependency_evidence_revisions"] == [
        "dotmac_starter_mt/agent/dotmac-durable-timers:7e0543004864845f0035c9ec325e3f5064c281cc",
        "dotmac_sub/agent/durable-timers-adoption:4489ca1712f3c263d914f2af0ebfcf044aa70605",
        "dotmac-kernel:0.1.0a67:outbox_relay.v1",
    ]
    disposition = dossier["durable_timer_disposition"]
    assert "not a dotmac-billing runtime or package dependency" in disposition
    package = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = package["tool"]["poetry"]["dependencies"]
    assert not any("timer" in name.lower() for name in dependencies)


def test_both_planes_are_declared_disjoint_and_complete() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == models.PLATFORM_TABLES
    assert module.tables and module.platform_tables
    assert not set(module.tables) & set(module.platform_tables)
    assert module.supported_plane_sets

    mapped = {
        table.name
        for table in models.Base.metadata.tables.values()
        if table.schema == models.SCHEMA
    }
    assert mapped == set(module.tables) | set(module.platform_tables)


def test_the_billing_lineage_passes_the_composed_gate() -> None:
    report = run_gate(
        [module],
        [KERNEL_VERSIONS, MIGRATION.parent],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
        module_planes=(
            ModulePlaneSelection(
                module="billing",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
        ),
    )
    assert report.ok, report.violations


def test_tenant_and_platform_models_have_opposite_scope_shapes() -> None:
    for table_name in module.tables:
        table = models.Base.metadata.tables[f"{models.SCHEMA}.{table_name}"]
        assert table.c.tenant_id.nullable is False
        identities = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if hasattr(constraint, "columns")
        }
        assert any("tenant_id" in columns for columns in identities)

    for table_name in module.platform_tables:
        table = models.Base.metadata.tables[f"{models.SCHEMA}.{table_name}"]
        assert "tenant_id" not in table.c


def test_no_foreign_key_crosses_a_plane() -> None:
    tenant = set(module.tables)
    platform = set(module.platform_tables)
    for table in models.Base.metadata.tables.values():
        if table.schema != models.SCHEMA:
            continue
        source_plane = tenant if table.name in tenant else platform
        other_plane = platform if source_plane is tenant else tenant
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name not in other_plane


def test_product_link_helpers_name_the_plane_and_target_the_allocation() -> None:
    assert linking.MODULE_SCHEMA == BILLING_MIGRATION_OWNER.db_schema
    assert not hasattr(linking, "link_billing_account")
    for helper in (
        linking.link_tenant_billing_account,
        linking.link_platform_billing_account,
    ):
        parameters = inspect.signature(helper).parameters
        assert "plane" not in parameters
        assert "platform" not in parameters
        subject_delete = parameters["on_delete_subject"]
        assert subject_delete.kind is inspect.Parameter.KEYWORD_ONLY
        assert subject_delete.default is inspect.Parameter.empty


def test_platform_link_refuses_an_unreachable_table() -> None:
    with pytest.raises(ValueError, match="at least one online role"):
        linking.link_platform_billing_account(
            table_name="vendor_billing_account",
            subject_table="vendor_accounts",
            on_delete_subject="RESTRICT",
            platform_roles=(),
        )


def test_link_helpers_refuse_generated_identifiers_postgres_would_truncate() -> None:
    for helper in (
        linking.link_tenant_billing_account,
        linking.link_platform_billing_account,
    ):
        with pytest.raises(ValueError, match="1..63"):
            helper(
                table_name="a" * 55,
                subject_table="subjects",
                on_delete_subject="RESTRICT",
            )


def test_link_helpers_emit_opposite_plane_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def record(helper) -> tuple[tuple[str, ...], str]:
        columns: list[str] = []
        statements: list[str] = []
        monkeypatch.setattr(
            linking.op,
            "create_table",
            lambda _name, *args, **_kwargs: columns.extend(
                item.name for item in args if hasattr(item, "name")
            ),
        )
        monkeypatch.setattr(linking.op, "create_index", lambda *a, **k: None)
        monkeypatch.setattr(linking.op, "execute", statements.append)
        helper(
            table_name="product_billing_account",
            subject_table="subjects",
            on_delete_subject="RESTRICT",
        )
        return tuple(columns), " ".join(statements)

    tenant_columns, tenant_sql = record(linking.link_tenant_billing_account)
    assert "tenant_id" in tenant_columns
    assert "FORCE ROW LEVEL SECURITY" in tenant_sql
    assert "app_current_tenant_id()" in tenant_sql
    assert "REVOKE" not in tenant_sql

    platform_columns, platform_sql = record(linking.link_platform_billing_account)
    assert "tenant_id" not in platform_columns
    assert "ROW LEVEL SECURITY" not in platform_sql
    assert (
        "REVOKE ALL PRIVILEGES ON public.product_billing_account FROM app_user"
        in platform_sql
    )
    for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
        assert f"REVOKE {privilege} (" in platform_sql


def test_public_contracts_freeze_only_the_agreed_v1_shapes() -> None:
    assert contracts.OBLIGATION_OUTPUT_CONTRACT == "RatedObligationOutputV1"
    assert contracts.SETTLEMENT_CONTRACT == "billing.settlement.accept.v1"
    assert contracts.DOCUMENT_FACT_CONTRACT == "billing.invoice.document.fact.v1"
    assert contracts.RECEIVABLE_POSITION_CONTRACT == "billing.receivable.position.v1"

    public = set(dotmac_billing.__all__)
    assert "AllocationV1" not in public
    assert "CoverageV1" not in public
    assert "RecurringObligationDueV1" not in public
    assert "template_profile_code" not in _source()

    position_fields = {
        field.name for field in dataclasses.fields(contracts.ReceivablePositionV1)
    }
    money_fields = {
        "collectible_receivable",
        "available_credit",
        "prepaid_funding",
    }
    assert money_fields <= position_fields
    assert not position_fields & {"balance", "current_balance", "funding_available"}

    artifact_fields = {
        field.name for field in dataclasses.fields(contracts.RecordDocumentArtifactV1)
    }
    assert artifact_fields == {
        "scope",
        "fact_id",
        "invoice_id",
        "fact_version",
        "media_type",
        "file_id",
        "checksum_sha256",
        "byte_length",
        "renderer_code",
        "renderer_version",
        "template_version",
        "presentation_model_digest",
        "rendered_at",
        "correlation_id",
        "issued_by",
        "supersedes_artifact_id",
        "supersession_reason",
    }
    assert not artifact_fields & {
        "amount",
        "balance",
        "coverage",
        "lifecycle",
    }


def test_published_and_service_boundary_contracts_have_no_open_payloads() -> None:
    """A dataclass name does not make an unshaped JSON dictionary typed."""

    boundary_types = [
        value
        for name in contracts.__all__
        if dataclasses.is_dataclass(value := getattr(contracts, name, None))
    ]
    boundary_types.append(CreateDraftDocument)

    def assert_closed(annotation: object, path: str) -> None:
        origin = get_origin(annotation)
        assert annotation not in {Any, object}, f"{path} is an open payload"
        assert origin is not dict, f"{path} is an unshaped dictionary"
        for index, argument in enumerate(get_args(annotation)):
            assert_closed(argument, f"{path}[{index}]")

    for contract_type in boundary_types:
        for field_name, annotation in get_type_hints(contract_type).items():
            assert_closed(annotation, f"{contract_type.__name__}.{field_name}")

    repository_return = get_type_hints(authority.RepositoryFactory.__call__)["return"]
    assert repository_return is authority.BillingRepository
    assert get_type_hints(authority.AuthorityBinding)["plane"] is authority.BillingPlane


def test_due_date_unknown_is_reportable_but_not_collectible() -> None:
    basis = contracts.DueDateBasisV1.unknown_unverified(
        source_authority="legacy_import",
        evidence_ref="archive:invoice:1",
    )
    assert basis.status is contracts.DueDateBasisStatus.UNKNOWN_UNVERIFIED
    assert basis.automated_collection_allowed is False


def test_no_second_financial_or_transport_authority_is_imported() -> None:
    source = _source().lower()
    forbidden_import_roots = (
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_subscriptions",
        "dotmac_collections",
        "dotmac_numbering",
        "dotmac_files",
    )
    tree = ast.parse(_source())
    imports = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        imported == root or imported.startswith(f"{root}.")
        for imported in imports
        for root in forbidden_import_roots
    )
    for forbidden in (
        "sessionmaker(",
        "sessionlocal(",
        "platformsessionlocal(",
        "db.commit(",
        "db.rollback(",
        "legacy_financial_writer",
        "provider_client",
        "webhook_signature",
        "chart_of_accounts",
        "journal_entry",
        "fiscal_period",
        "treasury",
        "tax_return",
    ):
        assert forbidden not in source


def test_money_storage_is_exact_and_never_float() -> None:
    for table in models.Base.metadata.tables.values():
        if table.schema != models.SCHEMA:
            continue
        for column in table.columns:
            if column.info.get("billing_money"):
                assert str(column.type) == "NUMERIC(20, 6)"
    tree = ast.parse(_source())
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
        for node in ast.walk(tree)
    )


def test_release_metadata_and_runtime_version_agree() -> None:
    package = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    declared = package["tool"]["poetry"]["version"]
    assert dotmac_billing.__version__ == module.version == declared
    assert package["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a75"
    assert package["tool"]["poetry"]["dependencies"]["alembic"] == ">=1.13"

    release = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]["dotmac-billing"]
    assert release["kernel_floor"] == "0.1.0a75"
    assert release["db_schema"] == "mod_billing"
    assert release["import_name"] == "dotmac_billing"
    assert re.fullmatch(r"0\.1\.0a\d+", declared)
