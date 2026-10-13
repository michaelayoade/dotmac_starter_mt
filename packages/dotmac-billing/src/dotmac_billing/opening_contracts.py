"""Immutable candidate opening evidence, never a seal, import or funding grant.

Amounts are exact signed integer minor-unit controls, not a second money type.
Runtime validation rejects coercions and mutable nested evidence at this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from types import UnionType
from typing import Protocol, get_args, get_origin, get_type_hints
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope


class OpeningEvidenceError(ValueError):
    """Malformed structural API input; domain refusals belong in the preview."""


def _matches(value: object, expected: object) -> bool:
    if get_origin(expected) is UnionType:
        return any(_matches(value, item) for item in get_args(expected))
    if get_origin(expected) is tuple:
        args = get_args(expected)
        return type(value) is tuple and all(_matches(item, args[0]) for item in value)
    return type(value) is expected


class OpeningVerificationUnavailable(RuntimeError):
    """Verifier cannot qualify evidence; a counted refusal, not a grant."""


class _ClosedEvidence:
    __slots__ = ()

    def __post_init__(self) -> None:
        annotations = get_type_hints(type(self))
        for field in fields(self):  # type: ignore[arg-type]
            value = getattr(self, field.name)
            if not _matches(value, annotations[field.name]):
                raise OpeningEvidenceError(f"invalid type for {field.name}")
            if type(value) is int and value.bit_length() > 4096:
                raise OpeningEvidenceError(f"integer too large for {field.name}")
            if isinstance(value, str) and not value.strip():
                raise OpeningEvidenceError(f"empty {field.name}")
            if isinstance(value, datetime) and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise OpeningEvidenceError(f"naive instant for {field.name}")
            if type(value) is TenantScope and type(value.tenant_id) is not UUID:
                raise OpeningEvidenceError("invalid tenant scope")


@dataclass(frozen=True, slots=True)
class OpeningSourceIdentityV1(_ClosedEvidence):
    kind: str
    key: str


@dataclass(frozen=True, slots=True)
class OpeningCaptureV1(_ClosedEvidence):
    source_system: str
    source_repository_sha: str
    installation_id: str
    schema_heads: tuple[str, ...]
    snapshot_id: str
    captured_at: datetime
    economic_cutoff: datetime
    recorded_cutoff: datetime
    isolation: str
    population_version: str
    source_authority_ref: str
    account_mapping_version: str
    cash_mapping_version: str


@dataclass(frozen=True, slots=True)
class OpeningPartitionV1(_ClosedEvidence):
    source_account_key: str
    account_id: UUID
    currency: str
    precision: int


@dataclass(frozen=True, slots=True)
class OpeningDispositionV1(_ClosedEvidence):
    identity: OpeningSourceIdentityV1
    partition: OpeningPartitionV1
    disposition: str
    reason: str
    residual_minor: int


@dataclass(frozen=True, slots=True)
class OpeningPopulationCountsV1(_ClosedEvidence):
    population: int
    eligible: int
    excluded: int
    blocking: int


@dataclass(frozen=True, slots=True)
class OpeningLaneControlsV1(_ClosedEvidence):
    partition: OpeningPartitionV1
    collectible_receivable_minor: int
    available_cash_credit_minor: int
    available_non_cash_credit_minor: int
    unapplied_customer_credit_minor: int
    prepaid_funding_reserved_minor: int
    prepaid_funding_consumed_minor: int
    written_off_total_minor: int
    refunded_total_minor: int
    adjustment_total_minor: int
    open_ar_minor: int


@dataclass(frozen=True, slots=True)
class OpeningConsumptionV1(_ClosedEvidence):
    reference: str
    kind: str
    amount_minor: int
    economic_at: datetime
    recorded_at: datetime
    target_ref: str | None
    ledger_ref: str | None
    reverses_ref: str | None


@dataclass(frozen=True, slots=True)
class OpeningCashConfirmationV1(_ClosedEvidence):
    owner: str
    reference: str
    version: str
    evidence_digest: str
    evidence_kind: str
    cash_amount_minor: int
    currency: str
    precision: int
    economic_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningCashLotV1(_ClosedEvidence):
    lot_id: str
    source_identity: OpeningSourceIdentityV1
    partition: OpeningPartitionV1
    # Exact same namespace as live AcceptSettlement source identity; no prefixing.
    source_system: str
    source_settlement_key: str
    confirmation: OpeningCashConfirmationV1 | None
    gross_minor: int
    residual_minor: int
    # Ordered exact historical allocations/refunds/reversals/reservations.
    consumptions: tuple[OpeningConsumptionV1, ...]


@dataclass(frozen=True, slots=True)
class OpeningManifestV1(_ClosedEvidence):
    scope: Scope
    capture: OpeningCaptureV1
    population: tuple[OpeningSourceIdentityV1, ...]
    dispositions: tuple[OpeningDispositionV1, ...]
    counts: OpeningPopulationCountsV1
    controls: tuple[OpeningLaneControlsV1, ...]
    cash_lots: tuple[OpeningCashLotV1, ...]


@dataclass(frozen=True, slots=True)
class OpeningTargetV1(_ClosedEvidence):
    scope: Scope
    account_id: UUID
    currency: str
    precision: int
    expected_position_fingerprint: str
    posting_watermark: str


@dataclass(frozen=True, slots=True)
class OpeningPreviewRequestV1(_ClosedEvidence):
    manifest: OpeningManifestV1
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    migration_authority_ref: str


@dataclass(frozen=True, slots=True)
class OpeningVerificationV1(_ClosedEvidence):
    """Attestation returned ONLY by an assembly-owned trusted verifier port.

    Creating this value as caller input does not establish any trust. The port
    must authenticate capture completeness, exact cash attribution, live source
    namespace mapping and migration authority for this operation and binding.
    """

    operation: str
    scope: Scope
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    migration_authority_ref: str
    source_authority_ref: str
    source_binding_proof_ref: str
    cash_lifecycle_proof_ref: str
    migration_authority_proof_ref: str


class OpeningEvidenceVerifier(Protocol):
    """Trusted assembly dependency; implementation must itself be pure here.

    Qualify the complete captured population and exact historical consumption,
    including zero-consumption lots. Bind actual installation/source namespace
    to the live source_system/source_settlement_key without synthetic IDs.
    Reject unsupported evidence with None; raise OpeningVerificationUnavailable
    only for bounded evidence-service unavailability. A digest, actor permission
    or caller boolean cannot qualify source capture, cash provenance or authority.
    """

    def verify(
        self, request: OpeningPreviewRequestV1, manifest_fingerprint: str
    ) -> OpeningVerificationV1 | None: ...


@dataclass(frozen=True, slots=True)
class OpeningBlockerV1(_ClosedEvidence):
    code: str
    count: int
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningImportPreviewV1(_ClosedEvidence):
    operation: str
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    admissible: bool
    cash_residual_minor: int
    blockers: tuple[OpeningBlockerV1, ...]

    @property
    def blocker_count(self) -> int:
        return sum(item.count for item in self.blockers)
