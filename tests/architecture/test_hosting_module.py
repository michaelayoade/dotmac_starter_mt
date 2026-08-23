from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import dotmac_hosting
import pytest
from dotmac_hosting.contracts import (
    HOSTING_ACCOUNT_CAPABILITY,
    HOSTING_ACCOUNT_OPERATIONS,
    ChangeHostingPackageV1,
    ChangeHostingSuspensionV1,
    HostingAcknowledgementV1,
    HostingAccountIdentityV1,
    HostingObservationV1,
    HostingOutcomeEvidenceV1,
    HostingResourceFactV1,
    ObserveHostingAccountV1,
    ProvisionHostingAccountV1,
    ReconcileHostingAccountV1,
    TerminateHostingAccountV1,
)
from dotmac_hosting.manifest import module
from dotmac_hosting.models import SCHEMA, TABLES
from dotmac_hosting.service import PUBLIC_EVENT_TYPES
from dotmac_kernel.namespaces import HOSTING_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER
from dotmac_kernel.planes import ModulePlane, declared_planes

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-hosting/src/dotmac_hosting"
MIGRATION = PACKAGE / "migrations/versions/ho_0001_hosting.py"
FORBIDDEN_PROVIDER_NAMES = re.compile(
    r"\b(blesta|directadmin|cpanel|plesk|whmcs|ispconfig|virtualmin|cyberpanel)\b",
    re.IGNORECASE,
)
FORBIDDEN_PROVIDER_FIELDS = {
    "password",
    "credential",
    "token",
    "secret",
    "private_key",
    "auth_code",
    "api_key",
    "metadata",
    "config",
    "headers",
    "body",
    "payload",
    "details",
}
SIBLING_IMPORTS = {
    "dotmac_approvals",
    "dotmac_billing",
    "dotmac_collections",
    "dotmac_domains",
    "dotmac_fulfillment",
    "dotmac_integration",
    "dotmac_orders",
    "dotmac_subscriptions",
}


def _source_files(root: Path = PACKAGE) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _provider_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _source_files(root):
        for match in FORBIDDEN_PROVIDER_NAMES.finditer(
            path.read_text(encoding="utf-8")
        ):
            violations.append(f"{path.name}:{match.group(0)}")
    return violations


def _unknown_request_events(event_types: tuple[str, ...]) -> list[str]:
    prefix = "hosting.account."
    suffix = ".requested.v1"
    return [
        event_type
        for event_type in event_types
        if event_type.startswith(prefix)
        and event_type.endswith(suffix)
        and event_type[len(prefix) : -len(suffix)] not in HOSTING_ACCOUNT_OPERATIONS
    ]


def _unresolved_operation_refs(field_names: set[str]) -> set[str]:
    allowed = {
        "operation_reference",
        "package_ref",
        "target_package_ref",
        "reason_ref",
        "account_ref",
    }
    return {name for name in field_names if name.endswith("_ref") and name not in allowed}


def _forbidden_provider_fields(contracts: tuple[type[object], ...]) -> set[str]:
    violations: set[str] = set()
    for contract in contracts:
        for field_name in contract.__dataclass_fields__:
            normalized = field_name.lower()
            if any(
                forbidden == normalized or forbidden in normalized.split("_")
                for forbidden in FORBIDDEN_PROVIDER_FIELDS
            ):
                violations.add(f"{contract.__name__}.{field_name}")
    return violations


MUTABLE_SERVICE_FIELDS = {
    "specification_code",
    "specification_version",
    "capability_binding_ref",
    "provider_account_ref",
    "lifecycle_state",
    "state_effective_at",
    "row_version",
    "updated_at",
}


def _direct_service_mutations(source: str) -> set[str]:
    tree = ast.parse(source)
    violations: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "service"
                and target.attr in MUTABLE_SERVICE_FIELDS
            ):
                violations.add(target.attr)
    return violations


