"""Exact, value-free evidence compositions owned by the managed suite."""

from __future__ import annotations

from dotmac_domains_contracts import (
    CAPABILITY_CONTRACTS as DOMAINS_CONTRACTS,
)
from dotmac_domains_contracts import CAPABILITY_SCHEMAS as DOMAINS_SCHEMAS
from dotmac_domains_contracts import DNS_AUTHORITATIVE
from dotmac_kernel import (
    CapabilityCompositionSnapshot,
    CapabilityContractSnapshot,
    CapabilityEvidenceBinding,
    CapabilitySchemaDocument,
    ProductManifestSnapshot,
)
from dotmac_managed_collaboration_contracts import (
    CAPABILITY_CONTRACTS as COLLABORATION_CONTRACTS,
)
from dotmac_managed_collaboration_contracts import (
    CAPABILITY_SCHEMAS as COLLABORATION_SCHEMAS,
)
from dotmac_managed_collaboration_contracts import (
    USER_GROUP_QUOTA_LIFECYCLE,
    USER_OIDC_CONFIGURATION_LIFECYCLE,
)
from dotmac_managed_email_contracts import (
    CAPABILITY_CONTRACTS as EMAIL_CONTRACTS,
)
from dotmac_managed_email_contracts import CAPABILITY_SCHEMAS as EMAIL_SCHEMAS
from dotmac_managed_email_contracts import EMAIL_LIFECYCLE
from dotmac_managed_identity_contracts import (
    CAPABILITY_CONTRACTS as IDENTITY_CONTRACTS,
)
from dotmac_managed_identity_contracts import (
    CAPABILITY_SCHEMAS as IDENTITY_SCHEMAS,
)
from dotmac_managed_identity_contracts import (
    OIDC_CLIENT_LIFECYCLE,
    REALM_LIFECYCLE,
    USER_LIFECYCLE,
)

OWNER_CODE = "dotmac-managed-suite"
VERSION = "0.1.0a1"


def _binding(
    *,
    binding_code: str,
    source: CapabilityContractSnapshot,
    source_pointer: str,
    target: CapabilityContractSnapshot,
    target_pointer: str,
    coverage: str,
    source_selector: tuple[str, str] | None = None,
    target_selector: tuple[str, str] | None = None,
) -> CapabilityEvidenceBinding:
    source_apply = source.require_operation("apply")
    target_apply = target.require_operation("apply")
    return CapabilityEvidenceBinding(
        binding_code=binding_code,
        source_owner_code=source.owner_code,
        source_capability_code=source.capability_code,
        source_capability_schema_version=source.schema_version,
        source_operation_code="apply",
        source_output_schema_ref=source_apply.output_schema_ref,
        source_output_schema_digest=source_apply.output_schema_digest,
        source_pointer=source_pointer,
        target_owner_code=target.owner_code,
        target_capability_code=target.capability_code,
        target_capability_schema_version=target.schema_version,
        target_operation_code="apply",
        target_input_schema_ref=target_apply.input_schema_ref,
        target_input_schema_digest=target_apply.input_schema_digest,
        target_pointer=target_pointer,
        source_selector_pointer=(
            None if source_selector is None else source_selector[0]
        ),
        source_selector_value=(None if source_selector is None else source_selector[1]),
        target_selector_pointer=(
            None if target_selector is None else target_selector[0]
        ),
        target_selector_value=(None if target_selector is None else target_selector[1]),
        coverage=coverage,
    )


IDENTITY_FEDERATION = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.identity-federation.v1",
    schema_version=1,
    evidence_bindings=(
        _binding(
            binding_code="realm-issuer-to-oidc-client",
            source=REALM_LIFECYCLE,
            source_pointer="/issuer_url",
            target=OIDC_CLIENT_LIFECYCLE,
            target_pointer="/issuer_url",
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="realm-issuer-to-user",
            source=REALM_LIFECYCLE,
            source_pointer="/issuer_url",
            target=USER_LIFECYCLE,
            target_pointer="/issuer_url",
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="realm-ref-to-user",
            source=REALM_LIFECYCLE,
            source_pointer="/realm_ref",
            target=USER_LIFECYCLE,
            target_pointer="/realm_ref",
            coverage="each_target_exactly_one",
        ),
    ),
)

IDENTITY_ACCOUNT_FEDERATION = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.identity-account-federation.v1",
    schema_version=1,
    evidence_bindings=(
        _binding(
            binding_code="identity-ref-to-collaboration-user",
            source=USER_LIFECYCLE,
            source_pointer="/identity_ref",
            target=USER_GROUP_QUOTA_LIFECYCLE,
            target_pointer="/user_ref",
            target_selector=("/resource_kind", "user"),
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="identity-issuer-to-collaboration-user",
            source=USER_LIFECYCLE,
            source_pointer="/issuer_url",
            target=USER_GROUP_QUOTA_LIFECYCLE,
            target_pointer="/identity_issuer",
            target_selector=("/resource_kind", "user"),
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="identity-subject-to-collaboration-user",
            source=USER_LIFECYCLE,
            source_pointer="/subject",
            target=USER_GROUP_QUOTA_LIFECYCLE,
            target_pointer="/identity_subject",
            target_selector=("/resource_kind", "user"),
            coverage="each_target_exactly_one",
        ),
    ),
)

