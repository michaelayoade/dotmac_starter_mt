"""Permanent scope-wide opening custody; previews never authorize sealing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.semantic_encoding import digest_of, encode_fields, encode_unordered

from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningManifestV1,
    _ClosedEvidence,
)
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_variance_contracts import OpeningOperationalHoldV1

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    #: Manifest partitions the seal deliberately leaves out of the admitted
    #: population (the sealed hold list). Canonical order: ascending by text.
    held_account_ids: tuple[UUID, ...] = ()
    #: A typed reason for every listed partition the manifest does NOT mark held
    #: (an operational hold: target not pristine, import refused). Ascending.
    operational_holds: tuple[OpeningOperationalHoldV1, ...] = ()

    def __post_init__(self) -> None:
        _ClosedEvidence.__post_init__(self)
        keys = [str(item) for item in self.held_account_ids]
        if keys != sorted(set(keys)):
            raise OpeningEvidenceError(
                "held accounts must be unique and in ascending canonical order"
            )
        reasons = [str(item.account_id) for item in self.operational_holds]
        if reasons != sorted(set(reasons)) or not set(reasons) <= set(keys):
            raise OpeningEvidenceError(
                "operational holds must be unique, ascending and listed"
            )


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


@dataclass(frozen=True, slots=True)
class OpeningAuthoritySwitchReceiptV1(_ClosedEvidence):
    """What the participant switched, persisted append-only with the seal."""

    participant_ref: str
    participant_version: str
    switched_to: str
    state_digest: str


@dataclass(frozen=True, slots=True)
class StoredOpeningAuthoritySwitchReceiptV1(_ClosedEvidence):
    window_id: UUID
    receipt: OpeningAuthoritySwitchReceiptV1
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningAuthoritySwitchContextV1(_ClosedEvidence):
    """What a participant may observe: the seal it is switching authority with."""

    scope: Scope
    seal: OpeningWindowSealV1


class OpeningAuthoritySwitchParticipant(Protocol):
    """Runs INSIDE Billing's seal transaction (ADR 0031: seal and switch are one).

    Billing defines only this protocol. The participant receives the caller's
    own session, writes through it and must neither commit nor roll back. Any
    exception rolls the seal back with every other effect of the operation; a
    participant that returns normally has switched authority atomically with
    the seal. It is not run on an idempotent replay of an already-stored seal.

    It must return an ``OpeningAuthoritySwitchReceiptV1``; ``None`` or any other
    value counts as failure and rolls the seal back. Billing stores the receipt
    append-only in the same transaction (one per window).

    Privilege note (SUB-13): the seal runs as the restricted opening principal,
    so the participant writes as that principal. A product participant needs a
    narrow product-owned grant (for example EXECUTE on one SECURITY DEFINER
    switch function); that widens the principal's privilege closure and must be
    provisioned and read back effective (ADR 0031 section 4).
    """

    def switch_authority(
        self, session: Session, context: OpeningAuthoritySwitchContextV1
    ) -> OpeningAuthoritySwitchReceiptV1: ...
