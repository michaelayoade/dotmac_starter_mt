"""Provider-neutral closed managed host contract catalogue."""

from __future__ import annotations

from dotmac_kernel import (
    CapabilityCheck,
    CapabilityCheckStage,
    CapabilityCompositionSnapshot,
    CapabilityConfigField,
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

OWNER_CODE = "dotmac-managed-host"
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


def _config_fields(
    *extra_fields: CapabilityConfigField,
) -> tuple[CapabilityConfigField, ...]:
    return tuple(
        sorted(
            (
                CapabilityConfigField(
                    "agent_identity_ref", CapabilityConfigValueType.REFERENCE
                ),
                CapabilityConfigField(
                    "agent_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
                ),
                *extra_fields,
            ),
            key=lambda field: field.field_code,
        )
    )


def _endpoint() -> tuple[CapabilityEndpointRequirement, ...]:
    return (
        CapabilityEndpointRequirement(
            endpoint_code="agent_endpoint",
            endpoint_type=CapabilityEndpointType.HTTPS_URL,
            operation_codes=_OPERATION_CODES,
        ),
    )


DEPLOYMENT_BUNDLE_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="host.deployment-bundle.lifecycle",
    schema_version=1,
    operations=_operations("deployment-bundle-lifecycle"),
    config_fields=_config_fields(
        CapabilityConfigField(
            "bundle_catalogue_ref", CapabilityConfigValueType.REFERENCE
        )
    ),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "host.bundle.artifact-pinned",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.DIGEST,
        ),
        CapabilityCheck(
            "host.bundle.version-supported",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "host.bundle.health-validated",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "host.bundle.installed-version-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "host.bundle.rollback-available",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
    ),
)

BACKUP_RESTORE_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="host.backup-restore.lifecycle",
    schema_version=1,
    operations=_operations("backup-restore-lifecycle"),
    config_fields=_config_fields(
        CapabilityConfigField("backup_storage_ref", CapabilityConfigValueType.REFERENCE)
    ),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "host.backup.storage-held",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "host.backup.latest-restorable",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.RECEIPT_REFERENCE,
        ),
        CapabilityCheck(
            "host.backup.restore-health-validated",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "host.backup.restore-rehearsal-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

HEALTH_PROBE_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="host.health-probe.lifecycle",
    schema_version=1,
    operations=_operations("health-probe-lifecycle"),
    config_fields=_config_fields(),
    endpoint_requirements=_endpoint(),
    checks=(
        CapabilityCheck(
            "host.health.probe-bounded",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "host.health.result-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = tuple(
    sorted(
        (
            BACKUP_RESTORE_LIFECYCLE,
            DEPLOYMENT_BUNDLE_LIFECYCLE,
            HEALTH_PROBE_LIFECYCLE,
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
    "BACKUP_RESTORE_LIFECYCLE",
    "CAPABILITY_COMPOSITIONS",
    "CAPABILITY_CONTRACTS",
    "CAPABILITY_SCHEMAS",
    "COMPOSITION_DEPENDENCY_CONTRACTS",
    "COMPOSITION_DEPENDENCY_SCHEMAS",
    "DEPLOYMENT_BUNDLE_LIFECYCLE",
    "HEALTH_PROBE_LIFECYCLE",
    "PRODUCT_MANIFEST",
]
