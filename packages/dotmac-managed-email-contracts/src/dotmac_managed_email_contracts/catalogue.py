"""Provider-neutral managed email contract catalogue."""

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

OWNER_CODE = "dotmac-managed-email"
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


def _endpoint(code: str) -> CapabilityEndpointRequirement:
    return CapabilityEndpointRequirement(
        endpoint_code=code,
        endpoint_type=CapabilityEndpointType.HTTPS_URL,
        operation_codes=_OPERATION_CODES,
    )


EMAIL_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="email.lifecycle",
    schema_version=1,
    operations=_operations("email-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "admin_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
        CapabilityConfigField(
            "oidc_client_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
    ),
    endpoint_requirements=(_endpoint("admin_endpoint"),),
    checks=(
        CapabilityCheck(
            "email.application.admin-api-private",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "email.application.health-ready",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "email.application.oidc-immutable-subject",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "email.application.oidc-no-account-or-email-linking",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "email.application.oidc-rs256-s256-aud-azp",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "email.alias.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.app-password.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.application.health-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.delivery.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.dkim.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.domain.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.mailbox.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "email.quota.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = (EMAIL_LIFECYCLE,)
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
    "EMAIL_LIFECYCLE",
    "PRODUCT_MANIFEST",
]
