"""Owner-contract admission for SPI 1.2 provisioning connectors.

Every refusal has a sensitivity case.  The legacy digest assertion is equally
load-bearing: adding the new contract to PROVISION must not silently invalidate
an installation pinned to a <=1.1 connector that never declared that mode.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    add_binding,
    create_draft,
    put_config_revision,
    set_binding_enabled,
)
from dotmac_integration.conformance import fake_plugin, fake_registry
from dotmac_integration.secret_refs import (
    CapabilityConfigurationError,
    verify_capability_configuration,
)
from dotmac_integration.spi import (
    CapabilityDeclaration,
    ConnectorManifest,
    ModeContractError,
    SpiRange,
    verify_plugin_modes,
)
from dotmac_kernel import (
    CapabilityCheck,
    CapabilityCheckStage,
    CapabilityConfigField,
    CapabilityConfigValueFormat,
    CapabilityConfigValueType,
    CapabilityContractSnapshot,
    CapabilityEndpointRequirement,
    CapabilityEndpointType,
    CapabilityEvidenceType,
    CapabilityOperation,
    CapabilitySchemaDocument,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

CAPABILITY = "testing.provision.v1"
ARTIFACT_DIGEST = "sha256:" + "a" * 64


def _schema(
    code: str,
    direction: str,
    *,
    public_property: str = "public_ref",
) -> CapabilitySchemaDocument:
    return CapabilitySchemaDocument.from_mapping(
        {
            "$id": f"schema:testing/{code}-{direction}@v1",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {
                public_property: {
                    "type": "string",
                    "x-dotmac-data-classification": "public_non_secret",
                },
                "private_note": {
                    "type": "string",
                    "x-dotmac-data-classification": "secret",
                },
            },
            "type": "object",
        }
    )


def _operation(code: str, *, salt: str = "1") -> CapabilityOperation:
    input_schema = _schema(code, "input", public_property=f"input_{salt}")
    output_schema = _schema(code, "output", public_property=f"output_{salt}")
    return CapabilityOperation(
        operation_code=code,
        input_schema_ref=input_schema.schema_ref,
        input_schema_digest=input_schema.digest,
        output_schema_ref=output_schema.schema_ref,
        output_schema_digest=output_schema.digest,
    )


def _snapshot(
    *,
    operations: tuple[CapabilityOperation, ...] | None = None,
    config_fields: tuple[CapabilityConfigField, ...] = (),
    endpoints: tuple[CapabilityEndpointRequirement, ...] = (),
    checks: tuple[CapabilityCheck, ...] = (),
) -> CapabilityContractSnapshot:
    return CapabilityContractSnapshot(
        owner_code="testing-owner",
        capability_code=CAPABILITY.rsplit(".v", 1)[0],
        schema_version=1,
        operations=operations
        or tuple(_operation(code) for code in ("apply", "cancel", "observe", "plan")),
        config_fields=config_fields,
        endpoint_requirements=endpoints,
        checks=checks,
    )


def _declaration(
    snapshot: CapabilityContractSnapshot | None = None,
) -> CapabilityDeclaration:
    contract = snapshot or _snapshot()
    schemas = tuple(
        schema
        for operation in contract.operations
        for schema in (
            _schema(
                operation.operation_code,
                "input",
                public_property=(
                    "input_2"
                    if operation.input_schema_digest
                    == _schema(
                        operation.operation_code, "input", public_property="input_2"
                    ).digest
                    else "input_1"
                ),
            ),
            _schema(
                operation.operation_code,
                "output",
                public_property=(
                    "output_2"
                    if operation.output_schema_digest
                    == _schema(
                        operation.operation_code, "output", public_property="output_2"
                    ).digest
                    else "output_1"
                ),
            ),
        )
    )
    return CapabilityDeclaration(
        capability_id=CAPABILITY,
        contract_snapshot=contract,
        schema_documents=schemas,
    )


def _manifest(declaration: CapabilityDeclaration) -> ConnectorManifest:
    return ConnectorManifest(
        connector_key="owner_contract_test",
        version="1.0.0",
        spi_range=SpiRange.parse(">=1.2,<2.0"),
        capabilities=(declaration,),
    )


def test_legacy_manifest_digest_and_declaration_shape_are_unchanged() -> None:
    declaration = CapabilityDeclaration(capability_id="legacy.delivery.v1")
    manifest = ConnectorManifest(
        connector_key="legacy_delivery",
        version="1.4.2",
        spi_range=SpiRange.parse(">=1.0,<2.0"),
        capabilities=(declaration,),
    )
    old_material = "legacy_delivery|1.4.2|>=1.0,<2.0|legacy.delivery.v1"

    assert declaration.contract_snapshot is None
    assert manifest.digest == hashlib.sha256(old_material.encode()).hexdigest()


def test_provision_mode_refuses_a_capability_without_an_owner_contract() -> None:
    plugin = fake_plugin(
        manifest_=_manifest(CapabilityDeclaration(capability_id=CAPABILITY))
    )

    with pytest.raises(ModeContractError, match="owner contract"):
        verify_plugin_modes(plugin)


def test_owner_contract_requires_exact_held_schema_coverage() -> None:
    contract = _snapshot()
    schemas = _declaration(contract).schema_documents

    with pytest.raises(Exception, match="exactly cover"):
        CapabilityDeclaration(
            capability_id=CAPABILITY,
            contract_snapshot=contract,
            schema_documents=schemas[:-1],
        )
    with pytest.raises(Exception, match="exactly cover"):
        CapabilityDeclaration(
            capability_id=CAPABILITY,
            contract_snapshot=contract,
            schema_documents=(*schemas, schemas[0]),
        )


def test_owner_contract_refuses_a_schema_that_is_not_draft_2020_12_valid() -> None:
    declaration = _declaration()
    contract = declaration.contract_snapshot
    assert contract is not None
    operation = next(
        item for item in contract.operations if item.operation_code == "apply"
    )
    schema = declaration.require_schema(
        operation.input_schema_ref,
        operation.input_schema_digest,
    )
    invalid_mapping = dict(schema.to_mapping())
    invalid_mapping["type"] = "provider-specific-object"
    invalid_schema = CapabilitySchemaDocument.from_mapping(invalid_mapping)
    invalid_operation = replace(
        operation,
        input_schema_digest=invalid_schema.digest,
    )
    invalid_contract = replace(
        contract,
        operations=tuple(
            invalid_operation if item.operation_code == "apply" else item
            for item in contract.operations
        ),
    )

    with pytest.raises(Exception, match="Draft 2020-12"):
        CapabilityDeclaration(
            capability_id=CAPABILITY,
            contract_snapshot=invalid_contract,
            schema_documents=tuple(
                sorted(
                    (
                        invalid_schema if item.schema_ref == schema.schema_ref else item
                        for item in declaration.schema_documents
                    ),
                    key=lambda item: item.schema_ref,
                )
            ),
        )


def test_manifest_digest_binds_exact_schema_bytes() -> None:
    declaration = _declaration()
    changed_schema = _schema("apply", "output", public_property="changed_public")
    changed_operation = replace(
        declaration.contract_snapshot.operations[0],
        output_schema_digest=changed_schema.digest,
    )
    changed_contract = replace(
        declaration.contract_snapshot,
        operations=(changed_operation, *declaration.contract_snapshot.operations[1:]),
    )
    changed_documents = tuple(
        changed_schema if item.schema_ref == changed_schema.schema_ref else item
        for item in declaration.schema_documents
    )
    changed = CapabilityDeclaration(
        capability_id=CAPABILITY,
        contract_snapshot=changed_contract,
        schema_documents=changed_documents,
    )

    assert _manifest(changed).digest != _manifest(declaration).digest


def test_provision_mode_refuses_an_owner_contract_missing_an_engine_operation() -> None:
    incomplete = _snapshot(
        operations=tuple(_operation(code) for code in ("apply", "observe", "plan"))
    )
    plugin = fake_plugin(manifest_=_manifest(_declaration(incomplete)))

    with pytest.raises(ModeContractError, match="cancel"):
        verify_plugin_modes(plugin)


def test_declaration_refuses_a_contract_for_a_different_capability() -> None:
    other = replace(_snapshot(), capability_code="testing.other")

    with pytest.raises(Exception, match="does not match"):
        CapabilityDeclaration(capability_id=CAPABILITY, contract_snapshot=other)


def test_manifest_digest_binds_every_owner_contract_region() -> None:
    base = _snapshot()
    variants = (
        replace(
            base,
            operations=(
                _operation("apply", salt="2"),
                *base.operations[1:],
            ),
        ),
        replace(
            base,
            config_fields=(
                CapabilityConfigField(
                    "label", CapabilityConfigValueType.STRING, required=False
                ),
            ),
        ),
        replace(
            base,
            endpoint_requirements=(
                CapabilityEndpointRequirement(
                    "service-url",
                    CapabilityEndpointType.HTTPS_URL,
                    ("apply",),
                    required=False,
                ),
            ),
        ),
        replace(
            base,
            checks=(
                CapabilityCheck(
                    "reachable",
                    CapabilityCheckStage.ACTIVATION,
                    CapabilityEvidenceType.BOOLEAN,
                ),
            ),
        ),
    )
    digest = _manifest(_declaration(base)).digest

    assert all(
        _manifest(_declaration(variant)).digest != digest for variant in variants
    )


def _typed_snapshot() -> CapabilityContractSnapshot:
    return _snapshot(
        config_fields=(
            CapabilityConfigField("account-ref", CapabilityConfigValueType.REFERENCE),
            CapabilityConfigField("active", CapabilityConfigValueType.BOOLEAN),
            CapabilityConfigField(
                "admin-email",
                CapabilityConfigValueType.STRING,
                CapabilityConfigValueFormat.EMAIL_ADDRESS,
            ),
            CapabilityConfigField(
                "callback-url",
                CapabilityConfigValueType.STRING,
                CapabilityConfigValueFormat.HTTPS_URL,
            ),
            CapabilityConfigField(
                "code",
                CapabilityConfigValueType.STRING,
                CapabilityConfigValueFormat.STABLE_CODE,
            ),
            CapabilityConfigField(
                "domains",
                CapabilityConfigValueType.STRING_LIST,
                CapabilityConfigValueFormat.FQDN_LIST,
            ),
            CapabilityConfigField(
                "hostname",
                CapabilityConfigValueType.STRING,
                CapabilityConfigValueFormat.FQDN,
            ),
            CapabilityConfigField(
                "quota",
                CapabilityConfigValueType.INTEGER,
                CapabilityConfigValueFormat.NONNEGATIVE_INTEGER,
            ),
            CapabilityConfigField("ratio", CapabilityConfigValueType.DECIMAL),
            CapabilityConfigField(
                "secret-ref", CapabilityConfigValueType.SECRET_REFERENCE
            ),
        ),
        endpoints=(
            CapabilityEndpointRequirement(
                "admin-url",
                CapabilityEndpointType.HTTPS_URL,
                ("apply", "plan"),
            ),
            CapabilityEndpointRequirement(
                "peer",
                CapabilityEndpointType.HOST_PORT,
                ("observe",),
                required=False,
            ),
        ),
        checks=(
            CapabilityCheck(
                "reachable",
                CapabilityCheckStage.ACTIVATION,
                CapabilityEvidenceType.BOOLEAN,
            ),
        ),
    )


def _valid_config() -> dict[str, object]:
    return {
        "account-ref": "account-7",
        "active": True,
        "admin-email": "operator@example.test",
        "admin-url": "https://admin.example.test/api",
        "callback-url": "https://service.example.test/callback",
        "code": "region-1",
        "domains": ["one.example.test", "two.example.test"],
        "hostname": "service.example.test",
        "quota": 0,
        "ratio": 1.5,
    }


def test_generic_configuration_verifier_returns_exact_contract_evidence() -> None:
    declaration = _declaration(_typed_snapshot())

    verified = verify_capability_configuration(
        declaration,
        config=_valid_config(),
        secret_refs={"secret-ref": "bao://path/to/held-material"},
        required_operation_codes=("apply", "plan"),
    )

    assert verified.owner_code == "testing-owner"
    assert verified.capability_code == CAPABILITY.rsplit(".v", 1)[0]
    assert verified.schema_version == 1
    assert declaration.contract_snapshot is not None
    assert verified.contract_digest == declaration.contract_snapshot.digest
    assert verified.operation_codes == ("apply", "cancel", "observe", "plan")
    assert verified.activation_check_codes == ("reachable",)
    assert "held-material" not in repr(verified)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"active": 1}, "active"),
        ({"admin-email": "not-an-email"}, "admin-email"),
        ({"callback-url": "http://service.example.test"}, "callback-url"),
        ({"code": "Not Stable"}, "code"),
        ({"domains": ["BAD.example.test"]}, "domains"),
        ({"hostname": "https://service.example.test"}, "hostname"),
        ({"quota": -1}, "quota"),
        ({"ratio": float("inf")}, "ratio"),
        ({"admin-url": "https:///missing-host"}, "admin-url"),
        ({"peer": "example.test:not-a-port"}, "peer"),
        ({"invented-field": "x"}, "invented-field"),
    ],
)
def test_each_declared_type_format_and_unknown_field_refusal_bites(
    change: dict[str, object], message: str
) -> None:
    config = _valid_config()
    config.update(change)

    with pytest.raises(CapabilityConfigurationError, match=message):
        verify_capability_configuration(
            _declaration(_typed_snapshot()),
            config=config,
            secret_refs={"secret-ref": "bao://held"},
        )


def test_secret_reference_ownership_and_errors_never_render_material() -> None:
    declaration = _declaration(_typed_snapshot())
    leaked = "literal-super-secret-value"

    with pytest.raises(CapabilityConfigurationError) as excinfo:
        verify_capability_configuration(
            declaration,
            config=_valid_config(),
            secret_refs={"secret-ref": leaked},
        )

    rendered = str(excinfo.value) + repr(excinfo.value)
    assert leaked not in rendered

    config = _valid_config()
    config["secret-ref"] = "bao://wrong-plane"
    with pytest.raises(CapabilityConfigurationError, match="secret-ref"):
        verify_capability_configuration(
            declaration,
            config=config,
            secret_refs={"secret-ref": "bao://held"},
        )


def test_missing_required_and_undeclared_operation_refusals_bite() -> None:
    config = _valid_config()
    del config["hostname"]
    declaration = _declaration(_typed_snapshot())

    with pytest.raises(CapabilityConfigurationError, match="hostname"):
        verify_capability_configuration(
            declaration,
            config=config,
            secret_refs={"secret-ref": "bao://held"},
        )

    with pytest.raises(CapabilityConfigurationError, match="invented"):
        verify_capability_configuration(
            declaration,
            config=_valid_config(),
            secret_refs={"secret-ref": "bao://held"},
            required_operation_codes=("invented",),
        )


def test_binding_activation_enforces_the_exact_contract_before_state_mutation() -> None:
    declaration = _declaration(_typed_snapshot())
    registry = fake_registry(plugins=[fake_plugin(manifest_=_manifest(declaration))])
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (ConnectorInstallation, ConnectorConfigRevision, CapabilityBinding):
        model.__table__.create(engine)  # type: ignore[attr-defined]

    with Session(engine) as db:
        installation = create_draft(
            db,
            registry=registry,
            connector_key="owner_contract_test",
            name="test",
            connector_artifact_digest=ARTIFACT_DIGEST,
        )
        put_config_revision(
            db,
            installation,
            config={"invented-field": "not in the product contract"},
        )
        binding = add_binding(
            db,
            installation,
            registry=registry,
            capability_id=CAPABILITY,
            capability_instance_ref="primary",
        )

        with pytest.raises(CapabilityConfigurationError, match="invented-field"):
            set_binding_enabled(
                db,
                installation,
                binding,
                registry=registry,
                enabled=True,
            )

        assert binding.state == "disabled"
        assert binding.enabled_at is None


def test_provision_activation_requires_a_release_catalog_artifact_pin() -> None:
    registry = fake_registry(plugins=[fake_plugin(manifest_=_manifest(_declaration()))])
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (ConnectorInstallation, ConnectorConfigRevision, CapabilityBinding):
        model.__table__.create(engine)  # type: ignore[attr-defined]

    with Session(engine) as db:
        installation = create_draft(
            db,
            registry=registry,
            connector_key="owner_contract_test",
            name="test",
        )
        put_config_revision(db, installation, config={})
        binding = add_binding(
            db,
            installation,
            registry=registry,
            capability_id=CAPABILITY,
            capability_instance_ref="primary",
        )

        with pytest.raises(Exception, match="Release Catalog connector artifact"):
            set_binding_enabled(
                db,
                installation,
                binding,
                registry=registry,
                enabled=True,
            )

        assert binding.state == "disabled"


def test_draft_validates_and_config_changes_preserve_the_artifact_pin() -> None:
    registry = fake_registry(plugins=[fake_plugin(manifest_=_manifest(_declaration()))])
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_intg": None}},
    )
    for model in (ConnectorInstallation, ConnectorConfigRevision):
        model.__table__.create(engine)  # type: ignore[attr-defined]

    with Session(engine) as db:
        with pytest.raises(Exception, match="64 lowercase hex"):
            create_draft(
                db,
                registry=registry,
                connector_key="owner_contract_test",
                name="invalid",
                connector_artifact_digest="sha256:NOT-CANONICAL",
            )

        installation = create_draft(
            db,
            registry=registry,
            connector_key="owner_contract_test",
            name="valid",
            connector_artifact_digest=ARTIFACT_DIGEST,
        )
        put_config_revision(db, installation, config={})

        assert installation.connector_artifact_digest == ARTIFACT_DIGEST
