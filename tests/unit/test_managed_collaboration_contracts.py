"""The managed-collaboration catalogue is exact, provider-free contract data."""

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
PACKAGE_ROOT = ROOT / "packages" / "dotmac-managed-collaboration-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_managed_collaboration_contracts as collaboration  # noqa: E402


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in collaboration.CAPABILITY_SCHEMAS
    }


def _public_capability_id(contract: CapabilityContractSnapshot) -> str:
    return f"{contract.capability_code}.v{contract.schema_version}"


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
        r"(?:^|[^a-z0-9])(nextcloud|owncloud)(?:$|[^a-z0-9])",
        re.IGNORECASE,
    )
    executable_marker = re.compile(
        r"(?:^|[^a-z0-9])(argv|command_text|shell|startup_script)(?:$|[^a-z0-9])",
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
            elif isinstance(node, ast.Name):
                if provider_marker.search(node.id) or executable_marker.search(node.id):
                    violations.append(node.id)
            elif isinstance(node, ast.Attribute):
                if provider_marker.search(node.attr) or executable_marker.search(
                    node.attr
                ):
                    violations.append(node.attr)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in docstrings
                and (
                    provider_marker.search(node.value)
                    or executable_marker.search(node.value)
                )
            ):
                violations.append(node.value)
    return tuple(sorted(set(violations)))


def test_manifest_uses_versioned_public_ids_but_contract_codes_are_unversioned() -> (
    None
):
    assert collaboration.__version__ == "0.1.0a1"
    assert isinstance(collaboration.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert collaboration.PRODUCT_MANIFEST.product_code == (
        "dotmac-managed-collaboration"
    )
    assert collaboration.PRODUCT_MANIFEST.product_version == collaboration.__version__
    assert collaboration.PRODUCT_MANIFEST.capability_codes == (
        "collaboration.application.lifecycle.v1",
        "collaboration.file-roundtrip.lifecycle.v1",
        "collaboration.user-group-quota.lifecycle.v1",
        "collaboration.user-oidc.configuration.lifecycle.v1",
    )
    assert (
        tuple(
            _public_capability_id(contract)
            for contract in collaboration.CAPABILITY_CONTRACTS
        )
        == collaboration.PRODUCT_MANIFEST.capability_codes
    )
    assert all(
        not contract.capability_code.endswith(f".v{contract.schema_version}")
        for contract in collaboration.CAPABILITY_CONTRACTS
    )


def test_contracts_and_exact_draft_2020_12_schema_bytes_cover_one_another() -> None:
    expected: dict[str, str] = {}
    assert all(
        isinstance(contract, CapabilityContractSnapshot)
        for contract in collaboration.CAPABILITY_CONTRACTS
    )
    for contract in collaboration.CAPABILITY_CONTRACTS:
        contract.require_declared_by(collaboration.PRODUCT_MANIFEST)
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

    schemas = {schema.schema_ref: schema for schema in collaboration.CAPABILITY_SCHEMAS}
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


def test_application_lifecycle_carries_operational_obligations_without_host_code() -> (
    None
):
    contract = collaboration.APPLICATION_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-collaboration",
        "collaboration.application.lifecycle",
        1,
    )
    assert {field.field_code: field.value_type for field in contract.config_fields} == {
        "backup_storage_ref": CapabilityConfigValueType.REFERENCE,
        "management_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
        "release_channel_ref": CapabilityConfigValueType.REFERENCE,
    }
    assert {check.check_code for check in contract.checks} == {
        "collaboration.application.backup-latest-restorable",
        "collaboration.application.backup-storage-held",
        "collaboration.application.control-api-private",
        "collaboration.application.decommission-observed",
        "collaboration.application.health-observed",
        "collaboration.application.restore-health-validated",
        "collaboration.application.rollback-available",
        "collaboration.application.suspension-observed",
        "collaboration.application.upgrade-health-validated",
    }

    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["action"]["enum"] == [
        "backup",
        "decommission",
        "ensure_active",
        "restore",
        "resume",
        "suspend",
        "upgrade",
    ]

    response = documents[contract.require_operation("apply").output_schema_ref]
    observed = cast(dict[str, dict[str, object]], response["properties"])
    assert observed["lifecycle_state"]["enum"] == [
        "absent",
        "active",
        "decommissioned",
        "suspended",
    ]
    for field in (
        "backup_object_ref",
        "backup_version_ref",
        "health_validated",
        "installed_version",
        "restore_validated",
        "rollback_available",
        "upgrade_validated",
    ):
        assert observed[field]["x-dotmac-data-classification"] == ("public_non_secret")

    validator = Draft202012Validator(response)
    incomplete_backup = {
        "action": "backup",
        "application_ref": "application-1",
        "completed_at": "2026-08-17T12:00:00Z",
        "health_validated": True,
        "lifecycle_state": "active",
        "observed_configuration_digest": f"sha256:{'0' * 64}",
    }
    assert {error.validator for error in validator.iter_errors(incomplete_backup)} == {
        "required"
    }


