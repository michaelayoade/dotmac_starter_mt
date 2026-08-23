from __future__ import annotations

import ast
import re
from dataclasses import dataclass, fields
from pathlib import Path

import dotmac_domains
import pytest
from dotmac_domains.contracts import (
    DNS_AUTHORITATIVE_CAPABILITY,
    DNS_OPERATIONS,
    DOMAINS_REGISTRAR_CAPABILITY,
    REGISTRAR_OPERATIONS,
    ReconcileRegistrarDomainV1,
    RegisterDomainV1,
    RenewDomainV1,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
)
from dotmac_domains.fakes import (
    FakeDNSAuthoritativeCapabilityV1,
    FakeRegistrarCapabilityV1,
)
from dotmac_domains.manifest import module
from dotmac_domains.models import SCHEMA, TABLES
from dotmac_domains.service import PUBLIC_EVENT_TYPES
from dotmac_domains.testing import (
    check_dns_authoritative_capability_v1,
    check_registrar_capability_v1,
)
from dotmac_kernel.namespaces import DOMAINS_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER
from dotmac_kernel.planes import ModulePlane, declared_planes

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-domains/src/dotmac_domains"
MIGRATION = PACKAGE / "migrations/versions/do_0001_domains.py"
FORBIDDEN_PROVIDER_NAMES = re.compile(
    r"\b(blesta|directadmin|cpanel|plesk|namecheap|godaddy|resellerclub|opensrs|enom)\b",
    re.IGNORECASE,
)
SIBLING_IMPORTS = {
    "dotmac_billing",
    "dotmac_collections",
    "dotmac_fulfillment",
    "dotmac_hosting",
    "dotmac_integration",
    "dotmac_orders",
    "dotmac_subscriptions",
}
REGISTRAR_DELIVERY_CONTRACTS = (
    RegisterDomainV1,
    RenewDomainV1,
    TransferDomainV1,
    UpdateDomainContactsV1,
    UpdateDomainNameserversV1,
    ReconcileRegistrarDomainV1,
)


def _source_files(root: Path = PACKAGE) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _provider_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        match = FORBIDDEN_PROVIDER_NAMES.search(path.read_text(encoding="utf-8"))
        if match:
            violations.append(f"{path.name}:{match.group(0)}")
    return violations


def test_manifest_and_namespace_are_one_tenant_only_owner() -> None:
    assert module.code == "domains"
    assert module.version == dotmac_domains.__version__ == "0.1.0a1"
    assert module.short_code == "domains"
    assert module.migration_prefix == "do"
    assert module.migration_branch == "domains"
    assert tuple(module.tables) == TABLES
    assert module.platform_tables == ()
    assert tuple(module.outbox_event_types) == PUBLIC_EVENT_TYPES
    assert declared_planes(module) == (ModulePlane.TENANT,)
    assert DOMAINS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert DOMAINS_MIGRATION_OWNER.db_schema == SCHEMA == "mod_domains"


def test_capability_families_are_independently_bindable_not_per_verb() -> None:
    assert DOMAINS_REGISTRAR_CAPABILITY == "domains.registrar.v1"
    assert REGISTRAR_OPERATIONS == {
        "availability",
        "registration",
        "renewal",
        "transfer",
        "contacts",
        "nameservers",
        "observation",
        "reconcile",
    }
    assert DNS_AUTHORITATIVE_CAPABILITY == "dns.authoritative.v1"
    assert DNS_OPERATIONS == {"zone", "recordset", "observation"}


def _unknown_registrar_request_events(event_types: tuple[str, ...]) -> list[str]:
    prefix = "domains.registrar."
    suffix = ".requested.v1"
    unknown: list[str] = []
    for event_type in event_types:
        if event_type.startswith(prefix) and event_type.endswith(suffix):
            operation = event_type[len(prefix) : -len(suffix)]
            if operation not in REGISTRAR_OPERATIONS:
                unknown.append(event_type)
    return unknown


def _unknown_dns_request_events(event_types: tuple[str, ...]) -> list[str]:
    prefix = "dns.authoritative."
    suffix = ".requested.v1"
    unknown: list[str] = []
    for event_type in event_types:
        if event_type.startswith(prefix) and event_type.endswith(suffix):
            operation = event_type[len(prefix) : -len(suffix)]
            if operation not in DNS_OPERATIONS:
                unknown.append(event_type)
    return unknown


def test_every_registrar_request_event_maps_to_a_declared_operation() -> None:
    assert _unknown_registrar_request_events(PUBLIC_EVENT_TYPES) == []
    assert _unknown_dns_request_events(PUBLIC_EVENT_TYPES) == []


def test_registrar_request_event_guard_rejects_an_undeclared_release() -> None:
    planted = (*PUBLIC_EVENT_TYPES, "domains.registrar.release.requested.v1")
    assert _unknown_registrar_request_events(planted) == [
        "domains.registrar.release.requested.v1"
    ]


def test_dns_request_event_guard_rejects_a_generic_intent_operation() -> None:
    planted = (*PUBLIC_EVENT_TYPES, "dns.authoritative.intent.requested.v1")
    assert _unknown_dns_request_events(planted) == [
        "dns.authoritative.intent.requested.v1"
    ]


