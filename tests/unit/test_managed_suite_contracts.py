"""The managed-suite catalogue owns immutable dataflow, never product logic."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import cast

from dotmac_kernel import (
    CapabilityCompositionSnapshot,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)

ROOT = Path(__file__).resolve().parents[2]
COLLABORATION_ROOT = (
    ROOT / "packages" / "dotmac-managed-collaboration-contracts" / "src"
)
DOMAINS_ROOT = ROOT / "packages" / "dotmac-domains-contracts" / "src"
EMAIL_ROOT = ROOT / "packages" / "dotmac-managed-email-contracts" / "src"
IDENTITY_ROOT = ROOT / "packages" / "dotmac-managed-identity-contracts" / "src"
SUITE_ROOT = ROOT / "packages" / "dotmac-managed-suite-contracts" / "src"
sys.path[:0] = [
    str(COLLABORATION_ROOT),
    str(DOMAINS_ROOT),
    str(EMAIL_ROOT),
    str(IDENTITY_ROOT),
    str(SUITE_ROOT),
]

import dotmac_domains_contracts as domains  # noqa: E402
import dotmac_managed_collaboration_contracts as collaboration  # noqa: E402
import dotmac_managed_email_contracts as email  # noqa: E402
import dotmac_managed_identity_contracts as identity  # noqa: E402
import dotmac_managed_suite_contracts as suite  # noqa: E402


def _schema(ref: str) -> CapabilitySchemaDocument:
    return next(
        item for item in suite.COMPOSITION_DEPENDENCY_SCHEMAS if item.schema_ref == ref
    )


def test_suite_manifest_owns_compositions_but_no_product_capability() -> None:
    assert suite.__version__ == "0.1.0a1"
    assert isinstance(suite.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert suite.PRODUCT_MANIFEST.product_code == "dotmac-managed-suite"
    assert suite.PRODUCT_MANIFEST.product_version == suite.__version__
    assert suite.PRODUCT_MANIFEST.capability_codes == ()
    assert suite.CAPABILITY_CONTRACTS == ()
    assert suite.CAPABILITY_SCHEMAS == ()
    assert suite.CAPABILITY_COMPOSITIONS == (
        suite.COLLABORATION_FEDERATION,
        suite.EMAIL_APPLICATION_DEPENDENCIES,
        suite.EMAIL_DNS,
        suite.EMAIL_FEDERATION,
        suite.IDENTITY_ACCOUNT_FEDERATION,
        suite.IDENTITY_FEDERATION,
    )
    assert set(suite.COMPOSITION_DEPENDENCY_CONTRACTS) == {
        *collaboration.CAPABILITY_CONTRACTS,
        *domains.CAPABILITY_CONTRACTS,
        *email.CAPABILITY_CONTRACTS,
        *identity.CAPABILITY_CONTRACTS,
    }
    assert not suite.PRODUCT_MANIFEST.capability_codes


def test_identity_federation_maps_exact_realm_evidence() -> None:
    composition = suite.IDENTITY_FEDERATION
    assert isinstance(composition, CapabilityCompositionSnapshot)
    assert composition.identity == (
        "dotmac-managed-suite",
        "managed-suite.identity-federation.v1",
        1,
    )
    assert len(composition.evidence_bindings) == 3
    binding = next(
        item
        for item in composition.evidence_bindings
        if item.binding_code == "realm-issuer-to-oidc-client"
    )
    assert binding.binding_code == "realm-issuer-to-oidc-client"
    assert (
        binding.source_owner_code,
        binding.source_capability_code,
        binding.source_operation_code,
        binding.source_pointer,
    ) == (
        "dotmac-managed-identity",
        "identity.realm.lifecycle",
        "apply",
        "/issuer_url",
    )
    assert (
        binding.target_owner_code,
        binding.target_capability_code,
        binding.target_operation_code,
        binding.target_pointer,
    ) == (
        "dotmac-managed-identity",
        "identity.oidc-client.lifecycle",
        "apply",
        "/issuer_url",
    )
    source = _schema(binding.source_output_schema_ref)
    target = _schema(binding.target_input_schema_ref)
    assert source.digest == binding.source_output_schema_digest
    assert target.digest == binding.target_input_schema_digest
    assert source.require_public_non_secret_pointer("/issuer_url")["format"] == "uri"
    assert target.require_public_non_secret_pointer("/issuer_url")["format"] == "uri"


def test_identity_user_evidence_maps_to_preprovisioned_collaboration_user() -> None:
    composition = suite.IDENTITY_ACCOUNT_FEDERATION
    assert composition.identity == (
        "dotmac-managed-suite",
        "managed-suite.identity-account-federation.v1",
        1,
    )
    assert {
        (binding.source_pointer, binding.target_pointer)
        for binding in composition.evidence_bindings
    } == {
        ("/identity_ref", "/user_ref"),
        ("/issuer_url", "/identity_issuer"),
        ("/subject", "/identity_subject"),
    }
    assert {
        (binding.target_selector_pointer, binding.target_selector_value)
        for binding in composition.evidence_bindings
    } == {("/resource_kind", "user")}
    assert {binding.coverage for binding in composition.evidence_bindings} == {
        "each_target_exactly_one"
    }


def test_composition_cross_checks_exact_owner_contracts_and_schemas() -> None:
    for composition in suite.CAPABILITY_COMPOSITIONS:
        composition.require_owned_by(suite.PRODUCT_MANIFEST)
        composition.require_compatible_with(
            contracts=suite.COMPOSITION_DEPENDENCY_CONTRACTS,
            schemas=suite.COMPOSITION_DEPENDENCY_SCHEMAS,
        )
        assert (
            CapabilityCompositionSnapshot.from_json_bytes(
                composition.to_json_bytes(),
                expected_digest=composition.digest,
            )
            == composition
        )


def test_suite_maps_identity_into_email_and_collaboration_and_email_into_dns() -> None:
    expected = {
        "managed-suite.collaboration-federation.v1": {
            ("/client_id", "/client_id"),
            ("/issuer_url", "/issuer_url"),
        },
        "managed-suite.email-dns.v1": {("/dns_requirements", "/dns_requirements")},
        "managed-suite.email-application-dependencies.v1": {
            ("/application_ref", "/application_ref")
        },
        "managed-suite.email-federation.v1": {
            ("/client_id", "/oidc_client_id"),
            ("/issuer_url", "/oidc_issuer_url"),
        },
        "managed-suite.identity-account-federation.v1": {
            ("/identity_ref", "/user_ref"),
            ("/issuer_url", "/identity_issuer"),
            ("/subject", "/identity_subject"),
        },
        "managed-suite.identity-federation.v1": {
            ("/issuer_url", "/issuer_url"),
            ("/realm_ref", "/realm_ref"),
        },
    }
    for composition in suite.CAPABILITY_COMPOSITIONS:
        assert {
            (binding.source_pointer, binding.target_pointer)
            for binding in composition.evidence_bindings
        } == expected[composition.composition_code]


def test_email_edges_apply_only_to_declared_resource_instances() -> None:
    assert {
        binding.target_selector_value
        for binding in suite.EMAIL_APPLICATION_DEPENDENCIES.evidence_bindings
    } == {
        "alias",
        "app_password",
        "delivery",
        "dkim",
        "domain",
        "mailbox",
        "quota",
    }
    assert {
        binding.source_selector_value
        for binding in suite.EMAIL_APPLICATION_DEPENDENCIES.evidence_bindings
    } == {"application"}
    assert {
        (
            binding.source_selector_value,
            binding.target_selector_value,
        )
        for binding in suite.EMAIL_DNS.evidence_bindings
    } == {("dkim", "recordset"), ("domain", "recordset")}
    assert {
        binding.target_selector_value
        for binding in suite.EMAIL_FEDERATION.evidence_bindings
    } == {"application"}
    assert {binding.coverage for binding in suite.EMAIL_DNS.evidence_bindings} == {
        "each_source_exactly_one"
    }
    assert {
        binding.coverage
        for composition in (
            suite.IDENTITY_FEDERATION,
            suite.EMAIL_FEDERATION,
            suite.EMAIL_APPLICATION_DEPENDENCIES,
            suite.COLLABORATION_FEDERATION,
            suite.IDENTITY_ACCOUNT_FEDERATION,
        )
        for binding in composition.evidence_bindings
    } == {"each_target_exactly_one"}


def test_composition_contains_no_runtime_value_or_secret_shape() -> None:
    documents = [
        cast(
            dict[str, object],
            json.loads(composition.to_json_bytes().decode("utf-8")),
        )
        for composition in suite.CAPABILITY_COMPOSITIONS
    ]
    encoded = json.dumps(documents, sort_keys=True)
    assert "https://" not in encoded
    assert "client_secret" not in encoded
    assert "password" not in encoded
    assert "private_key" not in encoded
    assert "secret_reference" not in encoded
    assert "public_non_secret" in encoded


def test_suite_exact_pins_every_catalogue_that_supplies_its_edges() -> None:
    pyproject = tomllib.loads(
        (ROOT / "packages/dotmac-managed-suite-contracts/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    dependencies = pyproject["tool"]["poetry"]["dependencies"]
    assert dependencies["dotmac-kernel"] == ">=0.1.0a69,<0.2.0"
    assert {
        name: version
        for name, version in dependencies.items()
        if name.endswith("-contracts")
    } == {
        "dotmac-domains-contracts": "0.1.0a1",
        "dotmac-managed-collaboration-contracts": "0.1.0a1",
        "dotmac-managed-email-contracts": "0.1.0a1",
        "dotmac-managed-identity-contracts": "0.1.0a1",
    }


def test_suite_does_not_claim_unreleased_application_edges() -> None:
    payload = b"".join(
        composition.to_json_bytes() for composition in suite.CAPABILITY_COMPOSITIONS
    )
    for absent_owner in (
        b"dotmac-erp",
        b"dotmac-academy",
        b"dotmac-workspace",
    ):
        assert absent_owner not in payload


def test_public_surface_is_curated() -> None:
    assert set(suite.__all__) == {
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COLLABORATION_FEDERATION",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "EMAIL_APPLICATION_DEPENDENCIES",
        "EMAIL_DNS",
        "EMAIL_FEDERATION",
        "IDENTITY_FEDERATION",
        "IDENTITY_ACCOUNT_FEDERATION",
        "PRODUCT_MANIFEST",
        "__version__",
    }