def test_user_oidc_configuration_is_exact_and_cannot_jit_or_email_link() -> None:
    contract = collaboration.USER_OIDC_CONFIGURATION_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-collaboration",
        "collaboration.user-oidc.configuration.lifecycle",
        1,
    )
    assert {check.check_code for check in contract.checks} == {
        "collaboration.user-oidc.audience-azp-validation",
        "collaboration.user-oidc.backchannel-logout",
        "collaboration.user-oidc.configuration-observed",
        "collaboration.user-oidc.direct-login-break-glass",
        "collaboration.user-oidc.email-linking-disabled",
        "collaboration.user-oidc.issuer-subject-binding",
        "collaboration.user-oidc.jit-account-creation-disabled",
        "collaboration.user-oidc.pkce-s256",
        "collaboration.user-oidc.session-provenance",
        "collaboration.user-oidc.session-revocation",
    }
    documents = _documents()
    for operation_code in ("apply", "plan"):
        request = documents[contract.require_operation(operation_code).input_schema_ref]
        properties = cast(dict[str, dict[str, object]], request["properties"])
        assert "email" not in properties
        assert properties["account_creation_mode"]["const"] == ("preprovisioned_only")
        assert properties["backchannel_logout_enabled"]["const"] is True
        assert properties["direct_login_mode"]["const"] == "break_glass"
        assert properties["email_linking_enabled"]["const"] is False
        assert properties["identity_binding_key"]["const"] == "issuer_subject"
        assert properties["pkce_method"]["const"] == "S256"
        assert properties["require_aud_azp_validation"]["const"] is True
        assert properties["session_provenance_required"]["const"] is True
        assert properties["session_revocation_required"]["const"] is True
        assert properties["subject_claim"]["const"] == "sub"


def test_user_group_quota_lifecycle_uses_stable_ids_not_email_identity() -> None:
    contract = collaboration.USER_GROUP_QUOTA_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-collaboration",
        "collaboration.user-group-quota.lifecycle",
        1,
    )
    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["resource_kind"]["enum"] == [
        "group",
        "group_membership",
        "quota",
        "user",
    ]
    assert "email" not in properties
    assert properties["identity_issuer"]["format"] == "uri"
    assert properties["identity_subject"]["minLength"] == 1
    assert properties["quota_bytes"]["minimum"] == 0


