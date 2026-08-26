"""Managed infrastructure publishes exact provider-neutral contract data."""

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
PACKAGE_ROOT = ROOT / "packages" / "dotmac-managed-infrastructure-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_managed_infrastructure_contracts as infrastructure  # noqa: E402


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in infrastructure.CAPABILITY_SCHEMAS
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
        "boto3",
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
        r"(?:^|[^a-z0-9])(aws|azure|contabo|digitalocean|hetzner)(?:$|[^a-z0-9])",
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


def test_manifest_declares_four_versioned_ids_from_unversioned_contracts() -> None:
    assert infrastructure.__version__ == "0.1.0a1"
    assert isinstance(infrastructure.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert infrastructure.PRODUCT_MANIFEST.product_code == (
        "dotmac-managed-infrastructure"
    )
    assert infrastructure.PRODUCT_MANIFEST.capability_codes == (
        "infrastructure.firewall.lifecycle.v1",
        "infrastructure.instance.lifecycle.v1",
        "infrastructure.network.lifecycle.v1",
        "infrastructure.volume.lifecycle.v1",
    )
    assert (
        tuple(
            contract.capability_id for contract in infrastructure.CAPABILITY_CONTRACTS
        )
        == infrastructure.PRODUCT_MANIFEST.capability_codes
    )
    for contract in infrastructure.CAPABILITY_CONTRACTS:
        assert not contract.capability_code.endswith(f".v{contract.schema_version}")
        contract.require_declared_by(infrastructure.PRODUCT_MANIFEST)


def test_contracts_and_exact_draft_2020_12_schema_bytes_cover_one_another() -> None:
    expected: dict[str, str] = {}
    assert all(
        isinstance(contract, CapabilityContractSnapshot)
        for contract in infrastructure.CAPABILITY_CONTRACTS
    )
    for contract in infrastructure.CAPABILITY_CONTRACTS:
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
    schemas = {
        schema.schema_ref: schema for schema in infrastructure.CAPABILITY_SCHEMAS
    }
    assert tuple(schemas) == tuple(sorted(schemas))
    assert len(schemas) == 32
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


def test_each_resource_has_typed_desired_and_observed_state() -> None:
    documents = _documents()
    expected = {
        infrastructure.FIREWALL_LIFECYCLE: (
            {"desired_rules_digest", "firewall_rules", "resource_ref"},
            ["absent", "disabled", "enabled"],
        ),
        infrastructure.INSTANCE_LIFECYCLE: (
            {
                "artifact_digest",
                "configuration_digest",
                "instance_type",
                "network_resource_refs",
                "resource_ref",
            },
            ["absent", "running", "stopped"],
        ),
        infrastructure.NETWORK_LIFECYCLE: (
            {"cidr", "configuration_digest", "resource_ref"},
            ["absent", "available"],
        ),
        infrastructure.VOLUME_LIFECYCLE: (
            {"configuration_digest", "resource_ref", "size_bytes", "volume_type"},
            ["absent", "attached", "available"],
        ),
    }
    for contract, (required_fields, states) in expected.items():
        request = documents[contract.require_operation("apply").input_schema_ref]
        properties = cast(dict[str, object], request["properties"])
        assert required_fields <= set(properties)
        assert (
            cast(dict[str, object], properties["desired_lifecycle_state"])["enum"]
            == states
        )
        response = documents[contract.require_operation("apply").output_schema_ref]
        observed = cast(dict[str, dict[str, object]], response["properties"])
        assert (
            observed["provider_resource_ref"]["x-dotmac-data-classification"]
            == "public_non_secret"
        )
        assert (
            observed["observed_configuration_digest"]["x-dotmac-data-classification"]
            == "public_non_secret"
        )


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
    for contract in infrastructure.CAPABILITY_CONTRACTS:
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
    for contract in infrastructure.CAPABILITY_CONTRACTS:
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
    for contract in infrastructure.CAPABILITY_CONTRACTS:
        for operation in contract.operations:
            assert_safe(
                documents[operation.output_schema_ref],
                schema_ref=operation.output_schema_ref,
            )


def test_catalogue_has_no_provider_io_persistence_or_dynamic_dispatch() -> None:
    assert _catalogue_boundary_violations(SOURCE_ROOT) == ()


def test_boundary_scan_bites_without_punishing_prose(tmp_path: Path) -> None:
    planted = tmp_path / "catalogue.py"
    planted.write_text(
        '"""No Contabo branch belongs here."""\n'
        "import requests\n"
        'provider = "contabo"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ("contabo", "requests")
    planted.write_text(
        '"""No Contabo branch belongs here."""\nVALUE = "provider-neutral"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated() -> None:
    assert set(infrastructure.__all__) == {
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "FIREWALL_LIFECYCLE",
        "INSTANCE_LIFECYCLE",
        "NETWORK_LIFECYCLE",
        "PRODUCT_MANIFEST",
        "VOLUME_LIFECYCLE",
        "__version__",
    }
    with pytest.raises(AttributeError):
        infrastructure.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            infrastructure.INSTANCE_LIFECYCLE
        )
