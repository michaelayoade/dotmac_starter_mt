"""Provider-neutral managed identity contract catalogue."""

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

OWNER_CODE = "dotmac-managed-identity"
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


def _admin_endpoint_requirement() -> CapabilityEndpointRequirement:
    return CapabilityEndpointRequirement(
        endpoint_code="admin_endpoint",
        endpoint_type=CapabilityEndpointType.HTTPS_URL,
        operation_codes=_OPERATION_CODES,
    )


REALM_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="identity.realm.lifecycle",
    schema_version=1,
    operations=_operations("realm-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "admin_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
        CapabilityConfigField(
            "identity_policy_ref", CapabilityConfigValueType.REFERENCE
        ),
    ),
    endpoint_requirements=(_admin_endpoint_requirement(),),
    checks=(
        CapabilityCheck(
            "identity.realm.admin-api-private",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.realm.discovery-https",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.realm.rs256-signing",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.realm.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

OIDC_CLIENT_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="identity.oidc-client.lifecycle",
    schema_version=1,
    operations=_operations("oidc-client-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "admin_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
        CapabilityConfigField(
            "client_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
    ),
    endpoint_requirements=(_admin_endpoint_requirement(),),
    checks=(
        CapabilityCheck(
            "identity.oidc-client.audience-azp-validation",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.oidc-client.authorization-code",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.oidc-client.confidential-client",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.oidc-client.pkce-s256",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.oidc-client.rs256-id-token",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.oidc-client.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

USER_LIFECYCLE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="identity.user.lifecycle",
    schema_version=1,
    operations=_operations("user-lifecycle"),
    config_fields=(
        CapabilityConfigField(
            "admin_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
    ),
    endpoint_requirements=(_admin_endpoint_requirement(),),
    checks=(
        CapabilityCheck(
            "identity.user.email-is-attribute-only",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.user.sessions-revoked-on-disable",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.user.stable-reference",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "identity.user.configuration-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = tuple(
    sorted(
        (REALM_LIFECYCLE, OIDC_CLIENT_LIFECYCLE, USER_LIFECYCLE),
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
    "OIDC_CLIENT_LIFECYCLE",
    "PRODUCT_MANIFEST",
    "REALM_LIFECYCLE",
    "USER_LIFECYCLE",
]