def test_file_roundtrip_requires_write_read_digest_match_and_cleanup() -> None:
    contract = collaboration.FILE_ROUNDTRIP_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-collaboration",
        "collaboration.file-roundtrip.lifecycle",
        1,
    )
    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["cleanup_required"]["const"] is True
    assert properties["probe_content"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    response = documents[contract.require_operation("apply").output_schema_ref]
    observed = cast(dict[str, dict[str, object]], response["properties"])
    for field in (
        "cleanup_succeeded",
        "digest_matches",
        "read_digest",
        "read_succeeded",
        "write_digest",
        "write_succeeded",
    ):
        assert observed[field]["x-dotmac-data-classification"] == ("public_non_secret")

    validator = Draft202012Validator(request)
    valid = {
        "application_ref": "application-1",
        "cleanup_required": True,
        "expected_content_digest": f"sha256:{'0' * 64}",
        "logical_path": "/dotmac-probes/roundtrip-1.txt",
        "probe_content": "bounded public probe",
        "roundtrip_ref": "roundtrip-1",
        "user_ref": "user-1",
    }
    assert list(validator.iter_errors(valid)) == []
    for unsafe_path in ("/../escape", "/.", "/folder/../escape", "//empty"):
        assert list(validator.iter_errors({**valid, "logical_path": unsafe_path}))


def test_no_output_schema_can_emit_secret_material_or_a_secret_reference() -> None:
    def assert_safe(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert key not in {
                    "access_token",
                    "client_secret",
                    "password",
                    "private_key",
                    "refresh_token",
                }, schema_ref
                assert_safe(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child, schema_ref=schema_ref)

    documents = _documents()
    for contract in collaboration.CAPABILITY_CONTRACTS:
        for operation in contract.operations:
            assert_safe(
                documents[operation.output_schema_ref],
                schema_ref=operation.output_schema_ref,
            )


def test_operation_requests_never_repeat_held_config_or_secret_reference_fields() -> (
    None
):
    def assert_no_secret_shape(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert not key.endswith("_secret_ref"), schema_ref
                assert_no_secret_shape(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_no_secret_shape(child, schema_ref=schema_ref)

    documents = _documents()
    orchestrator_fields = {
        "capability_id",
        "command_id",
        "config",
        "idempotency_key",
        "operation_ref",
        "plan_digest",
        "plan_hash",
        "provider_operation_ref",
        "secrets",
        "step_key",
    }
    for contract in collaboration.CAPABILITY_CONTRACTS:
        installation_codes = {field.field_code for field in contract.config_fields} | {
            endpoint.endpoint_code for endpoint in contract.endpoint_requirements
        }
        for operation in contract.operations:
            request = documents[operation.input_schema_ref]
            properties = cast(dict[str, object], request["properties"])
            assert installation_codes.isdisjoint(properties), operation.input_schema_ref
            assert orchestrator_fields.isdisjoint(
                properties
            ), operation.input_schema_ref
            assert_no_secret_shape(request, schema_ref=operation.input_schema_ref)


def test_all_four_operation_inputs_are_executable_target_mappings() -> None:
    documents = _documents()
    for contract in collaboration.CAPABILITY_CONTRACTS:
        apply = documents[contract.require_operation("apply").input_schema_ref]
        plan = documents[contract.require_operation("plan").input_schema_ref]
        assert plan["properties"] == apply["properties"]
        assert plan["required"] == apply["required"]
        assert plan.get("allOf") == apply.get("allOf")

        apply_properties = cast(dict[str, object], apply["properties"])
        for operation_code in ("cancel", "observe"):
            request = documents[
                contract.require_operation(operation_code).input_schema_ref
            ]
            target_properties = cast(dict[str, object], request["properties"])
            assert set(target_properties) <= set(apply_properties)
            for field, declaration in target_properties.items():
                assert declaration == apply_properties[field]


def test_catalogue_has_no_provider_io_persistence_or_executable_surface() -> None:
    assert _catalogue_boundary_violations(SOURCE_ROOT) == ()


def test_catalogue_boundary_scan_bites_without_punishing_prose(
    tmp_path: Path,
) -> None:
    planted = tmp_path / "catalogue.py"
    planted.write_text(
        '"""No Nextcloud or shell branch belongs here."""\n'
        "import requests\n"
        'provider = "nextcloud"\n'
        'argv = ["unsafe"]\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == (
        "argv",
        "nextcloud",
        "requests",
    )
    planted.write_text(
        '"""No Nextcloud or shell branch belongs here."""\n'
        'VALUE = "provider-neutral"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated_and_immutable() -> None:
    assert set(collaboration.__all__) == {
        "APPLICATION_LIFECYCLE",
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "FILE_ROUNDTRIP_LIFECYCLE",
        "PRODUCT_MANIFEST",
        "USER_GROUP_QUOTA_LIFECYCLE",
        "USER_OIDC_CONFIGURATION_LIFECYCLE",
        "__version__",
    }
    with pytest.raises(AttributeError):
        collaboration.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            collaboration.APPLICATION_LIFECYCLE
        )
