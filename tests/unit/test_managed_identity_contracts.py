"""The managed-identity catalogue is exact, provider-free contract data."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import cast

import pytest
from dotmac_kernel import (
    CapabilityConfigValueType,
    CapabilityContractSnapshot,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-managed-identity-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_managed_identity_contracts as identity  # noqa: E402


def _schemas() -> dict[str, CapabilitySchemaDocument]:
    return {schema.schema_ref: schema for schema in identity.CAPABILITY_SCHEMAS}


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in identity.CAPABILITY_SCHEMAS
    }


def _catalogue_boundary_violations(source_root: Path) -> tuple[str, ...]:
    forbidden_imports = {
        "alembic",
        "asyncpg",
        "httpx",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
    provider_marker = re.compile(
        r"(?:^|[^a-z0-9])(auth0|entra|google|keycloak|okta)(?:$|[^a-z0-9])",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for source in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        docstring_nodes = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(
                node,
                ast.AsyncFunctionDef | ast.ClassDef | ast.FunctionDef | ast.Module,
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                violations.extend(sorted(roots & forbidden_imports))
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root in forbidden_imports:
                    violations.append(root)
            elif isinstance(node, ast.Name):
                if provider_marker.search(node.id):
                    violations.append(node.id)
            elif isinstance(node, ast.Attribute):
                if provider_marker.search(node.attr):
                    violations.append(node.attr)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in docstring_nodes
                and provider_marker.search(node.value)
            ):
                violations.append(node.value)
    return tuple(sorted(set(violations)))


def test_manifest_contracts_and_schema_bytes_exactly_cover_one_another() -> None:
    assert identity.__version__ == "0.1.0a1"
    assert isinstance(identity.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert identity.PRODUCT_MANIFEST.product_code == "dotmac-managed-identity"
    assert identity.PRODUCT_MANIFEST.product_version == identity.__version__
    assert identity.PRODUCT_MANIFEST.capability_codes == (
        "identity.oidc-client.lifecycle.v1",
        "identity.realm.lifecycle.v1",
        "identity.user.lifecycle.v1",
    )
    assert identity.CAPABILITY_COMPOSITIONS == ()
    assert identity.COMPOSITION_DEPENDENCY_CONTRACTS == ()
    assert identity.COMPOSITION_DEPENDENCY_SCHEMAS == ()

    assert all(
        isinstance(contract, CapabilityContractSnapshot)
        for contract in identity.CAPABILITY_CONTRACTS
    )
    assert tuple(
        (contract.capability_code, contract.schema_version)
        for contract in identity.CAPABILITY_CONTRACTS
    ) == tuple(
        sorted(
            (contract.capability_code, contract.schema_version)
            for contract in identity.CAPABILITY_CONTRACTS
        )
    )

    expected: dict[str, str] = {}
    for contract in identity.CAPABILITY_CONTRACTS:
        contract.require_declared_by(identity.PRODUCT_MANIFEST)
        assert tuple(operation.operation_code for operation in contract.operations) == (
            "apply",
            "cancel",
            "observe",
            "plan",
        )
        for operation in contract.operations:
            for schema_ref, digest in (
                (operation.input_schema_ref, operation.input_schema_digest),
                (operation.output_schema_ref, operation.output_schema_digest),
            ):
                assert expected.setdefault(schema_ref, digest) == digest

    schemas = _schemas()
    assert tuple(schemas) == tuple(sorted(schemas))
    assert set(schemas) == set(expected)
    for schema_ref, schema in schemas.items():
        assert schema.digest == expected[schema_ref]
        assert (
            CapabilitySchemaDocument.from_json_bytes(
                schema.to_json_bytes(),
                expected_ref=schema_ref,
                expected_digest=expected[schema_ref],
            )
            == schema
        )


def test_realm_contract_makes_private_admin_and_public_oidc_security_explicit() -> None:
    contract = identity.REALM_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-identity",
        "identity.realm.lifecycle",
        1,
    )
    assert {field.field_code: field.value_type for field in contract.config_fields} == {
        "admin_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
        "identity_policy_ref": CapabilityConfigValueType.REFERENCE,
    }
    assert {check.check_code for check in contract.checks} == {
        "identity.realm.admin-api-private",
        "identity.realm.configuration-observed",
        "identity.realm.discovery-https",
        "identity.realm.rs256-signing",
    }

    documents = _documents()
    apply_result = documents[contract.require_operation("apply").output_schema_ref]
    properties = cast(dict[str, dict[str, object]], apply_result["properties"])
    assert properties["signing_algorithm"]["const"] == "RS256"
    assert properties["admin_endpoint_public"]["const"] is False
    assert properties["issuer_url"]["format"] == "uri"
    assert properties["discovery_url"]["format"] == "uri"
    assert properties["jwks_uri"]["format"] == "uri"


def test_user_contract_uses_stable_owner_reference_and_public_subject_evidence() -> (
    None
):
    contract = identity.USER_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-identity",
        "identity.user.lifecycle",
        1,
    )
    assert {field.field_code: field.value_type for field in contract.config_fields} == {
        "admin_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
    }
    assert {check.check_code for check in contract.checks} == {
        "identity.user.configuration-observed",
        "identity.user.email-is-attribute-only",
        "identity.user.sessions-revoked-on-disable",
        "identity.user.stable-reference",
    }

    documents = _documents()
    apply_input = documents[contract.require_operation("apply").input_schema_ref]
    input_properties = cast(dict[str, dict[str, object]], apply_input["properties"])
    assert input_properties["desired_lifecycle_state"]["enum"] == [
        "active",
        "disabled",
    ]
    assert input_properties["issuer_url"]["format"] == "uri"
    assert input_properties["identity_ref"]["pattern"]
    assert input_properties["email_address"]["format"] == "email"
    email_pattern = cast(str, input_properties["email_address"]["pattern"])
    assert re.fullmatch(email_pattern, "person@example.net")
    assert re.fullmatch(email_pattern, "not-an-email") is None
    assert input_properties["enrollment_redirect_uri"]["format"] == "uri"
    assert input_properties["enrollment_lifespan_seconds"] == {
        "maximum": 86400,
        "minimum": 300,
        "type": "integer",
    }
    assert input_properties["enrollment_revision"]["pattern"]
    assert "subject" not in input_properties

    apply_output = documents[contract.require_operation("apply").output_schema_ref]
    output_properties = cast(dict[str, dict[str, object]], apply_output["properties"])
    assert output_properties["issuer_url"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    assert output_properties["subject"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    assert output_properties["sessions_revoked"]["type"] == "boolean"
    for private_attribute in (
        "email_address",
        "email_verified",
        "family_name",
        "given_name",
        "login_name",
    ):
        assert (
            "x-dotmac-data-classification" not in output_properties[private_attribute]
        )


def test_client_contract_requires_confidential_rs256_s256_and_aud_azp() -> None:
    contract = identity.OIDC_CLIENT_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-identity",
        "identity.oidc-client.lifecycle",
        1,
    )
    assert {check.check_code for check in contract.checks} == {
        "identity.oidc-client.audience-azp-validation",
        "identity.oidc-client.authorization-code",
        "identity.oidc-client.confidential-client",
        "identity.oidc-client.configuration-observed",
        "identity.oidc-client.pkce-s256",
        "identity.oidc-client.rs256-id-token",
    }

    documents = _documents()
    for operation_code in ("apply", "plan"):
        request = documents[contract.require_operation(operation_code).input_schema_ref]
        properties = cast(dict[str, dict[str, object]], request["properties"])
        assert properties["authorization_code_enabled"]["const"] is True
        assert properties["client_authentication_required"]["const"] is True
        assert properties["id_token_signing_algorithm"]["const"] == "RS256"
        assert properties["pkce_method"]["const"] == "S256"
        assert properties["require_aud_azp_validation"]["const"] is True


def test_operation_inputs_never_duplicate_installation_configuration() -> None:
    documents = _documents()
    orchestrator_fields = {
        "approval_digest",
        "command_id",
        "deployment_ref",
        "plan_digest",
        "plan_hash",
    }
    for contract in identity.CAPABILITY_CONTRACTS:
        installation_codes = {field.field_code for field in contract.config_fields} | {
            endpoint.endpoint_code for endpoint in contract.endpoint_requirements
        }
        assert installation_codes
        for operation in contract.operations:
            request = documents[operation.input_schema_ref]
            properties = cast(dict[str, object], request["properties"])
            assert installation_codes.isdisjoint(properties), operation.input_schema_ref
            assert orchestrator_fields.isdisjoint(
                properties
            ), operation.input_schema_ref
            assert all(
                not property_code.endswith(("_secret", "_secret_ref"))
                for property_code in properties
            ), operation.input_schema_ref


def test_no_output_schema_can_emit_secret_material_or_a_secret_reference() -> None:
    def assert_safe(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert key not in {
                    "client_secret",
                    "password",
                    "private_key",
                }, schema_ref
                assert_safe(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child, schema_ref=schema_ref)

    documents = _documents()
    for contract in identity.CAPABILITY_CONTRACTS:
        for operation in contract.operations:
            output = documents[operation.output_schema_ref]
            assert_safe(output, schema_ref=operation.output_schema_ref)


def test_catalogue_has_no_provider_io_persistence_or_dynamic_dispatch() -> None:
    assert _catalogue_boundary_violations(SOURCE_ROOT) == ()


def test_catalogue_boundary_scan_bites_without_punishing_prose(tmp_path: Path) -> None:
    planted = tmp_path / "catalogue.py"
    planted.write_text(
        '"""No Keycloak branch belongs here."""\n'
        "import requests\n"
        'provider = "keycloak"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ("keycloak", "requests")

    planted.write_text(
        '"""No Keycloak branch belongs here."""\nVALUE = "provider-neutral"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated_and_immutable() -> None:
    assert set(identity.__all__) == {
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "OIDC_CLIENT_LIFECYCLE",
        "PRODUCT_MANIFEST",
        "REALM_LIFECYCLE",
        "USER_LIFECYCLE",
        "__version__",
    }
    with pytest.raises(AttributeError):
        identity.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            identity.REALM_LIFECYCLE
        )
