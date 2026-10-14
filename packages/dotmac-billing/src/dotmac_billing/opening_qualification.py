"""Pure opening import qualification over trusted, freshly consulted providers.

Providers are installed by trusted composition, never selected by a payload.
They must authenticate independent source custody and current exact-command
migration grants, including issuer, expiry and revocation. Digests establish
content equality only; this module supplies neither a trust root nor an issuer.
No import result authorizes an inverse: that remains a separate fresh authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from dotmac_kernel.cache import Scope

from dotmac_billing._opening_values import OpeningCashValue, _validate_closed
from dotmac_billing.opening_contracts import (
    OpeningCaptureV1,
    OpeningVerificationUnavailable,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningImportVerificationRequestV2,
    OpeningImportVerificationV2,
    OpeningManifestV2,
)
from dotmac_billing.opening_receivable_validation import (
    opening_account_command_digest_v2,
    opening_account_import_blockers_v2,
    opening_manifest_fingerprint_v2,
)


@dataclass(frozen=True, slots=True)
class OpeningSourceEvidenceV2(OpeningCashValue):
    """Provider-attested, independently held complete source and proof handles."""

    manifest: OpeningManifestV2
    source_binding_proof_ref: str
    cash_lifecycle_proof_ref: str
    receivable_lifecycle_proof_ref: str
    documentary_tax_mapping_proof_ref: str
    historical_discharge_mapping_proof_ref: str


@dataclass(frozen=True, slots=True)
class OpeningImportGrantV2(OpeningCashValue):
    """Fresh provider-verified authority for one exact import command."""

    operation: Literal["billing.opening.import.v2"]
    command: ImportOpeningAccountV2
    migration_authority_proof_ref: str


class OpeningSourceEvidenceProviderV2(Protocol):
    def load_source(
        self, *, scope: Scope, capture: OpeningCaptureV1
    ) -> OpeningSourceEvidenceV2 | None:
        """Load authenticated independent source; None is authoritative denial."""
        ...


class OpeningImportGrantProviderV2(Protocol):
    def verify_grant(
        self, request: OpeningImportVerificationRequestV2
    ) -> OpeningImportGrantV2 | None:
        """Verify current issuer authority; None is authoritative denial."""
        ...


def qualify_opening_account_import_v2(
    request: OpeningImportVerificationRequestV2,
    *,
    source_provider: OpeningSourceEvidenceProviderV2 | None,
    grant_provider: OpeningImportGrantProviderV2 | None,
) -> OpeningImportVerificationV2 | None:
    """Bind fresh authority and independent complete evidence to valid content.

    No caching, I/O, financial recalculation, persistence or inverse permission
    is implemented here. Providers own I/O and authentic authority. Expected
    unavailability raises OpeningVerificationUnavailable; programmer errors
    escape. A denial remains None and is never relabelled unavailable.
    """
    _validate_closed(request, OpeningImportVerificationRequestV2)
    if source_provider is None or grant_provider is None:
        raise OpeningVerificationUnavailable("opening qualification providers missing")

    command = request.command
    command_digest = opening_account_command_digest_v2(command)
    manifest_fingerprint = opening_manifest_fingerprint_v2(command.manifest)
    if (
        request.command_digest != command_digest
        or request.manifest_fingerprint != manifest_fingerprint
    ):
        return None

    grant = grant_provider.verify_grant(request)
    if type(grant) is not OpeningImportGrantV2:
        return None
    _validate_closed(grant, OpeningImportGrantV2)
    if (
        grant.operation != "billing.opening.import.v2"
        or grant.command.scope != command.scope
        or opening_account_command_digest_v2(grant.command) != command_digest
    ):
        return None

    source = source_provider.load_source(
        scope=command.scope, capture=command.manifest.capture
    )
    if type(source) is not OpeningSourceEvidenceV2:
        return None
    _validate_closed(source, OpeningSourceEvidenceV2)
    if (
        source.manifest.scope != command.scope
        or source.manifest.capture != command.manifest.capture
        or opening_manifest_fingerprint_v2(source.manifest) != manifest_fingerprint
    ):
        return None
    if opening_account_import_blockers_v2(command):
        return None

    return OpeningImportVerificationV2(
        operation="billing.opening.import.v2",
        scope=command.scope,
        command_digest=command_digest,
        manifest_fingerprint=manifest_fingerprint,
        partition=command.partition,
        target=command.target,
        installation_id=source.manifest.capture.installation_id,
        migration_authority_ref=grant.command.migration_authority_ref,
        source_authority_ref=source.manifest.capture.source_authority_ref,
        source_binding_proof_ref=source.source_binding_proof_ref,
        cash_lifecycle_proof_ref=source.cash_lifecycle_proof_ref,
        receivable_lifecycle_proof_ref=source.receivable_lifecycle_proof_ref,
        documentary_tax_mapping_proof_ref=source.documentary_tax_mapping_proof_ref,
        historical_discharge_mapping_proof_ref=(
            source.historical_discharge_mapping_proof_ref
        ),
        migration_authority_proof_ref=grant.migration_authority_proof_ref,
    )