def test_manifest_is_tenant_only_and_matches_permanent_namespace() -> None:
    assert module.code == "hosting"
    assert module.short_code == "hosting"
    assert module.migration_prefix == "ho"
    assert module.migration_branch == "hosting"
    assert module.tables == TABLES
    assert module.platform_tables == ()
    assert declared_planes(module) == frozenset({ModulePlane.TENANT})
    assert SCHEMA == "mod_hosting"
    assert HOSTING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert HOSTING_MIGRATION_OWNER.db_schema == SCHEMA


def test_capability_is_one_exact_six_operation_family() -> None:
    assert HOSTING_ACCOUNT_CAPABILITY == "hosting.account.v1"
    assert HOSTING_ACCOUNT_OPERATIONS == {
        "provision",
        "package",
        "suspension",
        "termination",
        "observation",
        "reconcile",
    }


def test_provider_operation_inputs_have_no_unresolved_local_references() -> None:
    contracts = (
        ProvisionHostingAccountV1,
        ChangeHostingPackageV1,
        ChangeHostingSuspensionV1,
        TerminateHostingAccountV1,
        ObserveHostingAccountV1,
        ReconcileHostingAccountV1,
    )
    violations = {
        contract.__name__: sorted(
            _unresolved_operation_refs(set(contract.__dataclass_fields__))
        )
        for contract in contracts
        if _unresolved_operation_refs(set(contract.__dataclass_fields__))
    }
    assert violations == {}


def test_unresolved_local_reference_guard_has_a_sensitivity_proof() -> None:
    assert _unresolved_operation_refs(
        {"operation_reference", "package_ref", "owner_contact_ref"}
    ) == {"owner_contact_ref"}


def test_provider_contracts_cannot_carry_secrets_or_open_transport_blobs() -> None:
    contracts: tuple[type[object], ...] = (
        HostingAccountIdentityV1,
        ProvisionHostingAccountV1,
        ChangeHostingPackageV1,
        ChangeHostingSuspensionV1,
        TerminateHostingAccountV1,
        ObserveHostingAccountV1,
        ReconcileHostingAccountV1,
        HostingAcknowledgementV1,
        HostingObservationV1,
        HostingResourceFactV1,
        HostingOutcomeEvidenceV1,
    )
    assert _forbidden_provider_fields(contracts) == set()


def test_provider_secret_output_guard_has_a_sensitivity_proof() -> None:
    @dataclass(frozen=True)
    class PlantedProviderResult:
        access_token: str

    assert _forbidden_provider_fields((PlantedProviderResult,)) == {
        "PlantedProviderResult.access_token"
    }


def test_every_hosting_request_event_maps_to_an_exact_operation() -> None:
    assert _unknown_request_events(PUBLIC_EVENT_TYPES) == []


def test_request_event_guard_rejects_an_undeclared_retry_operation() -> None:
    planted = (*PUBLIC_EVENT_TYPES, "hosting.account.retry.requested.v1")
    assert _unknown_request_events(planted) == [
        "hosting.account.retry.requested.v1"
    ]


def test_module_imports_no_sibling_business_owner_or_assembly() -> None:
    violations: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".", 1)[0]
                if root == "app" or root in SIBLING_IMPORTS:
                    violations.append(f"{path.name}:{name}")
    assert violations == []


def test_provider_identity_is_absent_from_hosting_code() -> None:
    assert _provider_violations(PACKAGE) == []


def test_provider_identity_guard_has_a_sensitivity_proof(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_hosting"
    planted.mkdir()
    (planted / "bad.py").write_text("panel = 'DirectAdmin'\n", encoding="utf-8")
    assert _provider_violations(planted) == ["bad.py:DirectAdmin"]


def test_transport_retry_state_is_not_reimplemented_by_hosting() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _source_files())
    for forbidden in (
        "attempt_count",
        "next_retry_at",
        "backoff",
        "lease_owner",
        "dead_letter",
    ):
        assert forbidden not in source


