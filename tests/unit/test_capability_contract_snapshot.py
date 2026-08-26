"""Capability contracts are product-owned canonical documents, not a catalogue.

The examples are deliberately synthetic.  A kernel test naming a real product,
provider or fleet capability would turn generic grammar into a central
vocabulary by accident.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest
from dotmac_kernel import (
    CAPABILITY_CONTRACT_SCHEMA,
    CapabilityCheck,
    CapabilityCheckStage,
    CapabilityConfigField,
    CapabilityConfigValueFormat,
    CapabilityConfigValueType,
    CapabilityContractDigestMismatchError,
    CapabilityContractError,
    CapabilityContractSnapshot,
    CapabilityEndpointRequirement,
    CapabilityEndpointType,
    CapabilityEvidenceType,
    CapabilityOperation,
    CapabilitySchemaDigestMismatchError,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)


def _operation(code: str, fill: str = "a") -> CapabilityOperation:
    return CapabilityOperation(
        operation_code=code,
        input_schema_ref=f"schema:synthetic.{code}.request@v1",
        input_schema_digest="sha256:" + fill * 64,
        output_schema_ref=f"schema:synthetic.{code}.result@v1",
        output_schema_digest="sha256:" + fill * 64,
    )


def _snapshot() -> CapabilityContractSnapshot:
    return CapabilityContractSnapshot(
        owner_code="example-product",
        capability_code="synthetic.lifecycle",
        schema_version=1,
        operations=(
            CapabilityOperation(
                operation_code="apply",
                input_schema_ref="schema:synthetic.apply.request@v1",
                input_schema_digest="sha256:" + "1" * 64,
                output_schema_ref="schema:synthetic.apply.result@v1",
                output_schema_digest="sha256:" + "2" * 64,
            ),
            CapabilityOperation(
                operation_code="observe",
                input_schema_ref="schema:synthetic.observe.request@v1",
                input_schema_digest="sha256:" + "3" * 64,
                output_schema_ref="schema:synthetic.observe.result@v1",
                output_schema_digest="sha256:" + "4" * 64,
            ),
            CapabilityOperation(
                operation_code="plan",
                input_schema_ref="schema:synthetic.plan.request@v1",
                input_schema_digest="sha256:" + "5" * 64,
                output_schema_ref="schema:synthetic.plan.result@v1",
                output_schema_digest="sha256:" + "6" * 64,
            ),
        ),
        config_fields=(
            CapabilityConfigField(
                field_code="account_reference",
                value_type=CapabilityConfigValueType.REFERENCE,
                value_format=CapabilityConfigValueFormat.NONE,
                required=False,
            ),
            CapabilityConfigField(
                field_code="credential_reference",
                value_type=CapabilityConfigValueType.SECRET_REFERENCE,
                value_format=CapabilityConfigValueFormat.NONE,
            ),
        ),
        endpoint_requirements=(
            CapabilityEndpointRequirement(
                endpoint_code="service_api",
                endpoint_type=CapabilityEndpointType.HTTPS_URL,
                operation_codes=("apply", "observe", "plan"),
            ),
            CapabilityEndpointRequirement(
                endpoint_code="service_host",
                endpoint_type=CapabilityEndpointType.FQDN,
                operation_codes=("observe",),
                required=False,
            ),
        ),
        checks=(
            CapabilityCheck(
                check_code="activation_ready",
                stage=CapabilityCheckStage.ACTIVATION,
                evidence_type=CapabilityEvidenceType.BOOLEAN,
            ),
            CapabilityCheck(
                check_code="state_receipt",
                stage=CapabilityCheckStage.EVIDENCE,
                evidence_type=CapabilityEvidenceType.DOCUMENT,
            ),
        ),
    )


def test_snapshot_is_an_exact_canonical_product_owned_document() -> None:
    snapshot = _snapshot()

    assert snapshot.schema == "dotmac.capability-contract/v1"
    assert snapshot.schema == CAPABILITY_CONTRACT_SCHEMA
    assert snapshot.identity == ("example-product", "synthetic.lifecycle", 1)
    assert snapshot.capability_id == "synthetic.lifecycle.v1"
    assert snapshot.digest.startswith("sha256:")
    assert len(snapshot.digest) == len("sha256:") + 64
    assert snapshot.to_json_bytes() == (
        b'{"capability_code":"synthetic.lifecycle","checks":['
        b'{"check_code":"activation_ready","evidence_type":"boolean",'
        b'"required":true,"stage":"activation"},'
        b'{"check_code":"state_receipt","evidence_type":"document",'
        b'"required":true,"stage":"evidence"}],"config_fields":['
        b'{"field_code":"account_reference","required":false,'
        b'"value_format":"none","value_type":"reference"},'
        b'{"field_code":"credential_reference","required":true,'
        b'"value_format":"none","value_type":"secret_reference"}],'
        b'"endpoint_requirements":[{"endpoint_code":"service_api",'
        b'"endpoint_type":"https_url","operation_codes":["apply","observe",'
        b'"plan"],"required":true},{"endpoint_code":"service_host",'
        b'"endpoint_type":"fqdn","operation_codes":["observe"],'
        b'"required":false}],"operations":[{"input_schema_digest":"sha256:'
        + b"1"
        * 64
        + b'","input_schema_ref":"schema:synthetic.apply.request@v1",'
        b'"operation_code":"apply","output_schema_digest":"sha256:'
        + b"2"
        * 64
        + b'","output_schema_ref":"schema:synthetic.apply.result@v1"},'
        b'{"input_schema_digest":"sha256:'
        + b"3"
        * 64
        + b'","input_schema_ref":"schema:synthetic.observe.request@v1",'
        b'"operation_code":"observe","output_schema_digest":"sha256:'
        + b"4"
        * 64
        + b'","output_schema_ref":"schema:synthetic.observe.result@v1"},'
        b'{"input_schema_digest":"sha256:'
        + b"5"
        * 64
        + b'","input_schema_ref":"schema:synthetic.plan.request@v1",'
        b'"operation_code":"plan","output_schema_digest":"sha256:'
        + b"6"
        * 64
        + b'","output_schema_ref":"schema:synthetic.plan.result@v1"}],'
        b'"owner_code":"example-product",'
        b'"schema":"dotmac.capability-contract/v1","schema_version":1}'
    )


def test_capability_code_is_unversioned_and_public_id_is_derived() -> None:
    with pytest.raises(CapabilityContractError, match="capability_code is unversioned"):
        replace(_snapshot(), capability_code="synthetic.lifecycle.v1")

    assert _snapshot().capability_id == "synthetic.lifecycle.v1"


def test_operations_are_independently_addressable_without_new_capabilities() -> None:
    snapshot = _snapshot()

    assert snapshot.require_operation("observe").input_schema_ref.endswith(
        "observe.request@v1"
    )
    with pytest.raises(CapabilityContractError, match="does not declare operation"):
        snapshot.require_operation("cancel")

    # There is one capability identity.  Operations have no owner/capability
    # fields of their own and therefore cannot silently mint another one.
    assert [field.name for field in fields(CapabilityOperation)] == [
        "operation_code",
        "input_schema_ref",
        "input_schema_digest",
        "output_schema_ref",
        "output_schema_digest",
    ]


def test_reference_and_secret_reference_are_distinct_schema_types() -> None:
    ordinary, secret = _snapshot().config_fields

    assert ordinary.value_type is CapabilityConfigValueType.REFERENCE
    assert secret.value_type is CapabilityConfigValueType.SECRET_REFERENCE
    assert ordinary.value_type != secret.value_type
    assert "value" not in {field.name for field in fields(CapabilityConfigField)}
    assert "default" not in {field.name for field in fields(CapabilityConfigField)}


@pytest.mark.parametrize(
    ("value_type", "value_format"),
    [
        (CapabilityConfigValueType.STRING, CapabilityConfigValueFormat.FQDN),
        (CapabilityConfigValueType.STRING_LIST, CapabilityConfigValueFormat.FQDN_LIST),
        (CapabilityConfigValueType.STRING, CapabilityConfigValueFormat.HTTPS_URL),
        (
            CapabilityConfigValueType.INTEGER,
            CapabilityConfigValueFormat.NONNEGATIVE_INTEGER,
        ),
        (CapabilityConfigValueType.INTEGER, CapabilityConfigValueFormat.BYTE_QUANTITY),
        (CapabilityConfigValueType.STRING, CapabilityConfigValueFormat.EMAIL_ADDRESS),
    ],
)
def test_generic_formats_express_managed_service_configuration_without_owner_branches(
    value_type: CapabilityConfigValueType,
    value_format: CapabilityConfigValueFormat,
) -> None:
    field = CapabilityConfigField(
        field_code="synthetic_field",
        value_type=value_type,
        value_format=value_format,
    )
    assert field.value_format is value_format


def test_configuration_format_must_match_its_primitive_type() -> None:
    with pytest.raises(CapabilityContractError, match="value_format"):
        CapabilityConfigField(
            field_code="bad",
            value_type=CapabilityConfigValueType.INTEGER,
            value_format=CapabilityConfigValueFormat.HTTPS_URL,
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("input_schema_ref", "synthetic.apply.request.v1"),
        ("input_schema_ref", "schema:synthetic.apply.request@v0"),
        ("input_schema_digest", "1" * 64),
        ("output_schema_ref", "schema:Synthetic.apply.result@v1"),
        ("output_schema_digest", "sha256:" + "A" * 64),
    ],
)
def test_operation_schema_identities_are_exact_and_content_bound(
    field_name: str, replacement: str
) -> None:
    values = {
        field.name: getattr(_operation("apply"), field.name)
        for field in fields(CapabilityOperation)
    }
    values[field_name] = replacement

    with pytest.raises(CapabilityContractError):
        CapabilityOperation(**values)


def test_endpoint_operations_must_be_declared_unique_and_canonical() -> None:
    values = {
        field.name: getattr(_snapshot(), field.name)
        for field in fields(CapabilityContractSnapshot)
    }
    values["endpoint_requirements"] = (
        CapabilityEndpointRequirement(
            endpoint_code="bad",
            endpoint_type=CapabilityEndpointType.HTTPS_URL,
            operation_codes=("invented",),
        ),
    )
    with pytest.raises(CapabilityContractError, match="undeclared operation"):
        CapabilityContractSnapshot(**values)

    with pytest.raises(CapabilityContractError, match="unique and sorted"):
        CapabilityEndpointRequirement(
            endpoint_code="bad",
            endpoint_type=CapabilityEndpointType.HTTPS_URL,
            operation_codes=("plan", "apply", "apply"),
        )


def test_configuration_fields_and_endpoints_are_disjoint_namespaces() -> None:
    values = {
        field.name: getattr(_snapshot(), field.name)
        for field in fields(CapabilityContractSnapshot)
    }
    values["config_fields"] = (
        CapabilityConfigField(
            field_code="service_api",
            value_type=CapabilityConfigValueType.STRING,
            value_format=CapabilityConfigValueFormat.HTTPS_URL,
        ),
    )

    with pytest.raises(CapabilityContractError, match="both a configuration field"):
        CapabilityContractSnapshot(**values)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        (
            "operations",
            (
                _operation("plan"),
                _operation("apply"),
            ),
        ),
        (
            "config_fields",
            (
                CapabilityConfigField(
                    field_code="zeta", value_type=CapabilityConfigValueType.STRING
                ),
                CapabilityConfigField(
                    field_code="alpha", value_type=CapabilityConfigValueType.STRING
                ),
            ),
        ),
        (
            "endpoint_requirements",
            (
                CapabilityEndpointRequirement(
                    endpoint_code="zeta",
                    endpoint_type=CapabilityEndpointType.FQDN,
                    operation_codes=("apply",),
                ),
                CapabilityEndpointRequirement(
                    endpoint_code="alpha",
                    endpoint_type=CapabilityEndpointType.FQDN,
                    operation_codes=("apply",),
                ),
            ),
        ),
        (
            "checks",
            (
                CapabilityCheck(
                    check_code="zeta",
                    stage=CapabilityCheckStage.EVIDENCE,
                    evidence_type=CapabilityEvidenceType.DOCUMENT,
                ),
                CapabilityCheck(
                    check_code="alpha",
                    stage=CapabilityCheckStage.ACTIVATION,
                    evidence_type=CapabilityEvidenceType.BOOLEAN,
                ),
            ),
        ),
    ],
)
def test_nested_declarations_must_already_be_canonically_ordered(
    field_name: str, replacement: object
) -> None:
    values = {
        field.name: getattr(_snapshot(), field.name)
        for field in fields(CapabilityContractSnapshot)
    }
    values[field_name] = replacement

    with pytest.raises(CapabilityContractError, match="canonical"):
        CapabilityContractSnapshot(**values)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        (
            "operations",
            (
                _operation("apply"),
                _operation("apply"),
            ),
        ),
        (
            "config_fields",
            (
                CapabilityConfigField(
                    field_code="same", value_type=CapabilityConfigValueType.STRING
                ),
                CapabilityConfigField(
                    field_code="same", value_type=CapabilityConfigValueType.STRING
                ),
            ),
        ),
        (
            "endpoint_requirements",
            (
                CapabilityEndpointRequirement(
                    endpoint_code="same",
                    endpoint_type=CapabilityEndpointType.FQDN,
                    operation_codes=("apply",),
                ),
                CapabilityEndpointRequirement(
                    endpoint_code="same",
                    endpoint_type=CapabilityEndpointType.FQDN,
                    operation_codes=("apply",),
                ),
            ),
        ),
        (
            "checks",
            (
                CapabilityCheck(
                    check_code="same",
                    stage=CapabilityCheckStage.ACTIVATION,
                    evidence_type=CapabilityEvidenceType.BOOLEAN,
                ),
                CapabilityCheck(
                    check_code="same",
                    stage=CapabilityCheckStage.ACTIVATION,
                    evidence_type=CapabilityEvidenceType.DOCUMENT,
                ),
            ),
        ),
    ],
)
def test_duplicate_nested_declarations_are_refused(
    field_name: str, replacement: object
) -> None:
    values = {
        field.name: getattr(_snapshot(), field.name)
        for field in fields(CapabilityContractSnapshot)
    }
    values[field_name] = replacement

    with pytest.raises(CapabilityContractError, match="unique"):
        CapabilityContractSnapshot(**values)


@pytest.mark.parametrize(
    ("owner_code", "capability_code", "schema_version"),
    [
        ("Example", "synthetic.valid", 1),
        ("example", "synthetic/provider", 1),
        ("example", "synthetic.valid", 0),
        ("example", "synthetic.valid", True),
    ],
)
def test_identity_has_provider_neutral_stable_code_grammar(
    owner_code: str, capability_code: str, schema_version: object
) -> None:
    with pytest.raises(CapabilityContractError):
        CapabilityContractSnapshot(
            owner_code=owner_code,
            capability_code=capability_code,
            schema_version=schema_version,  # type: ignore[arg-type]
            operations=(_operation("read"),),
        )


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: _operation("Bad Operation"),
        lambda: CapabilityConfigField(
            field_code="bad/value", value_type=CapabilityConfigValueType.STRING
        ),
        lambda: CapabilityEndpointRequirement(
            endpoint_code="bad value",
            endpoint_type=CapabilityEndpointType.FQDN,
            operation_codes=("read",),
        ),
        lambda: CapabilityCheck(
            check_code="BAD",
            stage=CapabilityCheckStage.ACTIVATION,
            evidence_type=CapabilityEvidenceType.BOOLEAN,
        ),
    ],
)
def test_nested_codes_use_the_same_provider_neutral_grammar(constructor) -> None:
    with pytest.raises(CapabilityContractError):
        constructor()


def test_snapshot_cross_checks_the_owning_product_manifest() -> None:
    snapshot = _snapshot()
    manifest = ProductManifestSnapshot(
        product_code="example-product",
        product_version="2.3.4",
        capability_codes=("synthetic.lifecycle.v1",),
    )

    snapshot.require_declared_by(manifest)

    wrong_owner = ProductManifestSnapshot(
        product_code="other-product",
        product_version="2.3.4",
        capability_codes=("synthetic.lifecycle.v1",),
    )
    with pytest.raises(CapabilityContractError, match="owner_code"):
        snapshot.require_declared_by(wrong_owner)

    missing = ProductManifestSnapshot(
        product_code="example-product",
        product_version="2.3.4",
        capability_codes=(),
    )
    with pytest.raises(KeyError, match="does not declare capability"):
        snapshot.require_declared_by(missing)


def test_parse_requires_exact_canonical_bytes_and_optional_digest() -> None:
    original = _snapshot()

    assert (
        CapabilityContractSnapshot.from_json_bytes(
            original.to_json_bytes(), expected_digest=original.digest
        )
        == original
    )

    pretty = original.to_json_bytes().replace(b',"owner_code"', b', "owner_code"')
    with pytest.raises(CapabilityContractError, match="canonical"):
        CapabilityContractSnapshot.from_json_bytes(pretty)

    with pytest.raises(CapabilityContractDigestMismatchError, match="does not match"):
        CapabilityContractSnapshot.from_json_bytes(
            original.to_json_bytes(), expected_digest="sha256:" + "0" * 64
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(b'"schema_version":1', b'"schema_version":0'),
        lambda payload: payload.replace(
            b'"value_type":"reference"', b'"value_type":"mystery"'
        ),
        lambda payload: payload.replace(
            b'"endpoint_type":"fqdn"', b'"endpoint_type":"mystery"'
        ),
        lambda payload: payload.replace(b'"stage":"evidence"', b'"stage":"mystery"'),
        lambda payload: payload.replace(
            b'"evidence_type":"document"', b'"evidence_type":"mystery"'
        ),
        lambda payload: payload.replace(b'"schema":', b'"unknown":true,"schema":'),
    ],
)
def test_parse_refuses_unknown_or_invalid_document_shapes(mutation) -> None:
    with pytest.raises(CapabilityContractError):
        CapabilityContractSnapshot.from_json_bytes(
            mutation(_snapshot().to_json_bytes())
        )


def _schema_document() -> CapabilitySchemaDocument:
    return CapabilitySchemaDocument.from_mapping(
        {
            "$id": "schema:synthetic.apply.request@v1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {
                "target_ref": {"type": "string"},
                "target": {
                    "$ref": "#/$defs/target",
                    "x-dotmac-data-classification": "public_non_secret",
                },
            },
            "required": ["target", "target_ref"],
            "$defs": {
                "target": {
                    "additionalProperties": False,
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                    "type": "object",
                }
            },
            "type": "object",
        }
    )


def test_capability_schema_document_is_exact_canonical_held_evidence() -> None:
    schema = _schema_document()

    assert schema.schema_ref == "schema:synthetic.apply.request@v1"
    assert schema.digest.startswith("sha256:")
    assert schema.to_json_bytes() == (
        b'{"$defs":{"target":{"additionalProperties":false,'
        b'"properties":{"enabled":{"type":"boolean"}},'
        b'"required":["enabled"],"type":"object"}},'
        b'"$id":"schema:synthetic.apply.request@v1",'
        b'"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"additionalProperties":false,"properties":{"target":'
        b'{"$ref":"#/$defs/target","x-dotmac-data-classification":'
        b'"public_non_secret"},"target_ref":{"type":"string"}},'
        b'"required":["target","target_ref"],"type":"object"}'
    )
    assert (
        CapabilitySchemaDocument.from_json_bytes(
            schema.to_json_bytes(),
            expected_ref=schema.schema_ref,
            expected_digest=schema.digest,
        )
        == schema
    )


def test_capability_schema_ref_and_digest_are_independently_verified() -> None:
    schema = _schema_document()

    with pytest.raises(CapabilityContractError, match="schema reference"):
        CapabilitySchemaDocument.from_json_bytes(
            schema.to_json_bytes(), expected_ref="schema:synthetic.other@v1"
        )
    with pytest.raises(CapabilitySchemaDigestMismatchError, match="does not match"):
        CapabilitySchemaDocument.from_json_bytes(
            schema.to_json_bytes(), expected_digest="sha256:" + "0" * 64
        )


def test_capability_schema_resolves_only_declared_non_secret_instance_paths() -> None:
    schema = _schema_document()

    target = schema.require_instance_pointer("/target")
    enabled = schema.require_instance_pointer("/target/enabled")

    assert target["x-dotmac-data-classification"] == "public_non_secret"
    assert enabled["type"] == "boolean"
    schema.require_public_non_secret_pointer("/target")

    with pytest.raises(CapabilityContractError, match="not public_non_secret"):
        schema.require_public_non_secret_pointer("/target_ref")
    with pytest.raises(CapabilityContractError, match="does not declare"):
        schema.require_instance_pointer("/invented")
    with pytest.raises(CapabilityContractError, match="JSON pointer"):
        schema.require_instance_pointer("target")


def test_capability_schema_copies_only_public_evidence_from_a_fresh_document() -> None:
    schema = _schema_document()
    first = dict(schema.to_mapping())
    first["type"] = "string"

    assert schema.to_mapping()["type"] == "object"
    assert (
        schema.instance_value_at(
            {"target": {"enabled": True}, "target_ref": "internal"},
            "/target/enabled",
        )
        is True
    )
    assert schema.public_non_secret_projection(
        {"target": {"enabled": True}, "target_ref": "internal"}
    ) == {"target": {"enabled": True}}
    with pytest.raises(CapabilityContractError, match="does not contain"):
        schema.instance_value_at({"target": {}}, "/target/enabled")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"$id":"schema:synthetic.apply.request@v1",'
        b'"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"type":"object","type":"string"}',
        b'{"$id":"schema:synthetic.apply.request@v1",'
        b'"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"maximum":NaN,"type":"number"}',
        b'{"$id":"schema:synthetic.apply.request@v1",'
        b'"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"properties":{"x":{"$ref":"https://example.com/remote.json"}},'
        b'"type":"object"}',
        b'{"$id":"schema:synthetic.apply.request@v1",'
        b'"$schema":"http://json-schema.org/draft-07/schema#",'
        b'"type":"object"}',
    ],
)
def test_capability_schema_refuses_ambiguous_or_remote_documents(
    payload: bytes,
) -> None:
    with pytest.raises(CapabilityContractError):
        CapabilitySchemaDocument.from_json_bytes(payload)


def test_capability_schema_refuses_noncanonical_bytes() -> None:
    pretty = _schema_document().to_json_bytes().replace(b',"type"', b', "type"')

    with pytest.raises(CapabilityContractError, match="canonical"):
        CapabilitySchemaDocument.from_json_bytes(pretty)


def test_snapshot_and_all_nested_contracts_are_frozen_and_slotted() -> None:
    snapshot = _snapshot()

    for contract_type in (
        CapabilityContractSnapshot,
        CapabilityOperation,
        CapabilityConfigField,
        CapabilityEndpointRequirement,
        CapabilityCheck,
    ):
        assert hasattr(contract_type, "__slots__")
    assert isinstance(snapshot.operations, tuple)
    assert isinstance(snapshot.config_fields, tuple)
    assert isinstance(snapshot.endpoint_requirements, tuple)
    assert isinstance(snapshot.checks, tuple)
    with pytest.raises(FrozenInstanceError):
        snapshot.owner_code = "different"  # type: ignore[misc]


def test_schema_contains_no_provider_binding_or_configuration_values() -> None:
    assert {field.name for field in fields(CapabilityContractSnapshot)} == {
        "owner_code",
        "capability_code",
        "schema_version",
        "operations",
        "config_fields",
        "endpoint_requirements",
        "checks",
    }
    forbidden = {
        "provider",
        "provider_code",
        "connector",
        "connector_code",
        "configuration",
        "secret",
        "secret_value",
    }
    assert not forbidden & {field.name for field in fields(CapabilityContractSnapshot)}
