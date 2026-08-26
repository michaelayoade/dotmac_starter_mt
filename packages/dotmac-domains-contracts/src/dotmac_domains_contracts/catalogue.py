"""Provider-neutral authoritative DNS and TLS contract catalogue."""

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

OWNER_CODE = "dotmac-domains"
VERSION = "0.1.0a1"
_OPERATION_CODES = ("apply", "cancel", "observe", "plan")


def _operation(operation_code: str) -> CapabilityOperation:
    prefix = f"schema:{OWNER_CODE}/dns-authoritative/{operation_code}"
    input_schema = SCHEMAS_BY_REF[f"{prefix}/input@v1"]
    output_schema = SCHEMAS_BY_REF[f"{prefix}/output@v1"]
    return CapabilityOperation(
        operation_code=operation_code,
        input_schema_ref=input_schema.schema_ref,
        input_schema_digest=input_schema.digest,
        output_schema_ref=output_schema.schema_ref,
        output_schema_digest=output_schema.digest,
    )


DNS_AUTHORITATIVE = CapabilityContractSnapshot(
    owner_code=OWNER_CODE,
    capability_code="dns.authoritative",
    schema_version=1,
    operations=tuple(_operation(code) for code in _OPERATION_CODES),
    config_fields=(
        CapabilityConfigField(
            "api_secret_ref", CapabilityConfigValueType.SECRET_REFERENCE
        ),
    ),
    endpoint_requirements=(
        CapabilityEndpointRequirement(
            endpoint_code="api_endpoint",
            endpoint_type=CapabilityEndpointType.HTTPS_URL,
            operation_codes=_OPERATION_CODES,
        ),
        CapabilityEndpointRequirement(
            endpoint_code="public_resolver_endpoint",
            endpoint_type=CapabilityEndpointType.HOST_PORT,
            operation_codes=_OPERATION_CODES,
        ),
    ),
    checks=(
        CapabilityCheck(
            "domains.dns.canonical-idna",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "domains.tls.certificate-valid",
            CapabilityCheckStage.ACTIVATION,
            CapabilityEvidenceType.BOOLEAN,
        ),
        CapabilityCheck(
            "domains.dns.autoconfig-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.autodiscover-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.dkim-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.dmarc-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.mta-sts-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.mx-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.ptr-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.spf-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.dns.tls-rpt-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
        CapabilityCheck(
            "domains.tls.https-redirect-observed",
            CapabilityCheckStage.EVIDENCE,
            CapabilityEvidenceType.DOCUMENT,
        ),
    ),
)

CAPABILITY_CONTRACTS = (DNS_AUTHORITATIVE,)
CAPABILITY_COMPOSITIONS: tuple[CapabilityCompositionSnapshot, ...] = ()
COMPOSITION_DEPENDENCY_CONTRACTS: tuple[CapabilityContractSnapshot, ...] = ()
COMPOSITION_DEPENDENCY_SCHEMAS: tuple[CapabilitySchemaDocument, ...] = ()
PRODUCT_MANIFEST = ProductManifestSnapshot(
    product_code=OWNER_CODE,
    product_version=VERSION,
    capability_codes=(DNS_AUTHORITATIVE.capability_id,),
)

__all__ = [
    "CAPABILITY_COMPOSITIONS",
    "CAPABILITY_CONTRACTS",
    "CAPABILITY_SCHEMAS",
    "COMPOSITION_DEPENDENCY_CONTRACTS",
    "COMPOSITION_DEPENDENCY_SCHEMAS",
    "DNS_AUTHORITATIVE",
    "PRODUCT_MANIFEST",
]
