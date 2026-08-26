"""Managed host publishes closed, provider-neutral contract data."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import cast

import pytest
from dotmac_kernel import (
    CapabilityContractSnapshot,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-managed-host-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_managed_host_contracts as host  # noqa: E402


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in host.CAPABILITY_SCHEMAS
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
        "asyncssh",
        "asyncpg",
        "fabric",
        "httpx",
        "importlib",
        "paramiko",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
    execution_marker = re.compile(
        r"(?:^|[^a-z0-9])(argv|command|exec|file.?and.?run|script|shell|ssh)"
        r"(?:$|[^a-z0-9])",
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
            elif isinstance(node, ast.Name) and execution_marker.search(node.id):
                violations.append(node.id)
            elif isinstance(node, ast.Attribute) and execution_marker.search(node.attr):
                violations.append(node.attr)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in docstrings
                and execution_marker.search(node.value)
            ):
                violations.append(node.value)
    return tuple(sorted(set(violations)))


def test_manifest_declares_three_versioned_ids_from_unversioned_contracts() -> None:
    assert host.__version__ == "0.1.0a1"
    assert isinstance(host.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert host.PRODUCT_MANIFEST.product_code == "dotmac-managed-host"
    assert host.PRODUCT_MANIFEST.capability_codes == (
        "host.backup-restore.lifecycle.v1",
        "host.deployment-bundle.lifecycle.v1",
        "host.health-probe.lifecycle.v1",
    )
    assert (
        tuple(contract.capability_id for contract in host.CAPABILITY_CONTRACTS)
        == host.PRODUCT_MANIFEST.capability_codes
    )
    for contract in host.CAPABILITY_CONTRACTS:
        assert not contract.capability_code.endswith(f".v{contract.schema_version}")
        contract.require_declared_by(host.PRODUCT_MANIFEST)


def test_contracts_and_exact_draft_2020_12_schema_bytes_cover_one_another() -> None:
    expected: dict[str, str] = {}
    assert all(
        isinstance(contract, CapabilityContractSnapshot)
        for contract in host.CAPABILITY_CONTRACTS
    )
    for contract in host.CAPABILITY_CONTRACTS:
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
    schemas = {schema.schema_ref: schema for schema in host.CAPABILITY_SCHEMAS}
    assert tuple(schemas) == tuple(sorted(schemas))
    assert len(schemas) == 24
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


def test_bundle_lifecycle_is_closed_and_contains_update_semantics() -> None:
    contract = host.DEPLOYMENT_BUNDLE_LIFECYCLE
    assert contract.identity == (
        "dotmac-managed-host",
        "host.deployment-bundle.lifecycle",
        1,
    )
    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["bundle_operation_code"]["enum"] == [
        "decommission",
        "install",
        "repair",
        "resume",
        "rollback",
        "suspend",
        "upgrade",
    ]
    assert properties["bundle_operation_version"]["const"] == 1
    assert properties["artifact_digest"]["type"] == "string"
    assert properties["configuration_digest"]["type"] == "string"
    forbidden = {"argv", "command", "exec", "script", "shell", "ssh"}
    assert _declared_property_names(request).isdisjoint(forbidden)
    response = documents[contract.require_operation("apply").output_schema_ref]
    observed = cast(dict[str, dict[str, object]], response["properties"])
    assert observed["installed_version"]["type"] == "string"
    assert observed["rollback_available"]["type"] == "boolean"
    assert observed["health_state"]["enum"] == [
        "degraded",
        "healthy",
        "unhealthy",
    ]


def test_backup_restore_and_health_probe_have_distinct_failure_boundaries() -> None:
    documents = _documents()
    backup = host.BACKUP_RESTORE_LIFECYCLE
    protection_request = documents[backup.require_operation("apply").input_schema_ref]
    protection_properties = cast(
        dict[str, dict[str, object]], protection_request["properties"]
    )
    assert protection_properties["action"]["enum"] == ["backup", "restore"]
    protection_result = documents[backup.require_operation("apply").output_schema_ref]
    protection_observed = cast(
        dict[str, dict[str, object]], protection_result["properties"]
    )
    assert protection_observed["backup_object_ref"]["type"] == "string"
    assert protection_observed["backup_version_ref"]["type"] == "string"
    assert protection_observed["restore_validated"]["type"] == "boolean"

    health = host.HEALTH_PROBE_LIFECYCLE
    health_request = documents[health.require_operation("apply").input_schema_ref]
    health_properties = cast(dict[str, dict[str, object]], health_request["properties"])
    assert health_properties["probe_kind"]["enum"] == [
        "http_roundtrip",
        "liveness",
        "readiness",
        "service",
    ]
    health_result = documents[health.require_operation("apply").output_schema_ref]
    health_observed = cast(dict[str, dict[str, object]], health_result["properties"])
    assert health_observed["health_state"]["enum"] == [
        "degraded",
        "healthy",
        "unhealthy",
    ]
    assert health_observed["observed_at"]["format"] == "date-time"


def test_operation_inputs_never_repeat_config_or_carry_secret_shaped_keys() -> None:
    documents = _documents()
    secret_tokens = {"credential", "password", "privatekey", "secret", "token"}
    orchestrator_fields = {
        "approval_digest",
        "approval_id",
        "command_id",
        "deployment_ref",
        "plan_digest",
        "plan_hash",
    }
    for contract in host.CAPABILITY_CONTRACTS:
        installation_codes = {field.field_code for field in contract.config_fields} | {
            endpoint.endpoint_code for endpoint in contract.endpoint_requirements
        }
        for operation in contract.operations:
            names = _declared_property_names(documents[operation.input_schema_ref])
            output_names = _declared_property_names(
                documents[operation.output_schema_ref]
            )
            assert names.isdisjoint(installation_codes)
            assert names.isdisjoint(orchestrator_fields)
            assert output_names.isdisjoint(orchestrator_fields)
            assert not {
                name
                for name in names
                if set(re.findall(r"[a-z0-9]+", name.lower())) & secret_tokens
                or "".join(re.findall(r"[a-z0-9]+", name.lower())) in secret_tokens
            }


def test_all_operation_inputs_are_immutable_target_mappings() -> None:
    documents = _documents()
    for contract in host.CAPABILITY_CONTRACTS:
        apply = documents[contract.require_operation("apply").input_schema_ref]
        plan = documents[contract.require_operation("plan").input_schema_ref]
        assert plan["properties"] == apply["properties"]
        assert plan["required"] == apply["required"]
        assert plan.get("allOf") == apply.get("allOf")

        apply_properties = cast(dict[str, object], apply["properties"])
        apply_required = set(cast(list[str], apply["required"]))
        for operation_code in ("cancel", "observe"):
            request = documents[
                contract.require_operation(operation_code).input_schema_ref
            ]
            target_properties = cast(dict[str, object], request["properties"])
            target_required = set(cast(list[str], request["required"]))
            assert target_required == set(target_properties)
            assert target_required <= apply_required
            for field, declaration in target_properties.items():
                assert declaration == apply_properties[field]


def test_output_schemas_are_secret_free_public_facts() -> None:
    def assert_safe(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert key not in {"credential", "password", "private_key", "token"}
                assert_safe(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child, schema_ref=schema_ref)

    documents = _documents()
    for contract in host.CAPABILITY_CONTRACTS:
        for operation in contract.operations:
            assert_safe(
                documents[operation.output_schema_ref],
                schema_ref=operation.output_schema_ref,
            )


def test_catalogue_has_no_provider_io_persistence_dynamic_dispatch_or_remote_exec() -> (
    None
):
    assert _catalogue_boundary_violations(SOURCE_ROOT) == ()


def test_boundary_scan_bites_without_punishing_prose(tmp_path: Path) -> None:
    planted = tmp_path / "catalogue.py"
    planted.write_text(
        '"""No SSH or shell surface belongs here."""\n'
        "import subprocess\n"
        'kind = "argv"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ("argv", "subprocess")
    planted.write_text(
        '"""No SSH or shell surface belongs here."""\nVALUE = "closed-bundle"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated() -> None:
    assert set(host.__all__) == {
        "BACKUP_RESTORE_LIFECYCLE",
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "DEPLOYMENT_BUNDLE_LIFECYCLE",
        "HEALTH_PROBE_LIFECYCLE",
        "PRODUCT_MANIFEST",
        "__version__",
    }
    with pytest.raises(AttributeError):
        host.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            host.DEPLOYMENT_BUNDLE_LIFECYCLE
        )
