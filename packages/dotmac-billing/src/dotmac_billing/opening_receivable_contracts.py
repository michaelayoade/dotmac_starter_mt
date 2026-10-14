"""Successor opening boundary: documentary history and residual recognition.

These candidate values do not establish source, tax or migration authority.
V1 cash commands, fingerprints and stored facts retain their original shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.contracts import (
    AccountingEffectV1,
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    DueDateBasisV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PresentationAssetReferenceV1,
    ServicePeriodEvidenceV1,
    ServicePeriodStatus,
)
from dotmac_billing.opening_cash_contracts import OpeningCashEffectV1
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_contracts import (
    OpeningBlockerV1,
    OpeningCaptureV1,
    OpeningCashLotV1,
    OpeningDispositionV1,
    OpeningLaneControlsV1,
    OpeningPartitionV1,
    OpeningPopulationCountsV1,
    OpeningSourceIdentityV1,
    OpeningTargetV1,
)


@dataclass(frozen=True, slots=True)
class OpeningCollectionEvidenceV1(OpeningCashValue):
    status: Literal["verified", "unknown_unverified"]
    subject_ref: str | None
    service_ref: str | None
    collection_timing: Literal["advance", "arrears"] | None
    service_period: ServicePeriodEvidenceV1
    due_at: datetime | None
    due_date_basis: DueDateBasisV1
    source_authority: str
    evidence_ref: str
    mapping_version: str

    @property
    def automated_collection_allowed(self) -> bool:
        return (
            self.status == "verified"
            and self.subject_ref is not None
            and self.collection_timing is not None
            and self.due_at is not None
            and self.due_date_basis.automated_collection_allowed
        )


@dataclass(frozen=True, slots=True)
class OpeningOriginalDocumentV1(OpeningCashValue):
    document_number: str
    issued_at: datetime
    documentary_reference: str
    documentary_digest: str
    subtotal: Money
    tax_total: Money
    grand_total: Money
    seller: PartyDocumentSnapshotV1
    customer: PartyDocumentSnapshotV1
    party_tax_identities: tuple[PartyTaxIdentitySnapshotV1, ...]
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...]
    fx_snapshot: AppliedFxSnapshotV1 | None
    tax_evidence_ref: str
    fx_evidence_ref: str
    document_profile_code: str
    document_profile_version: str
    payment_instructions: PaymentInstructionsSnapshotV1
    brand_asset: PresentationAssetReferenceV1
    locale: str
    timezone: str


@dataclass(frozen=True, slots=True)
class OpeningReplayBindingV1(OpeningCashValue):
    operation: Literal[
        "billing.withholding.accept",
        "billing.withholding.tax_component",
        "billing.non_cash_credit.grant",
        "billing.non_cash_credit.apply",
        "billing.credit_note.issue",
    ]
    source_owner: str
    source_ref: str
    evidence_digest: str
    determination_ref: str | None = None
    component_ref: str | None = None


@dataclass(frozen=True, slots=True)
class OpeningHistoricalDischargeV1(OpeningCashValue):
    """Signed component from the canonical source resolver, never live funding.

    A refund/inverse carries a negative amount and its concrete original ref.
    Exact namespace mappings are evidence, never synthesized legacy keys.
    `reference` identifies the application/effect; source_system/source_key
    identify its parent receipt/credit. Only exact minor-unit attribution is
    representable here. A source resolver's fractional attribution must remain
    a counted source blocker rather than inventing per-edge penny allocation.
    """

    reference: str
    kind: Literal[
        "payment",
        "credit_note",
        "prepaid_opening",
        "refund",
        "withholding",
        "non_cash_credit",
        "reversal",
        "adjustment",
    ]
    amount_minor: int
    economic_at: datetime
    recorded_at: datetime
    source_system: str
    source_key: str
    live_operation: str
    live_identity: str
    mapping_version: str
    ledger_ref: str
    evidence_digest: str
    tax_evidence_ref: str
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...]
    reverses_ref: str | None
    replay_bindings: tuple[OpeningReplayBindingV1, ...] = ()


@dataclass(frozen=True, slots=True)
class OpeningReceivableItemV1(OpeningCashValue):
    source_identity: OpeningSourceIdentityV1
    partition: OpeningPartitionV1
    source_invoice_key: str
    original: OpeningOriginalDocumentV1 | None
    historical_discharges: tuple[OpeningHistoricalDischargeV1, ...]
    residual_minor: int
    collection: OpeningCollectionEvidenceV1
    receivable_mapping_version: str
    tax_mapping_version: str
    source_resolver_version: str
    source_evidence_digest: str


@dataclass(frozen=True, slots=True)
class OpeningManifestV2(OpeningCashValue):
    scope: Scope
    capture: OpeningCaptureV1
    population: tuple[OpeningSourceIdentityV1, ...]
    dispositions: tuple[OpeningDispositionV1, ...]
    counts: OpeningPopulationCountsV1
    controls: tuple[OpeningLaneControlsV1, ...]
    cash_lots: tuple[OpeningCashLotV1, ...]
    receivables: tuple[OpeningReceivableItemV1, ...]


@dataclass(frozen=True, slots=True)
class ImportOpeningAccountV2(OpeningCashValue):
    scope: Scope
    manifest: OpeningManifestV2
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    migration_authority_ref: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningImportVerificationRequestV2(OpeningCashValue):
    command: ImportOpeningAccountV2
    command_digest: str
    manifest_fingerprint: str


@dataclass(frozen=True, slots=True)
class OpeningImportVerificationV2(OpeningCashValue):
    operation: Literal["billing.opening.import.v2"]
    scope: Scope
    command_digest: str
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    installation_id: str
    migration_authority_ref: str
    source_authority_ref: str
    source_binding_proof_ref: str
    cash_lifecycle_proof_ref: str
    receivable_lifecycle_proof_ref: str
    documentary_tax_mapping_proof_ref: str
    historical_discharge_mapping_proof_ref: str
    migration_authority_proof_ref: str


class OpeningAccountImportVerifierV2(Protocol):
    def verify_import_v2(
        self, request: OpeningImportVerificationRequestV2
    ) -> OpeningImportVerificationV2 | None: ...

    def verify_import_reversal_v2(
        self, request: OpeningImportReversalVerificationRequestV2
    ) -> OpeningImportReversalVerificationV2 | None: ...


@dataclass(frozen=True, slots=True)
class OpeningImportReversalVerificationRequestV2(OpeningCashValue):
    command: ReverseOpeningAccountImportV1
    command_digest: str
    receipt_evidence_digest: str
    original_posting_group_id: UUID
    billing_account_id: UUID
    currency: str
    precision: int


@dataclass(frozen=True, slots=True)
class OpeningImportReversalVerificationV2(OpeningCashValue):
    operation: Literal["billing.opening.import.reverse.v2"]
    scope: Scope
    command_digest: str
    receipt_id: UUID
    receipt_evidence_digest: str
    original_posting_group_id: UUID
    billing_account_id: UUID
    currency: str
    precision: int
    migration_authority_ref: str
    migration_authority_proof_ref: str


@dataclass(frozen=True, slots=True)
class OpeningImportPreviewV2(OpeningCashValue):
    operation: Literal["billing.opening.import.v2"]
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    admissible: bool
    cash_residual_minor: int
    receivable_residual_minor: int
    blockers: tuple[OpeningBlockerV1, ...]

    @property
    def blocker_count(self) -> int:
        return sum(item.count for item in self.blockers)


@dataclass(frozen=True, slots=True)
class OpeningReceivableBasisV1(OpeningCashValue):
    scope: Scope
    basis_id: UUID
    receipt_id: UUID
    document_id: UUID
    billing_account_id: UUID
    posting_group_id: UUID
    source_version: int
    sequence: int
    installation_id: str
    source_system: str
    source_invoice_key: str
    source: OpeningReceivableItemV1
    initial_receivable: Money
    manifest_fingerprint: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningReceivablePositionV1(OpeningCashValue):
    basis: OpeningReceivableBasisV1
    remaining: Money
    posting_watermark: UUID
    source_version: int
    unwound: bool


@dataclass(frozen=True, slots=True)
class ReceivableExposureV2(OpeningCashValue):
    scope: Scope
    source_owner: str
    source_authority: Literal["internal"]
    projection_mode: Literal["authoritative"]
    derived_from: Literal["posting_groups"]
    completeness: Literal["complete"]
    completeness_reason_code: str | None
    exposure_ref: str
    document_id: UUID
    billing_account_id: UUID
    basis_id: UUID
    source_version: int
    posting_group_watermark: UUID
    collection: OpeningCollectionEvidenceV1
    financial_state: Literal["open", "partially_resolved", "resolved", "cancelled"]
    collectible_receivable: Money
    state_fingerprint: str
    observed_at: datetime

    @property
    def automated_collection_allowed(self) -> bool:
        evidence = self.collection
        return (
            self.source_owner == "dotmac-billing"
            and self.source_authority == "internal"
            and self.projection_mode == "authoritative"
            and self.derived_from == "posting_groups"
            and self.completeness == "complete"
            and self.completeness_reason_code is None
            and evidence.automated_collection_allowed
            and self.financial_state in {"open", "partially_resolved"}
            and self.collectible_receivable.amount > 0
            and evidence.due_at is not None
            and evidence.due_at <= self.observed_at
            and (
                evidence.collection_timing != "advance"
                or (
                    evidence.service_period.status is ServicePeriodStatus.VERIFIED
                    and evidence.service_period.starts_at is not None
                    and evidence.service_period.starts_at <= self.observed_at
                )
            )
        )


@dataclass(frozen=True, slots=True)
class OpeningImportReceiptV2(OpeningCashValue):
    scope: Scope
    receipt_id: UUID
    receipt_kind: Literal["import", "unwind"]
    billing_account_id: UUID
    manifest: OpeningManifestV2
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    verification: OpeningImportVerificationV2 | OpeningImportReversalVerificationV2
    total_residual: Money
    total_receivable: Money
    lot_ids: tuple[UUID, ...]
    basis_ids: tuple[UUID, ...]
    posting_group_id: UUID
    original_receipt_id: UUID | None
    request_identity: str
    request_digest: str
    occurred_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class AccountingFactV5(OpeningCashValue):
    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: Literal[5]
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal"]
    effect_kind: Literal["opening_account_imported", "opening_account_import_reversed"]
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    import_receipt: OpeningImportReceiptV2
    receivable_bases: tuple[OpeningReceivableBasisV1, ...]
    opening_cash_effects: tuple[OpeningCashEffectV1, ...]
    corrects_posting_group_id: UUID | None
