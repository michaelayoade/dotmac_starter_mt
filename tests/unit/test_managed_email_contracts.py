"""The managed-email catalogue is exact, provider-free contract data."""

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
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-managed-email-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_managed_email_contracts as email  # noqa: E402


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in email.CAPABILITY_SCHEMAS
    }


def _declared_property_names(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(str(key) for key in properties)
        for value in node.values():
            names.update(_declared_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.update(_declared_property_names(value))
    return names


def _catalogue_boundary_violations(source_root: Path) -> tuple[str, ...]:
    forbidden_imports = {
        "alembic",
        "asyncpg",
        "httpx",
        "importlib",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
    provider_marker = re.compile(
        r"(?:^|[^a-z0-9])(dovecot|mailcow|postfix|sogo)(?:$|[^a-z0-9])",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for source in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        docstrings = {
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
            elif isinstance(node, ast.Name) and provider_marker.search(node.id):
                violations.append(node.id)
            elif isinstance(node, ast.Attribute) and provider_marker.search(node.attr):
                violations.append(node.attr)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in docstrings
                and provider_marker.search(node.value)
            ):
                violations.append(node.value)
    return tuple(sorted(set(violations)))


def test_manifest_uses_versioned_public_ids_but_contract_codes_are_unversioned() -> (
    None
):
    assert email.__version__ == "0.1.0a1"
    assert isinstance(email.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert email.PRODUCT_MANIFEST.product_code == "dotmac-managed-email"
    assert email.PRODUCT_MANIFEST.product_version == email.__version__
    assert email.PRODUCT_MANIFEST.capability_codes == ("email.lifecycle.v1",)
    assert (
        tuple(contract.capability_id for contract in email.CAPABILITY_CONTRACTS)
        == email.PRODUCT_MANIFEST.capability_codes
    )
    assert all(
        not contract.capability_code.endswith(f".v{contract.schema_version}")
        for contract in email.CAPABILITY_CONTRACTS
    )


def test_contracts_and_exact_draft_2020_12_schema_bytes_cover_one_another() -> None:
    expected: dict[str, str] = {}
    assert all(
        isinstance(contract, CapabilityContractSnapshot)
        for contract in email.CAPABILITY_CONTRACTS
    )
    for contract in email.CAPABILITY_CONTRACTS:
        contract.require_declared_by(email.PRODUCT_MANIFEST)
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

    schemas = {schema.schema_ref: schema for schema in email.CAPABILITY_SCHEMAS}
    assert tuple(schemas) == tuple(sorted(schemas))
    assert set(schemas) == set(expected)
    for schema_ref, schema in schemas.items():
        Draft202012Validator.check_schema(schema.to_mapping())
        assert schema.digest == expected[schema_ref]
        assert (
            CapabilitySchemaDocument.from_json_bytes(
                schema.to_json_bytes(),
                expected_ref=schema_ref,
                expected_digest=expected[schema_ref],
            )
            == schema
        )


def test_email_lifecycle_is_one_coherent_resource_family() -> None:
    contract = email.EMAIL_LIFECYCLE
    assert contract.identity == ("dotmac-managed-email", "email.lifecycle", 1)
    assert {field.field_code: field.value_type for field in contract.config_fields} == {
        "admin_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
        "oidc_client_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
    }
    assert {check.check_code for check in contract.checks} == {
        "email.alias.configuration-observed",
        "email.app-password.configuration-observed",
        "email.application.admin-api-private",
        "email.application.health-ready",
        "email.application.health-observed",
        "email.application.oidc-immutable-subject",
        "email.application.oidc-no-account-or-email-linking",
        "email.application.oidc-rs256-s256-aud-azp",
        "email.delivery.configuration-observed",
        "email.dkim.configuration-observed",
        "email.domain.configuration-observed",
        "email.mailbox.configuration-observed",
        "email.quota.configuration-observed",
    }

    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["resource_kind"]["enum"] == [
        "alias",
        "app_password",
        "application",
        "delivery",
        "dkim",
        "domain",
        "mailbox",
        "quota",
    ]
    assert properties["desired_lifecycle_state"]["enum"] == [
        "absent",
        "disabled",
        "enabled",
        "present",
    ]
    assert properties["oidc_subject_binding"]["const"] == ("immutable_issuer_subject")
    assert properties["oidc_id_token_signing_algorithm"]["const"] == "RS256"
    assert properties["oidc_pkce_method"]["const"] == "S256"
    assert properties["oidc_require_aud_azp_validation"]["const"] is True
    assert properties["oidc_email_linking_enabled"]["const"] is False
    assert properties["oidc_account_creation_enabled"]["const"] is False
    assert properties["oidc_mailpassword_flow_enabled"]["const"] is False
    assert properties["backup_mx_enabled"]["const"] is False
    assert properties["relay_all_recipients_enabled"]["const"] is False
    assert properties["relay_unknown_recipients_enabled"]["const"] is False
    assert properties["domain_quota_bytes"]["multipleOf"] == 1_048_576
    assert properties["mailbox_quota_default_bytes"]["multipleOf"] == 1_048_576
    assert properties["mailbox_quota_max_bytes"]["multipleOf"] == 1_048_576
    assert not set(properties) & {field.field_code for field in contract.config_fields}

    response = documents[contract.require_operation("apply").output_schema_ref]
    observed = cast(dict[str, dict[str, object]], response["properties"])
    assert observed["healthy"]["x-dotmac-data-classification"] == ("public_non_secret")
    assert observed["dkim_record_value"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    assert observed["dns_requirements"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    assert observed["app_password_configured"]["type"] == "boolean"


def test_email_observation_requires_only_evidence_owned_by_each_resource() -> None:
    """DNS is domain/DKIM evidence, never a made-up global success field."""

    contract = email.EMAIL_LIFECYCLE
    output = next(
        schema
        for schema in email.CAPABILITY_SCHEMAS
        if schema.schema_ref == contract.require_operation("apply").output_schema_ref
    ).to_mapping()
    validator = Draft202012Validator(output)
    common = {
        "application_ref": "mail-app",
        "healthy": True,
        "lifecycle_state": "enabled",
        "observed_configuration_digest": "sha256:" + "1" * 64,
        "resource_ref": "mail-app",
    }
    application = {
        **common,
        "resource_kind": "application",
        "oidc_account_creation_enabled": False,
        "oidc_client_id": "mail-client",
        "oidc_email_linking_enabled": False,
        "oidc_enabled": True,
        "oidc_id_token_signing_algorithm": "RS256",
        "oidc_issuer_url": "https://id.example.test/realms/customer",
        "oidc_logout_uri": "https://mail.example.test/",
        "oidc_mailpassword_flow_enabled": False,
        "oidc_pkce_method": "S256",
        "oidc_redirect_uri": "https://mail.example.test/",
        "oidc_require_aud_azp_validation": True,
        "oidc_subject_binding": "immutable_issuer_subject",
    }
    assert not tuple(validator.iter_errors(application))

    domain = {
        **common,
        "resource_kind": "domain",
        "resource_ref": "example.test",
        "domain_name": "example.test",
        "mail_hostname": "mail.example.test",
    }
    assert tuple(validator.iter_errors(domain)), "domain evidence must include DNS"
    domain["dns_requirements"] = [
        {
            "owner_name": "example.test",
            "record_type": "MX",
            "required": True,
            "requirement_kind": "mx",
            "ttl": 300,
            "values": ["10 mail.example.test."],
        }
    ]
    assert not tuple(validator.iter_errors(domain))


def test_domain_and_mailbox_creation_policy_is_explicit_in_desired_state() -> None:
    contract = email.EMAIL_LIFECYCLE
    request = next(
        schema
        for schema in email.CAPABILITY_SCHEMAS
        if schema.schema_ref == contract.require_operation("apply").input_schema_ref
    ).to_mapping()
    validator = Draft202012Validator(request)
    common = {
        "application_ref": "mail-app",
        "desired_lifecycle_state": "enabled",
        "resource_ref": "managed-resource",
    }
    domain = {
        **common,
        "resource_kind": "domain",
        "backup_mx_enabled": False,
        "domain_alias_limit": 200,
        "domain_mailbox_limit": 50,
        "domain_name": "example.test",
        "domain_quota_bytes": 100 * 1_048_576,
        "global_address_list_enabled": True,
        "mailbox_quota_default_bytes": 5 * 1_048_576,
        "mailbox_quota_max_bytes": 20 * 1_048_576,
        "relay_all_recipients_enabled": False,
        "relay_unknown_recipients_enabled": False,
    }
    assert not tuple(validator.iter_errors(domain))
    assert tuple(
        validator.iter_errors(
            {key: value for key, value in domain.items() if key != "backup_mx_enabled"}
        )
    )

    mailbox = {
        **common,
        "resource_kind": "mailbox",
        "dav_access_enabled": True,
        "delivery_enabled": True,
        "domain_name": "example.test",
        "eas_access_enabled": False,
        "imap_access_enabled": True,
        "mailbox_local_part": "person",
        "pop3_access_enabled": False,
        "quota_bytes": 5 * 1_048_576,
        "sieve_access_enabled": True,
        "smtp_access_enabled": True,
        "webmail_access_enabled": True,
    }
    assert not tuple(validator.iter_errors(mailbox))
    mailbox["quota_bytes"] = 5 * 1_000_000
    assert tuple(validator.iter_errors(mailbox))


def test_no_output_schema_can_emit_secret_material_or_a_secret_reference() -> None:
    def assert_safe(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert key not in {
                    "app_password",
                    "mailbox_password",
                    "password",
                    "private_key",
                }, schema_ref
                assert_safe(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child, schema_ref=schema_ref)

    documents = _documents()
    for contract in email.CAPABILITY_CONTRACTS:
        for operation in contract.operations:
            assert_safe(
                documents[operation.output_schema_ref],
                schema_ref=operation.output_schema_ref,
            )


def test_operation_inputs_never_repeat_config_or_carry_secret_shaped_keys() -> None:
    documents = _documents()
    secret_tokens = {"credential", "password", "privatekey", "secret", "token"}
    orchestrator_fields = {
        "approval_digest",
        "command_id",
        "deployment_ref",
        "plan_digest",
        "plan_hash",
    }
    for contract in email.CAPABILITY_CONTRACTS:
        installation_codes = {field.field_code for field in contract.config_fields} | {
            endpoint.endpoint_code for endpoint in contract.endpoint_requirements
        }
        for operation in contract.operations:
            names = _declared_property_names(documents[operation.input_schema_ref])
            assert names.isdisjoint(installation_codes)
            assert names.isdisjoint(orchestrator_fields)
            assert not {
                name
                for name in names
                if set(re.findall(r"[a-z0-9]+", name.lower())) & secret_tokens
                or "".join(re.findall(r"[a-z0-9]+", name.lower())) in secret_tokens
            }


def test_catalogue_has_no_provider_io_persistence_or_dynamic_dispatch() -> None:
    assert _catalogue_boundary_violations(SOURCE_ROOT) == ()


def test_catalogue_boundary_scan_bites_without_punishing_prose(tmp_path: Path) -> None:
    planted = tmp_path / "catalogue.py"
    planted.write_text(
        '"""No Mailcow branch belongs here."""\n'
        "import requests\n"
        'provider = "mailcow"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ("mailcow", "requests")
    planted.write_text(
        '"""No Mailcow branch belongs here."""\nVALUE = "provider-neutral"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated_and_immutable() -> None:
    assert set(email.__all__) == {
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "EMAIL_LIFECYCLE",
        "PRODUCT_MANIFEST",
        "__version__",
    }
    with pytest.raises(AttributeError):
        email.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            email.EMAIL_LIFECYCLE
        )