def test_provider_free_fake_passes_the_owner_conformance_kit() -> None:
    fake = FakeRegistrarCapabilityV1(available_names={"conformance-customer.ng"})
    check_registrar_capability_v1(fake)
    assert {operation for operation, _ in fake.calls} == REGISTRAR_OPERATIONS
    assert fake.registration_requests[0].nameservers == (
        "ns1.dotmac.ng",
        "ns2.dotmac.ng",
    )
    assert (
        fake.registration_requests[0].contact_set.registrant.email
        == "owner@conformance-customer.ng"
    )
    assert (
        fake.contact_requests[0].contact_set.source_reference
        == "conformance-contact-set"
    )
    assert [request.direction.value for request in fake.transfer_requests] == [
        "approve_out",
        "cancel",
    ]

    dns_fake = FakeDNSAuthoritativeCapabilityV1()
    check_dns_authoritative_capability_v1(dns_fake)
    assert {operation for operation, _ in dns_fake.calls} == DNS_OPERATIONS


def _registrar_reference_fields(contracts: tuple[type[object], ...]) -> set[str]:
    return {
        field.name
        for contract in contracts
        for field in fields(contract)
        if field.name.endswith("_ref")
    }


def test_registrar_delivery_has_no_owner_local_reference_to_resolve() -> None:
    assert _registrar_reference_fields(REGISTRAR_DELIVERY_CONTRACTS) == set()


def test_unresolved_reference_guard_detects_a_planted_local_intent_ref() -> None:
    @dataclass(frozen=True)
    class PlantedRegistrarRequest:
        local_intent_ref: str

    assert _registrar_reference_fields(
        (*REGISTRAR_DELIVERY_CONTRACTS, PlantedRegistrarRequest)
    ) == {"local_intent_ref"}


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


def test_provider_identity_is_absent_from_domain_code() -> None:
    assert _provider_violations(PACKAGE) == []


def test_provider_identity_guard_has_a_sensitivity_proof(tmp_path: Path) -> None:
    planted = tmp_path / "dotmac_domains"
    planted.mkdir()
    (planted / "bad.py").write_text("provider = 'Blesta'\n", encoding="utf-8")
    assert _provider_violations(planted) == ["bad.py:Blesta"]


def test_tenant_domain_routing_catalogue_is_not_a_model_or_import() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _source_files())
    assert "TenantDomain" not in combined
    assert "tenant_domains" not in combined
    assert "verified_at" not in combined


def test_observation_and_consequence_vocabularies_are_plain_open_text() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "postgresql.ENUM" not in migration
    assert "CREATE TYPE" not in migration
    assert "observation_kind IN" not in migration
    assert "consequence_kind IN" not in migration


def test_root_migration_declares_and_verifies_every_prerequisite() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert 'revision = "do_0001_domains"' in source
    assert 'branch_labels = ("domains",)' in source
    assert "depends_on = resolve_depends_on(REQUIRES)" in source
    assert "require_prerequisites(op.get_bind(), REQUIRES)" in source
    assert "alembic stamp" not in source.lower()
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        for node in ast.walk(tree)
    )


def test_append_only_evidence_has_database_triggers_and_no_online_mutation_grant() -> (
    None
):
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "domain_commands",
        "domain_command_outcomes",
        "domain_observations",
        "dns_observations",
        "domain_intents",
    ):
        assert f"CREATE TRIGGER {table}_immutable" in source
        assert f"GRANT SELECT, INSERT ON mod_domains.{table} TO app_user" in source
        assert f"UPDATE, DELETE ON mod_domains.{table} TO app_user" not in source


def test_mutable_evidence_only_exposes_its_one_terminal_transition() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TRIGGER domain_holds_controlled_update" in source
    assert "GRANT UPDATE (cleared_at, cleared_reason)" in source
    assert "CREATE TRIGGER domain_attention_controlled_update" in source
    assert "GRANT UPDATE (resolved_at, resolution_code)" in source
    assert "UPDATE, DELETE ON mod_domains.domain_holds" not in source
    assert "UPDATE, DELETE ON mod_domains.domain_attention_conditions" not in source
    assert "CREATE TRIGGER domain_services_controlled_update" in source
    assert "GRANT SELECT, INSERT ON mod_domains.domain_services TO app_user" in source
    assert "DELETE ON mod_domains.domain_services" not in source


def test_transfer_in_is_deferred_until_a_shared_operation_secret_channel_exists() -> None:
    assert "TransferInDomain" not in dotmac_domains.__all__
    assert "request_transfer_in" not in dotmac_domains.__all__
    assert "def request_transfer_in(" not in (PACKAGE / "service.py").read_text(
        encoding="utf-8"
    )
    assert {direction.value for direction in dotmac_domains.TransferDirection} == {
        "approve_out",
        "cancel",
    }
    assert {field.name for field in fields(TransferDomainV1)} == {
        "operation_reference",
        "name",
        "direction",
    }


def test_active_name_uniqueness_excludes_only_retry_safe_terminal_aggregates() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "uq_domain_services_tenant_active_name" in source
    assert "lifecycle_state NOT IN ('registration_failed', 'released')" in source
    assert "uq_domain_services_tenant_name" not in source


def test_every_model_is_publicly_owned_by_the_manifest() -> None:
    assert set(TABLES) == {
        "domain_services",
        "domain_commands",
        "domain_command_outcomes",
        "domain_observations",
        "dns_observations",
        "domain_intents",
        "domain_holds",
        "domain_attention_conditions",
    }
    assert len(TABLES) == len(set(TABLES))


def test_package_public_surface_does_not_export_orm_models() -> None:
    assert "DomainService" not in dotmac_domains.__all__
    assert "DomainObservation" not in dotmac_domains.__all__
    assert "DNSObservation" not in dotmac_domains.__all__
    with pytest.raises(AttributeError):
        getattr(dotmac_domains, "DomainService")  # noqa: B009
