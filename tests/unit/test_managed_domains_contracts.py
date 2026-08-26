"""The managed-domains catalogue owns exact DNS/TLS contract data."""

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
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "dotmac-domains-contracts"
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

import dotmac_domains_contracts as domains  # noqa: E402


def _documents() -> dict[str, dict[str, object]]:
    return {
        schema.schema_ref: cast(
            dict[str, object], json.loads(schema.to_json_bytes().decode("utf-8"))
        )
        for schema in domains.CAPABILITY_SCHEMAS
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
        r"(?:^|[^a-z0-9])(cloudflare|contabo|powerdns|route53)(?:$|[^a-z0-9])",
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


def test_manifest_uses_the_versioned_public_id_and_unversioned_contract_code() -> None:
    assert domains.__version__ == "0.1.0a1"
    assert isinstance(domains.PRODUCT_MANIFEST, ProductManifestSnapshot)
    assert domains.PRODUCT_MANIFEST.product_code == "dotmac-domains"
    assert domains.PRODUCT_MANIFEST.product_version == domains.__version__
    assert domains.PRODUCT_MANIFEST.capability_codes == ("dns.authoritative.v1",)
    assert domains.DNS_AUTHORITATIVE.identity == (
        "dotmac-domains",
        "dns.authoritative",
        1,
    )
    assert (
        domains.DNS_AUTHORITATIVE.capability_id
        == (domains.PRODUCT_MANIFEST.capability_codes[0])
    )


def test_contract_and_exact_draft_2020_12_schema_bytes_cover_one_another() -> None:
    contract = domains.DNS_AUTHORITATIVE
    assert domains.CAPABILITY_CONTRACTS == (contract,)
    contract.require_declared_by(domains.PRODUCT_MANIFEST)
    assert tuple(operation.operation_code for operation in contract.operations) == (
        "apply",
        "cancel",
        "observe",
        "plan",
    )
    expected: dict[str, str] = {}
    for operation in contract.operations:
        for schema_ref, digest in (
            (operation.input_schema_ref, operation.input_schema_digest),
            (operation.output_schema_ref, operation.output_schema_digest),
        ):
            assert expected.setdefault(schema_ref, digest) == digest

    schemas = {schema.schema_ref: schema for schema in domains.CAPABILITY_SCHEMAS}
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


def test_dns_contract_owns_all_required_dns_and_tls_evidence() -> None:
    contract = domains.DNS_AUTHORITATIVE
    assert {field.field_code: field.value_type for field in contract.config_fields} == {
        "api_secret_ref": CapabilityConfigValueType.SECRET_REFERENCE,
    }
    assert {check.check_code for check in contract.checks} == {
        "domains.dns.autoconfig-observed",
        "domains.dns.autodiscover-observed",
        "domains.dns.canonical-idna",
        "domains.dns.dkim-observed",
        "domains.dns.dmarc-observed",
        "domains.dns.mta-sts-observed",
        "domains.dns.mx-observed",
        "domains.dns.ptr-observed",
        "domains.dns.spf-observed",
        "domains.dns.tls-rpt-observed",
        "domains.tls.certificate-valid",
        "domains.tls.https-redirect-observed",
    }

    documents = _documents()
    request = documents[contract.require_operation("apply").input_schema_ref]
    properties = cast(dict[str, dict[str, object]], request["properties"])
    assert properties["resource_kind"]["enum"] == [
        "observation",
        "recordset",
        "zone",
    ]
    requirement = cast(dict[str, object], properties["dns_requirements"]["items"])
    requirement_properties = cast(
        dict[str, dict[str, object]], requirement["properties"]
    )
    assert requirement_properties["requirement_kind"]["enum"] == [
        "autoconfig",
        "autodiscover",
        "dkim",
        "dmarc",
        "mta_sts",
        "mx",
        "ptr",
        "spf",
        "tls_rpt",
    ]
    assert requirement_properties["record_type"]["enum"] == [
        "A",
        "AAAA",
        "CAA",
        "CNAME",
        "MX",
        "PTR",
        "SRV",
        "TXT",
    ]

    response = documents[contract.require_operation("apply").output_schema_ref]
    observed = cast(dict[str, dict[str, object]], response["properties"])
    assert observed["assigned_nameservers"]["x-dotmac-data-classification"] == (
        "public_non_secret"
    )
    assert (
        observed["observed_dns_requirements"]["x-dotmac-data-classification"]
        == "public_non_secret"
    )
    tls_evidence = cast(dict[str, object], observed["tls_evidence"]["items"])
    tls_properties = cast(dict[str, dict[str, object]], tls_evidence["properties"])
    assert set(tls_properties) == {
        "certificate_expires_at",
        "certificate_hostname_valid",
        "certificate_valid",
        "hostname",
        "http_redirects_to_https",
        "https_reachable",
        "mta_sts_policy_valid",
        "tls_rpt_uri_valid",
    }


def test_output_schemas_are_public_facts_and_never_secret_material() -> None:
    def assert_safe(value: object, *, schema_ref: str) -> None:
        if isinstance(value, dict):
            assert value.get("x-dotmac-data-classification") != "secret", schema_ref
            assert value.get("x-dotmac-value-type") != "secret_reference", schema_ref
            assert value.get("writeOnly") is not True, schema_ref
            for key, child in value.items():
                assert key not in {"password", "private_key", "token"}, schema_ref
                assert_safe(child, schema_ref=schema_ref)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child, schema_ref=schema_ref)

    documents = _documents()
    for operation in domains.DNS_AUTHORITATIVE.operations:
        assert_safe(
            documents[operation.output_schema_ref],
            schema_ref=operation.output_schema_ref,
        )


def test_operation_inputs_never_repeat_config_or_carry_secret_shaped_keys() -> None:
    documents = _documents()
    contract = domains.DNS_AUTHORITATIVE
    installation_codes = {field.field_code for field in contract.config_fields} | {
        endpoint.endpoint_code for endpoint in contract.endpoint_requirements
    }
    secret_tokens = {"credential", "password", "privatekey", "secret", "token"}
    orchestrator_fields = {
        "approval_digest",
        "command_id",
        "deployment_ref",
        "plan_digest",
        "plan_hash",
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
        '"""No Cloudflare branch belongs here."""\n'
        "import requests\n"
        'provider = "cloudflare"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ("cloudflare", "requests")
    planted.write_text(
        '"""No Cloudflare branch belongs here."""\nVALUE = "provider-neutral"\n',
        encoding="utf-8",
    )
    assert _catalogue_boundary_violations(tmp_path) == ()


def test_public_surface_is_curated_and_immutable() -> None:
    assert set(domains.__all__) == {
        "CAPABILITY_COMPOSITIONS",
        "CAPABILITY_CONTRACTS",
        "CAPABILITY_SCHEMAS",
        "COMPOSITION_DEPENDENCY_CONTRACTS",
        "COMPOSITION_DEPENDENCY_SCHEMAS",
        "DNS_AUTHORITATIVE",
        "PRODUCT_MANIFEST",
        "__version__",
    }
    with pytest.raises(AttributeError):
        domains.CAPABILITY_CONTRACTS.append(  # type: ignore[attr-defined]
            domains.DNS_AUTHORITATIVE
        )
