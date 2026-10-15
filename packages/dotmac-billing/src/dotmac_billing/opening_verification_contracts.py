"""DRAFT public-verification contracts; no signing custody or enrolled authority.

Trusted composition supplies independent readers. A custody adapter must
independently authenticate immutable versions and their original provenance;
merely echoing signed labels or recomputing a checksum violates this port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.opening_contracts import OpeningCaptureV1, OpeningSourceIdentityV1
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningImportVerificationRequestV2,
    OpeningManifestV2,
)

OpeningSignaturePurposeV1 = Literal[
    "billing.opening.source.attest.v1", "billing.opening.import.v2"
]
OpeningEvidenceRoleV1 = Literal[
    "source_binding",
    "cash_lifecycle",
    "receivable_lifecycle",
    "documentary_tax_mapping",
    "historical_discharge_mapping",
    "fence",
    "controls",
    "population",
    "original_document",
    "tax",
    "fx",
    "discharge",
    "confirmation",
    "consumption",
    "collection",
    "receivable",
]


@dataclass(frozen=True, slots=True)
class OpeningTrustKeyV1(OpeningCashValue):
    key_id: str
    issuer: str
    purpose: OpeningSignaturePurposeV1
    public_key_hex: str
    public_key_fingerprint: str
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningAuthorityPolicyV1(OpeningCashValue):
    policy_id: str
    scope: Scope
    installation_id: str
    audience: str
    source_key: OpeningTrustKeyV1
    grant_key: OpeningTrustKeyV1
    not_before: datetime
    expires_at: datetime
    max_validity_seconds: int
    max_revocation_age_seconds: int


@dataclass(frozen=True, slots=True)
class OpeningRevocationStateV1(OpeningCashValue):
    policy_id: str
    scope: Scope
    installation_id: str
    checked_at: datetime
    revoked_key_ids: tuple[str, ...]
    revoked_issuers: tuple[str, ...]
    revoked_grant_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningSignatureTermsV1(OpeningCashValue):
    schema_version: Literal[1]
    purpose: OpeningSignaturePurposeV1
    issuer: str
    key_id: str
    policy_id: str
    scope: Scope
    installation_id: str
    audience: str
    issued_at: datetime
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningEvidenceRequirementV1(OpeningCashValue):
    role: OpeningEvidenceRoleV1
    reference: str
    subject: OpeningSourceIdentityV1 | None
    original_issued_at: datetime | None


@dataclass(frozen=True, slots=True)
class OpeningEvidenceBindingV1(OpeningCashValue):
    requirement: OpeningEvidenceRequirementV1
    object_id: str
    version_id: str
    content_sha256: str
    provenance_ref: str


@dataclass(frozen=True, slots=True)
class OpeningImmutableEvidenceV1(OpeningCashValue):
    object_id: str
    version_id: str
    provenance_ref: str
    content: bytes
    provenance: Literal["retained_source", "issue_time_original", "regenerated"]
    original_issued_at: datetime | None


@dataclass(frozen=True, slots=True)
class BillingOpeningSourceAttestationV1(OpeningCashValue):
    terms: OpeningSignatureTermsV1
    manifest: OpeningManifestV2
    source_binding_proof_ref: str
    cash_lifecycle_proof_ref: str
    receivable_lifecycle_proof_ref: str
    documentary_tax_mapping_proof_ref: str
    historical_discharge_mapping_proof_ref: str
    fence_proof_ref: str
    controls_proof_ref: str
    bindings: tuple[OpeningEvidenceBindingV1, ...]


@dataclass(frozen=True, slots=True)
class SignedOpeningSourceAttestationV1(OpeningCashValue):
    attestation: BillingOpeningSourceAttestationV1
    signature_hex: str


@dataclass(frozen=True, slots=True)
class BillingOpeningCommandGrantV1(OpeningCashValue):
    terms: OpeningSignatureTermsV1
    grant_id: str
    approval_decision_ref: str
    command: ImportOpeningAccountV2
    command_digest: str
    manifest_fingerprint: str
    source_attestation_digest: str
    window_id: UUID
    window_epoch: UUID
    migration_authority_proof_ref: str


@dataclass(frozen=True, slots=True)
class SignedOpeningCommandGrantV1(OpeningCashValue):
    grant: BillingOpeningCommandGrantV1
    signature_hex: str


@dataclass(frozen=True, slots=True)
class OpeningGrantIssuanceV1(OpeningCashValue):
    """Issuer's immutable grant-ID binding; never a consumed-import record."""

    grant_id: str
    grant_digest: str
    command_digest: str
    idempotency_key: str
    scope: Scope
    installation_id: str


@dataclass(frozen=True, slots=True)
class OpeningWindowIdentityV1(OpeningCashValue):
    """Identity from get_opening_window on the caller's Session, including seals."""

    scope: Scope
    window_id: UUID
    epoch: UUID
    manifest_fingerprint: str


class OpeningVerificationClockV1(Protocol):
    def now(self) -> datetime: ...


class OpeningAuthorityReaderV1(Protocol):
    """None is unavailable configuration/revocation; errors must not be hidden."""

    def current_policy(
        self, *, scope: Scope, installation_id: str
    ) -> OpeningAuthorityPolicyV1 | None: ...

    def current_revocations(
        self, *, scope: Scope, installation_id: str, policy_id: str
    ) -> OpeningRevocationStateV1 | None: ...


class OpeningSignedSourceReaderV1(Protocol):
    def load_attestation(
        self, *, scope: Scope, capture: OpeningCaptureV1
    ) -> SignedOpeningSourceAttestationV1 | None:
        """Independent trusted lookup; None is authenticated absence/denial.

        Unavailable storage/transport must raise OpeningVerificationUnavailable.
        Request-selected files or echoed candidate manifests are not this port.
        """
        ...


class OpeningImmutableCustodyReaderV1(Protocol):
    def authenticate_immutable(
        self,
        binding: OpeningEvidenceBindingV1,
        *,
        scope: Scope,
        capture: OpeningCaptureV1,
    ) -> OpeningImmutableEvidenceV1 | None:
        """Authenticate immutable coordinates AND independent original provenance.

        None is authentic absence/denial; unavailable custody raises
        OpeningVerificationUnavailable. A signed binding is not provenance.
        """
        ...


class OpeningSignedGrantReaderV1(Protocol):
    def load_grant(
        self, request: OpeningImportVerificationRequestV2
    ) -> SignedOpeningCommandGrantV1 | None: ...

    def load_issuance(
        self, *, scope: Scope, installation_id: str, grant_id: str
    ) -> OpeningGrantIssuanceV1 | None:
        """Authoritative immutable issuance binding; None is authenticated denial."""
        ...


class OpeningWindowIdentityReaderV1(Protocol):
    def current_window(self, *, scope: Scope) -> OpeningWindowIdentityV1 | None:
        """Use existing get_opening_window; no new Session or sealed-state veto."""
        ...
