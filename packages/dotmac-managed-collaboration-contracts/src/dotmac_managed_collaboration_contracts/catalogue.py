"""Provider-neutral managed collaboration contract catalogue."""

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

OWNER_CODE = "dotmac-managed-collaboration"
VERSION = "0.1.0a1"
_OPERATION_CODES = ("apply", "cancel", "observe", "plan")


def _public_capability_id(contract: CapabilityContractSnapshot) -> str:
    return f"{contract.capability_code}.v{contract.schema_version}"


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


def _management_endpoint() -> CapabilityEndpointRequirement:
    return CapabilityEndpointRequirement(
        endpoint_code="management_endpoint",
        endpoint_type=CapabilityEndpointType.HTTPS_URL,
        operation_codes=_OPERATION_CODES,
    )


def _management_config() -> tuple[CapabilityConfigField, ...]:
    return (
        CapabilityConfigField(
            "management_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
    )


APPLICATION_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="collaboration.application.lifecycle",
    schema_version=1,
    operations=_operations("application-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "backup_storage_ref", CapabilityConfigValueType.REFERENCE
        ),
        *_management_config(),
        CapabilityConfigField(
            "release_channel_ref", CapabilityConfigValueType.REFERENCE
        ),
    ),
    endpoint_requirements=(_management_endpoint(),),
    checks=(
        CapabilityCheck(
            "collaboration.application.backup-storage-held",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.application.control-api-private",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.application.rollback-available",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.application.backup-latest-restorable",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.RECEIPT_REFERENCE,
        ),
        CapabilityCheck(
            "collaboration.application.decommission-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.application.health-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.application.restore-health-validated",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.application.suspension-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.application.upgrade-health-validated",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
    ),
)

FILE_ROUNDTRIP_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="collaboration.file-roundtrip.lifecycle",
    schema_version=1,
    operations=_operations("file-roundtrip-lifecycle"),
    config_fields=_management_config(),
    endpoint_requirements=(_management_endpoint(),),
    checks=(
        CapabilityCheck(
            "collaboration.file-roundtrip.control-api-private",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.file-roundtrip.cleanup-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.file-roundtrip.digest-matched",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DIGEST,
        ),
        CapabilityCheck(
            "collaboration.file-roundtrip.exact-user-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.file-roundtrip.read-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.file-roundtrip.write-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.BOOLEAN,
        ),
    ),
)

USER_GROUP_QUOTA_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="collaboration.user-group-quota.lifecycle",
    schema_version=1,
    operations=_operations("user-group-quota-lifecycle"),
    config_fields=_management_config(),
    endpoint_requirements=(_management_endpoint(),),
    checks=(
        CapabilityCheck(
            "collaboration.account.control-api-private",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.account.group-membership-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.account.issuer-subject-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.account.quota-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.account.stable-user-id-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "collaboration.account.user-state-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

USER_OIDC_CONFIGURATION_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="collaboration.user-oidc.configuration.lifecycle",
    schema_version=1,
    operations=_operations("user-oidc-configuration-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "client_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
        *_management_config(),
    ),
    endpoint_requirements=(_management_endpoint(),),
    checks=(
        CapabilityCheck(
            "collaboration.user-oidc.audience-azp-validation",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.backchannel-logout",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.direct-login-break-glass",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.email-linking-disabled",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.issuer-subject-binding",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.jit-account-creation-disabled",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.pkce-s256",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.session-provenance",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.session-revocation",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "collaboration.user-oidc.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = tuple(
    sorted(
        (
            APPLICATION_LIFECYCLE,
            FILE_ROUNDTRIP_LIFECYCLE,
            USER_GROUP_QUOTA_LIFECYCLE,
            USER_OIDC_CONFIGURATION_LIFECYCLE,
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
    capability_codes=tuple(
        _public_capability_id(item) for item in CAPABILITY_CONTRACTS
    ),
)

__all__ = [
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
]