EMAIL_FEDERATION = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.email-federation.v1",
    schema_version=1,
    evidence_bindings=(
        _binding(
            binding_code="oidc-client-id-to-email",
            source=OIDC_CLIENT_LIFECYCLE,
            source_pointer="/client_id",
            target=EMAIL_LIFECYCLE,
            target_pointer="/oidc_client_id",
            target_selector=("/resource_kind", "application"),
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="oidc-issuer-to-email",
            source=OIDC_CLIENT_LIFECYCLE,
            source_pointer="/issuer_url",
            target=EMAIL_LIFECYCLE,
            target_pointer="/oidc_issuer_url",
            target_selector=("/resource_kind", "application"),
            coverage="each_target_exactly_one",
        ),
    ),
)

EMAIL_APPLICATION_DEPENDENCIES = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.email-application-dependencies.v1",
    schema_version=1,
    evidence_bindings=tuple(
        _binding(
            binding_code=f"email-application-to-{resource_kind}",
            source=EMAIL_LIFECYCLE,
            source_pointer="/application_ref",
            target=EMAIL_LIFECYCLE,
            target_pointer="/application_ref",
            source_selector=("/resource_kind", "application"),
            target_selector=("/resource_kind", resource_kind),
            coverage="each_target_exactly_one",
        )
        for resource_kind in (
            "alias",
            "app_password",
            "delivery",
            "dkim",
            "domain",
            "mailbox",
            "quota",
        )
    ),
)

COLLABORATION_FEDERATION = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.collaboration-federation.v1",
    schema_version=1,
    evidence_bindings=(
        _binding(
            binding_code="oidc-client-id-to-collaboration",
            source=OIDC_CLIENT_LIFECYCLE,
            source_pointer="/client_id",
            target=USER_OIDC_CONFIGURATION_LIFECYCLE,
            target_pointer="/client_id",
            coverage="each_target_exactly_one",
        ),
        _binding(
            binding_code="oidc-issuer-to-collaboration",
            source=OIDC_CLIENT_LIFECYCLE,
            source_pointer="/issuer_url",
            target=USER_OIDC_CONFIGURATION_LIFECYCLE,
            target_pointer="/issuer_url",
            coverage="each_target_exactly_one",
        ),
    ),
)

EMAIL_DNS = CapabilityCompositionSnapshot(
    owner_code=OWNER_CODE,
    composition_code="managed-suite.email-dns.v1",
    schema_version=1,
    evidence_bindings=(
        _binding(
            binding_code="email-dkim-dns-requirements",
            source=EMAIL_LIFECYCLE,
            source_pointer="/dns_requirements",
            target=DNS_AUTHORITATIVE,
            target_pointer="/dns_requirements",
            source_selector=("/resource_kind", "dkim"),
            target_selector=("/resource_kind", "recordset"),
            coverage="each_source_exactly_one",
        ),
        _binding(
            binding_code="email-domain-dns-requirements",
            source=EMAIL_LIFECYCLE,
            source_pointer="/dns_requirements",
            target=DNS_AUTHORITATIVE,
            target_pointer="/dns_requirements",
            source_selector=("/resource_kind", "domain"),
            target_selector=("/resource_kind", "recordset"),
            coverage="each_source_exactly_one",
        ),
    ),
)

# Construction is not enough: this proves the pinned paths remain declared,
# public/non-secret and type-compatible in the exact dependency version.
COMPOSITION_DEPENDENCY_CONTRACTS = tuple(
    sorted(
        (
            *COLLABORATION_CONTRACTS,
            *DOMAINS_CONTRACTS,
            *EMAIL_CONTRACTS,
            *IDENTITY_CONTRACTS,
        ),
        key=lambda item: item.identity,
    )
)
COMPOSITION_DEPENDENCY_SCHEMAS = tuple(
    sorted(
        (
            *COLLABORATION_SCHEMAS,
            *DOMAINS_SCHEMAS,
            *EMAIL_SCHEMAS,
            *IDENTITY_SCHEMAS,
        ),
        key=lambda item: item.schema_ref,
    )
)
CAPABILITY_COMPOSITIONS = tuple(
    sorted(
        (
            COLLABORATION_FEDERATION,
            EMAIL_APPLICATION_DEPENDENCIES,
            EMAIL_DNS,
            EMAIL_FEDERATION,
            IDENTITY_ACCOUNT_FEDERATION,
            IDENTITY_FEDERATION,
        ),
        key=lambda item: item.identity,
    )
)
for composition in CAPABILITY_COMPOSITIONS:
    composition.require_compatible_with(
        contracts=COMPOSITION_DEPENDENCY_CONTRACTS,
        schemas=COMPOSITION_DEPENDENCY_SCHEMAS,
    )

PRODUCT_MANIFEST = ProductManifestSnapshot(
    product_code=OWNER_CODE,
    product_version=VERSION,
    capability_codes=(),
)
CAPABILITY_CONTRACTS: tuple[CapabilityContractSnapshot, ...] = ()
CAPABILITY_SCHEMAS: tuple[CapabilitySchemaDocument, ...] = ()

__all__ = [
    "CAPABILITY_COMPOSITIONS",
    "CAPABILITY_CONTRACTS",
    "CAPABILITY_SCHEMAS",
    "COLLABORATION_FEDERATION",
    "COMPOSITION_DEPENDENCY_CONTRACTS",
    "COMPOSITION_DEPENDENCY_SCHEMAS",
    "EMAIL_APPLICATION_DEPENDENCIES",
    "EMAIL_DNS",
    "EMAIL_FEDERATION",
    "IDENTITY_FEDERATION",
    "IDENTITY_ACCOUNT_FEDERATION",
    "PRODUCT_MANIFEST",
]
