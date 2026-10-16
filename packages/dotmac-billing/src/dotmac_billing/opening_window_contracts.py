"""Permanent scope-wide opening custody; previews never authorize sealing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.semantic_encoding import digest_of, encode_fields, encode_unordered

from dotmac_billing.opening_contracts import OpeningManifestV1, _ClosedEvidence
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2


@dataclass(frozen=True, slots=True)
class OpeningWindowV1(_ClosedEvidence):
    scope: Scope
    window_id: UUID
    epoch: UUID
    manifest_version: int
    manifest_fingerprint: str
    manifest: OpeningManifestV1 | OpeningManifestV2
    principal_name: str
    principal_oid: int
    provision_authority_ref: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ProvisionOpeningWindowV1(_ClosedEvidence):
    window: OpeningWindowV1


@dataclass(frozen=True, slots=True)
class OpeningWindowProvisionVerificationRequestV1(_ClosedEvidence):
    command: ProvisionOpeningWindowV1
    command_digest: str


@dataclass(frozen=True, slots=True)
class OpeningWindowProvisionVerificationV1(_ClosedEvidence):
    operation: str
    scope: Scope
    window_id: UUID
    epoch: UUID
    manifest_fingerprint: str
    principal_name: str
    principal_oid: int
    command_digest: str
    authority_ref: str
    complete_population_proof_ref: str
    authority_proof_ref: str


class OpeningWindowProvisionVerifier(Protocol):
    """Qualify frozen complete capture and actual restricted role binding."""

    def verify_provision(
        self, request: OpeningWindowProvisionVerificationRequestV1
    ) -> OpeningWindowProvisionVerificationV1 | None: ...


@dataclass(frozen=True, slots=True)
class StoredOpeningWindowV1(_ClosedEvidence):
    window: OpeningWindowV1
    verification: OpeningWindowProvisionVerificationV1


@dataclass(frozen=True, slots=True)
class OpeningWindowAdmissionV1(_ClosedEvidence):
    scope: Scope
    window_id: UUID
    receipt_id: UUID
    billing_account_id: UUID
    request_identity: str
    request_digest: str
    manifest_fingerprint: str


def opening_admission_population_digest(
    admissions: tuple[OpeningWindowAdmissionV1, ...],
) -> str:
    """Full typed, domain-separated population; order is not authority."""
    rows = encode_unordered(
        encode_fields(
            (
                (
                    "scope_kind",
                    "tenant" if isinstance(item.scope, TenantScope) else "platform",
                ),
                (
                    "scope_id_uuid",
                    str(item.scope.tenant_id)
                    if isinstance(item.scope, TenantScope)
                    else "platform",
                ),
                ("window_id_uuid", str(item.window_id)),
                ("receipt_id_uuid", str(item.receipt_id)),
                ("billing_account_id_uuid", str(item.billing_account_id)),
                ("request_identity", item.request_identity),
                ("request_digest", item.request_digest),
                ("manifest_fingerprint", item.manifest_fingerprint),
            )
        )
        for item in admissions
    )
    return digest_of(
        "billing.opening.window.admissions.v1",
        encode_fields((("encoding_version", 1), ("rows", rows))),
    ).removeprefix("cv1:")


@dataclass(frozen=True, slots=True)
class SealOpeningWindowV1(_ClosedEvidence):
    scope: Scope
    window_id: UUID
    epoch: UUID
    manifest_fingerprint: str
    admissions: tuple[OpeningWindowAdmissionV1, ...]
    coupled_watermark: str
    command_key: str
    authority_ref: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningWindowSealVerificationRequestV1(_ClosedEvidence):
    command: SealOpeningWindowV1
    command_digest: str
    population_digest: str


@dataclass(frozen=True, slots=True)
class OpeningWindowSealVerificationV1(_ClosedEvidence):
    operation: str
    scope: Scope
    window_id: UUID
    epoch: UUID
    manifest_fingerprint: str
    command_digest: str
    population_digest: str
    population_count: int
    coupled_watermark: str
    authority_ref: str
    complete_population_proof_ref: str
    coupled_watermark_proof_ref: str
    authority_proof_ref: str


class OpeningWindowSealVerifier(Protocol):
    """Assembly verifies external complete population and coupled writer fence.

    Billing compares its own immutable admissions under the physical window
    lock. A caller-created verification value is never a trusted dependency.
    """

    def verify_seal(
        self, request: OpeningWindowSealVerificationRequestV1
    ) -> OpeningWindowSealVerificationV1 | None: ...


@dataclass(frozen=True, slots=True)
class OpeningWindowSealV1(_ClosedEvidence):
    command: SealOpeningWindowV1
    verification: OpeningWindowSealVerificationV1
    population_digest: str
    population_count: int
    recorded_at: datetime
