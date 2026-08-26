"""Capability composition binds approved dataflow without runtime values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from dotmac_kernel import (
    CAPABILITY_COMPOSITION_SCHEMA,
    CapabilityCompositionDigestMismatchError,
    CapabilityCompositionError,
    CapabilityCompositionSnapshot,
    CapabilityContractSnapshot,
    CapabilityEvidenceBinding,
    CapabilityOperation,
    CapabilitySchemaDocument,
)


def _schema(ref: str, field: str, *, public: bool = True) -> CapabilitySchemaDocument:
    field_schema: dict[str, object] = {"type": "string"}
    if public:
        field_schema["x-dotmac-data-classification"] = "public_non_secret"
    return CapabilitySchemaDocument.from_mapping(
        {
            "$id": ref,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {field: field_schema},
            "required": [field],
            "type": "object",
        }
    )


SOURCE_SCHEMA = _schema("schema:managed-mail.apply.result@v1", "dkim_txt")
TARGET_SCHEMA = _schema(
    "schema:managed-dns.apply.request@v1", "record_value", public=False
)
SOURCE_INPUT = _schema("schema:managed-mail.apply.request@v1", "domain")
TARGET_OUTPUT = _schema("schema:managed-dns.apply.result@v1", "recordset_ref")


def _contract(
    owner: str,
    capability: str,
    input_schema: CapabilitySchemaDocument,
    output_schema: CapabilitySchemaDocument,
) -> CapabilityContractSnapshot:
    return CapabilityContractSnapshot(
        owner_code=owner,
        capability_code=capability,
        schema_version=1,
        operations=(
            CapabilityOperation(
                operation_code="apply",
                input_schema_ref=input_schema.schema_ref,
                input_schema_digest=input_schema.digest,
                output_schema_ref=output_schema.schema_ref,
                output_schema_digest=output_schema.digest,
            ),
        ),
    )


def _discriminated_schema(
    ref: str,
    *,
    value_field: str,
    resource_kinds: tuple[str, ...],
    public_value: bool,
) -> CapabilitySchemaDocument:
    value_schema: dict[str, object] = {"type": "string"}
    if public_value:
        value_schema["x-dotmac-data-classification"] = "public_non_secret"
    return CapabilitySchemaDocument.from_mapping(
        {
            "$id": ref,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {
                "resource_kind": {"enum": list(resource_kinds), "type": "string"},
                value_field: value_schema,
            },
            "required": ["resource_kind", value_field],
            "type": "object",
        }
    )


SOURCE_CONTRACT = _contract(
    "dotmac-managed-email",
    "email.domain.lifecycle",
    SOURCE_INPUT,
    SOURCE_SCHEMA,
)
TARGET_CONTRACT = _contract(
    "dotmac-domains",
    "dns.authoritative",
    TARGET_SCHEMA,
    TARGET_OUTPUT,
)


def _binding() -> CapabilityEvidenceBinding:
    return CapabilityEvidenceBinding(
        binding_code="mail-dkim-to-dns-record",
        source_owner_code=SOURCE_CONTRACT.owner_code,
        source_capability_code=SOURCE_CONTRACT.capability_code,
        source_capability_schema_version=1,
        source_operation_code="apply",
        source_output_schema_ref=SOURCE_SCHEMA.schema_ref,
        source_output_schema_digest=SOURCE_SCHEMA.digest,
        source_pointer="/dkim_txt",
        target_owner_code=TARGET_CONTRACT.owner_code,
        target_capability_code=TARGET_CONTRACT.capability_code,
        target_capability_schema_version=1,
        target_operation_code="apply",
        target_input_schema_ref=TARGET_SCHEMA.schema_ref,
        target_input_schema_digest=TARGET_SCHEMA.digest,
        target_pointer="/record_value",
        coverage="each_source_exactly_one",
        required=True,
    )


def _composition() -> CapabilityCompositionSnapshot:
    return CapabilityCompositionSnapshot(
        owner_code="dotmac-managed-suite",
        composition_code="managed-suite.mail-dns.v1",
        schema_version=1,
        evidence_bindings=(_binding(),),
    )


def test_composition_is_canonical_value_free_and_exactly_verifiable() -> None:
    composition = _composition()

    assert composition.schema == CAPABILITY_COMPOSITION_SCHEMA
    assert composition.identity == (
        "dotmac-managed-suite",
        "managed-suite.mail-dns.v1",
        1,
    )
    assert composition.digest.startswith("sha256:")
    assert b"dkim_txt" in composition.to_json_bytes()
    assert b"record_value" in composition.to_json_bytes()
    assert b"v=DKIM1" not in composition.to_json_bytes()
    assert (
        CapabilityCompositionSnapshot.from_json_bytes(
            composition.to_json_bytes(), expected_digest=composition.digest
        )
        == composition
    )


def test_composition_cross_checks_contracts_and_schema_documents() -> None:
    _composition().require_compatible_with(
        contracts=(SOURCE_CONTRACT, TARGET_CONTRACT),
        schemas=(SOURCE_SCHEMA, TARGET_SCHEMA),
    )

    with pytest.raises(CapabilityCompositionError, match="source schema digest"):
        _composition().require_compatible_with(
            contracts=(SOURCE_CONTRACT, TARGET_CONTRACT),
            schemas=(
                _schema(SOURCE_SCHEMA.schema_ref, "different"),
                TARGET_SCHEMA,
            ),
        )


def test_instance_selectors_restrict_same_capability_resource_instances() -> None:
    source_input = _discriminated_schema(
        SOURCE_INPUT.schema_ref,
        value_field="domain",
        resource_kinds=("application", "domain", "mailbox"),
        public_value=False,
    )
    target_input = _discriminated_schema(
        TARGET_SCHEMA.schema_ref,
        value_field="record_value",
        resource_kinds=("recordset", "zone"),
        public_value=False,
    )
    source_contract = _contract(
        SOURCE_CONTRACT.owner_code,
        SOURCE_CONTRACT.capability_code,
        source_input,
        SOURCE_SCHEMA,
    )
    target_contract = _contract(
        TARGET_CONTRACT.owner_code,
        TARGET_CONTRACT.capability_code,
        target_input,
        TARGET_OUTPUT,
    )
    binding = replace(
        _binding(),
        source_selector_pointer="/resource_kind",
        source_selector_value="domain",
        target_input_schema_digest=target_input.digest,
        target_selector_pointer="/resource_kind",
        target_selector_value="recordset",
    )
    composition = CapabilityCompositionSnapshot(
        owner_code="dotmac-managed-suite",
        composition_code="managed-suite.mail-dns.v1",
        schema_version=1,
        evidence_bindings=(binding,),
    )

    composition.require_compatible_with(
        contracts=(source_contract, target_contract),
        schemas=(SOURCE_SCHEMA, source_input, target_input, TARGET_OUTPUT),
    )
    parsed = CapabilityCompositionSnapshot.from_json_bytes(
        composition.to_json_bytes(), expected_digest=composition.digest
    )
    assert parsed.evidence_bindings[0].source_selector_value == "domain"
    assert parsed.evidence_bindings[0].target_selector_value == "recordset"

    invalid = replace(binding, target_selector_value="mailbox")
    with pytest.raises(CapabilityCompositionError, match="is not admitted"):
        CapabilityCompositionSnapshot(
            owner_code="dotmac-managed-suite",
            composition_code="managed-suite.mail-dns.v1",
            schema_version=1,
            evidence_bindings=(invalid,),
        ).require_compatible_with(
            contracts=(source_contract, target_contract),
            schemas=(SOURCE_SCHEMA, source_input, target_input, TARGET_OUTPUT),
        )


def test_a_selector_still_requires_the_source_input_schema_to_be_held() -> None:
    """Lazy resolution must not skip the schema a source selector reads."""
    source_input = _discriminated_schema(
        SOURCE_INPUT.schema_ref,
        value_field="domain",
        resource_kinds=("domain", "mailbox"),
        public_value=False,
    )
    source_contract = _contract(
        SOURCE_CONTRACT.owner_code,
        SOURCE_CONTRACT.capability_code,
        source_input,
        SOURCE_SCHEMA,
    )
    binding = replace(
        _binding(),
        source_selector_pointer="/resource_kind",
        source_selector_value="domain",
    )
    composition = CapabilityCompositionSnapshot(
        owner_code="dotmac-managed-suite",
        composition_code="managed-suite.mail-dns.v1",
        schema_version=1,
        evidence_bindings=(binding,),
    )

    with pytest.raises(CapabilityCompositionError, match="source input"):
        composition.require_compatible_with(
            contracts=(source_contract, TARGET_CONTRACT),
            # `source_input` deliberately withheld.
            schemas=(SOURCE_SCHEMA, TARGET_SCHEMA),
        )

    composition.require_compatible_with(
        contracts=(source_contract, TARGET_CONTRACT),
        schemas=(SOURCE_SCHEMA, source_input, TARGET_SCHEMA),
    )


def test_composition_refuses_secret_or_undeclared_evidence_paths() -> None:
    secret_source = CapabilitySchemaDocument.from_mapping(
        {
            "$id": SOURCE_SCHEMA.schema_ref,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "dkim_txt": {
                    "type": "string",
                    "x-dotmac-data-classification": "secret",
                }
            },
            "type": "object",
        }
    )
    secret_contract = _contract(
        SOURCE_CONTRACT.owner_code,
        SOURCE_CONTRACT.capability_code,
        SOURCE_INPUT,
        secret_source,
    )
    binding_values = _binding().__dict__ if hasattr(_binding(), "__dict__") else None
    assert binding_values is None  # slotted: no mutable document escape hatch
    secret_binding = CapabilityEvidenceBinding(
        **{
            field: getattr(_binding(), field)
            for field in _binding().__dataclass_fields__
            if field not in {"source_output_schema_digest"}
        },
        source_output_schema_digest=secret_source.digest,
    )
    composition = CapabilityCompositionSnapshot(
        owner_code="dotmac-managed-suite",
        composition_code="managed-suite.mail-dns.v1",
        schema_version=1,
        evidence_bindings=(secret_binding,),
    )
    with pytest.raises(CapabilityCompositionError, match="public_non_secret"):
        composition.require_compatible_with(
            contracts=(secret_contract, TARGET_CONTRACT),
            schemas=(secret_source, TARGET_SCHEMA),
        )


def test_composition_refuses_digest_drift_order_drift_and_mutation() -> None:
    original = _composition()
    with pytest.raises(CapabilityCompositionDigestMismatchError):
        CapabilityCompositionSnapshot.from_json_bytes(
            original.to_json_bytes(), expected_digest="sha256:" + "0" * 64
        )
    with pytest.raises(CapabilityCompositionError, match="canonical"):
        CapabilityCompositionSnapshot.from_json_bytes(
            original.to_json_bytes().replace(b',"owner_code"', b', "owner_code"')
        )
    with pytest.raises(FrozenInstanceError):
        original.schema_version = 2  # type: ignore[misc]


def test_composition_requires_an_exact_instance_coverage_axis() -> None:
    assert _binding().coverage == "each_source_exactly_one"
    with pytest.raises(CapabilityCompositionError, match="coverage must be"):
        replace(_binding(), coverage="caller_selected")
    with pytest.raises(CapabilityCompositionError, match="bindings are required"):
        replace(_binding(), required=False)