def test_mailbox_lifecycle_is_absent_and_count_is_observational_only() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _source_files())
    for forbidden in (
        "MailboxService",
        "create_mailbox",
        "suspend_mailbox",
        "delete_mailbox",
        "mailbox_address",
        "mailbox_quota",
    ):
        assert forbidden not in source
    assert "mailbox_count" in source


def test_root_migration_declares_and_verifies_every_prerequisite() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "ho_0001_hosting"' in source
    assert 'branch_labels = ("hosting",)' in source
    assert "depends_on = resolve_depends_on(REQUIRES)" in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source
    assert "alembic stamp" not in source.lower()


def test_append_only_evidence_has_database_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "hosting_specifications",
        "hosting_specification_versions",
        "hosting_desired_revisions",
        "hosting_commands",
        "hosting_command_outcomes",
        "hosting_observations",
        "hosting_observation_resources",
        "hosting_termination_approval_evidence",
    ):
        assert f"CREATE TRIGGER {table}_immutable" in source
        assert f"GRANT SELECT, INSERT ON mod_hosting.{table} TO app_user" in source
        assert f"UPDATE, DELETE ON mod_hosting.{table} TO app_user" not in source


def test_online_role_has_no_delete_grant_on_hosting_aggregate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert (
        "GRANT SELECT, INSERT ON mod_hosting.hosting_services TO app_user"
        in source
    )
    assert "CREATE TRIGGER hosting_services_controlled_update" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path TO pg_catalog, pg_temp" in source
    assert "current_setting('app.current_tenant', true)" in source
    assert "REVOKE ALL ON FUNCTION mod_hosting.mutate_hosting_service(" in source
    assert "GRANT EXECUTE ON FUNCTION mod_hosting.mutate_hosting_service(" in source
    assert "GRANT UPDATE" not in "\n".join(
        line
        for line in source.splitlines()
        if "hosting_services TO app_user" in line
    )
    assert "DELETE ON mod_hosting.hosting_services TO app_user" not in source


def test_every_hosting_aggregate_mutation_uses_the_database_seam() -> None:
    source = (PACKAGE / "service.py").read_text(encoding="utf-8")
    assert _direct_service_mutations(source) == set()
    assert source.count("_mutate_service(") >= 6
    assert "UPDATE mod_hosting.hosting_services" not in source


def test_hosting_aggregate_mutation_guard_has_a_sensitivity_proof() -> None:
    assert _direct_service_mutations(
        "service.lifecycle_state = 'terminated'\nservice.row_version += 1\n"
    ) == {"lifecycle_state", "row_version"}


def test_specification_chain_and_owner_references_are_structural() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "fk_hosting_specification_versions_previous",
        "fk_hosting_services_specification_version",
        "fk_hosting_desired_revisions_specification_version",
    ):
        assert constraint in source
    assert "uq_hosting_specification_versions_chain_identity" in source
    assert "ck_hosting_specification_versions_change_rules_shape" in source
    assert "ck_hosting_specification_versions_package_rank" in source


def test_exact_declared_tables_are_owned_once() -> None:
    assert set(TABLES) == {
        "hosting_specifications",
        "hosting_specification_versions",
        "hosting_services",
        "hosting_desired_revisions",
        "hosting_commands",
        "hosting_command_outcomes",
        "hosting_observations",
        "hosting_observation_resources",
        "hosting_suspension_locks",
        "hosting_retention_holds",
        "hosting_termination_approval_evidence",
        "hosting_attention_conditions",
    }
    assert len(TABLES) == len(set(TABLES))


def test_public_surface_does_not_export_orm_models() -> None:
    assert "HostingService" not in dotmac_hosting.__all__
    assert "HostingObservation" not in dotmac_hosting.__all__
    with pytest.raises(AttributeError):
        getattr(dotmac_hosting, "HostingService")  # noqa: B009
