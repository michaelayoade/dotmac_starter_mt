"""Provider-neutral managed infrastructure contract catalogue."""

from __future__ import annotations

from dotmac_kernel import (
    CapabilityCheck,
    CapabilityCheckStage,
    CapabilityCompositionSnapshot,
    CapabilityConfigField,
    CapabilityConfigValueFormat,
    CapabilityConfigValueType,
    CapabilityContractSnapshot,
    CapabilityEndpointRequirement,
    CapabilityEndpointType,
    CapabilityEvidenceType,
    CapabilityOperation,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)

from .schemas import CAPABILITY_SCHEMAS, SCHEMAS_BY_REF

OWNER_CODE = "dotmac-managed-infrastructure"
VERSION = "0.1.0a1"
_OPERATION_CODES = ("apply", "cancel", "observe", "plan")


def _operation(capability_path: str, operation_code: str) -> CapabilityOperation:
    prefix = f"schema:{OWNER_CODE}/{capability_path}/{operation_code}"
    input_schema = SCHEMAS_BY_REF[f"{prefix}/input@v1"]
    output_schema = SCHEMAS_BY_REF[f"{prefix}/output@v1"]
    return CapabilityOperation(
        operation_code=operation_code,
        input_schema_ref=input_schema.schema_ref,
        input_schema_digest=input_schema.digest,
        output_schema_ref=output_schema.schema_ref,
        output_schema_digest=output_schema.digest,
    )


def _operations(capability_path: str) -> tuple[CapabilityOperation, ...]:
    return tuple(_operation(capability_path, code) for code in _OPERATION_CODES)


def _config_fields() -> tuple[CapabilityConfigField, ...]:
    return (
        CapabilityConfigField("account_ref", CapabilityConfigValueType.REFERENCE),
        CapabilityConfigField(
            "api_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
        CapabilityConfigField(
            "region_code",
            CapabilityConfigValueType.STRING,
            CapabilityConfigValueFormat.STABLE_CODE,
        ),
    )


def _endpoint() -> tuple[CapabilityEndpointRequirement, ...]:
    return (
        CapabilityEndpointRequirement(
            endpoint_code="api_endpoint",
            endpoint_type=CapabilityEndpointType.HTTPS_URL,
            operation_codes=_OPERATION_CODES,
        ),
    )


INSTANCE_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="infrastructure.instance.lifecycle",
    schema_version=1,
    operations=_operations("instance-lifecycle"),
    config_fields=_config_fields(),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "infrastructure.instance.artifact-pinned",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.DIGEST,
        ),
        CapabilityCheck(
            "infrastructure.instance.identity-stable",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "infrastructure.instance.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "infrastructure.instance.health-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

NETWORK_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="infrastructure.network.lifecycle",
    schema_version=1,
    operations=_operations("network-lifecycle"),
    config_fields=_config_fields(),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "infrastructure.network.binding-isolated",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "infrastructure.network.cidr-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

VOLUME_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="infrastructure.volume.lifecycle",
    schema_version=1,
    operations=_operations("volume-lifecycle"),
    config_fields=_config_fields(),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "infrastructure.volume.attachment-bound",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "infrastructure.volume.attachment-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "infrastructure.volume.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

FIREWALL_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="infrastructure.firewall.lifecycle",
    schema_version=1,
    operations=_operations("firewall-lifecycle"),
    config_fields=_config_fields(),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "infrastructure.firewall.binding-isolated",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "infrastructure.firewall.rules-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = tuple(
    sorted(
        (
            FIREWALL_LIFECYCLE,
            INSTANCE_LIFECYCLE,
            NETWORK_LIFECYCLE,
            VOLUME_LIFECYCLE,
        ),
        key=lambda item: (item.capability_code, item.schema_version),
    )
)
CAPABILITY_COMPOSITIONS: tuple[CapabilityCompositionSnapshot, ...] = ()
COMPOSITION_DEPENDENCY_CONTRACTS: tuple[CapabilityContractSnapshot, ...] = ()
COMPOSITION_DEPENDENCY_SCHEMAS: tuple[CapabilitySchemaDocument, ...] = ()
PRODUCT_MANIFEST = ProductManifestSnapshot(
    product_code=OWNER_CODE,
    product_version=VERSION,
    capability_codes=tuple(item.capability_id for item in CAPABILITY_CONTRACTS),
)

__all__ = [
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
]
