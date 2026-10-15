"""Typed projection boundary for Sub's serialized V2 observation export.

Adapters retain these fields from the actual export, including complete cash
source populations; they do not qualify cash or attest source truth. Mapping
source accounts to Starter partitions remains the assembly's explicit job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, TenantScope

from dotmac_billing._opening_values import OpeningCashValue
from dotmac_billing.opening_contracts import (
    OpeningBlockerV1,
    OpeningEvidenceError,
    OpeningPartitionV1,
    OpeningSourceIdentityV1,
)
from dotmac_billing.opening_receivable_contracts import (
    ImportOpeningAccountV2,
    OpeningImportPreviewV2,
    OpeningManifestV2,
)

__all__ = [
    "OpeningObservedInvoiceV1",
    "OpeningObservedSettlementV1",
    "OpeningRehearsalInvoiceMappingV1",
    "OpeningRehearsalDischargeMappingV1",
    "OpeningObservedControlV1",
    "OpeningObservedLaneV1",
    "OpeningObservedPrecisionV1",
    "OpeningObservedPopulationV1",
    "OpeningSubObservationV2",
    "OpeningRehearsalMappingV1",
    "OpeningRehearsalBindingV1",
    "OpeningRehearsalRunV1",
    "OpeningRehearsalDeltaV1",
    "OpeningRehearsalReportV1",
    "OpeningRehearsalComparisonV1",
    "OpeningRehearsalAssemblyPartitionV1",
    "OpeningRehearsalAssemblyV1",
    "OpeningRehearsalCensusBlockerV1",
    "OpeningRehearsalCensusDeltaV1",
    "OpeningRehearsalCensusPartitionV1",
    "OpeningRehearsalCensusProvenanceV1",
    "OpeningRehearsalCensusCountV1",
    "OpeningRehearsalCensusUnmappedV1",
    "OpeningRehearsalCensusSidecarV1",
    "OpeningRehearsalCensusRollupV1",
    "OpeningRehearsalCensusV1",
]


@dataclass(frozen=True, slots=True)
class OpeningObservedInvoiceV1(OpeningCashValue):
    """Available financial fields of actual Sub InvoiceOpeningEvidence."""

    invoice_id: UUID
    account_id: UUID
    currency: str
    precision: int | None
    invoice_number: str | None
    issued_at: datetime | None
    total: Decimal
    canonical_discharge: Decimal
    residual: Decimal
    settlement_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class OpeningObservedSettlementV1(OpeningCashValue):
    """Actual Sub SettlementEvidence; no inferred WHT/noncash attribution."""

    kind: str
    id: UUID
    invoice_id: UUID
    source_id: UUID
    amount: Decimal
    active: bool


@dataclass(frozen=True, slots=True)
class OpeningRehearsalInvoiceMappingV1(OpeningCashValue):
    """Explicit assembly mapping, not a field asserted by the Sub observation."""

    source_invoice_id: UUID
    source_identity: OpeningSourceIdentityV1
    source_invoice_key: str


@dataclass(frozen=True, slots=True)
class OpeningRehearsalDischargeMappingV1(OpeningCashValue):
    """Declared source-to-manifest mapping; never financial verification."""

    settlement_kind: str
    settlement_id: UUID
    historical_reference: str
    source_id: UUID


@dataclass(frozen=True, slots=True)
class OpeningObservedControlV1(OpeningCashValue):
    account_id: UUID | None
    currency: str | None
    invoice_count: int
    eligible_count: int
    excluded_count: int
    exception_count: int
    original_total: Decimal
    canonical_discharge: Decimal
    residual: Decimal
    eligible_residual: Decimal
    source_count: int


@dataclass(frozen=True, slots=True)
class OpeningObservedLaneV1(OpeningCashValue):
    account_id: UUID
    currency: str
    fingerprint: str
    authority: str
    authority_cutover_id: UUID | None
    posting_group_count: int
    collectible_receivable: Decimal
    unapplied_customer_credit: Decimal
    prepaid_funding_reserved: Decimal
    prepaid_funding_consumed: Decimal
    written_off_total: Decimal
    refunded_total: Decimal
    adjustment_total: Decimal


@dataclass(frozen=True, slots=True)
class OpeningObservedPopulationV1(OpeningCashValue):
    table: str
    count: int
    identities: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class OpeningObservedPrecisionV1(OpeningCashValue):
    currency: str
    precision: int


@dataclass(frozen=True, slots=True)
class OpeningSubObservationV2(OpeningCashValue):
    source_repository_sha: str
    installation_id: UUID
    schema_heads: tuple[str, ...]
    snapshot_id: str
    captured_at: datetime
    economic_cutoff: datetime
    isolation: str
    population_version: str
    population_complete: bool
    currency_precisions: tuple[OpeningObservedPrecisionV1, ...]
    content_fingerprint: str
    invoice_ids: tuple[UUID, ...]
    eligible_invoice_ids: tuple[UUID, ...]
    controls: tuple[OpeningObservedControlV1, ...]
    lanes: tuple[OpeningObservedLaneV1, ...]
    cash_populations: tuple[OpeningObservedPopulationV1, ...]
    qualified_payment_ids: tuple[UUID, ...]
    blockers: tuple[OpeningBlockerV1, ...]
    invoices: tuple[OpeningObservedInvoiceV1, ...] = ()
    settlements: tuple[OpeningObservedSettlementV1, ...] = ()


@dataclass(frozen=True, slots=True)
class OpeningRehearsalMappingV1(OpeningCashValue):
    source_account_id: UUID
    partition: OpeningPartitionV1


@dataclass(frozen=True, slots=True)
class OpeningRehearsalBindingV1(OpeningCashValue):
    """Actual assembly artifact and policy coordinates, not approval evidence."""

    target_installation_id: UUID
    artifact_identity: str
    policy_identity: str


@dataclass(frozen=True, slots=True)
class OpeningRehearsalRunV1(OpeningCashValue):
    source: OpeningSubObservationV2
    manifest: OpeningManifestV2
    mappings: tuple[OpeningRehearsalMappingV1, ...]
    commands: tuple[ImportOpeningAccountV2, ...]
    previews: tuple[OpeningImportPreviewV2, ...]
    binding: OpeningRehearsalBindingV1
    accepted_manual_holds: tuple[str, ...] = ()
    invoice_mappings: tuple[OpeningRehearsalInvoiceMappingV1, ...] = ()
    discharge_mappings: tuple[OpeningRehearsalDischargeMappingV1, ...] = ()


@dataclass(frozen=True, slots=True)
class OpeningRehearsalDeltaV1(OpeningCashValue):
    partition: OpeningPartitionV1
    lane: Literal["receivable", "cash"]
    source_minor: int | None
    preview_minor: int
    unclassified_delta_minor: int | None


@dataclass(frozen=True, slots=True)
class OpeningRehearsalReportV1(OpeningCashValue):
    content_digest: str
    binding_digest: str
    source_snapshot_id: str
    source_installation_id: UUID
    economic_cutoff: datetime
    recorded_cutoff: datetime
    watermarks: tuple[str, ...]
    complete_exact_reconciliation: bool
    blockers: tuple[OpeningBlockerV1, ...]
    deltas: tuple[OpeningRehearsalDeltaV1, ...]
    accepted_manual_holds: tuple[str, ...]
    activation_ready: Literal[False] = False


@dataclass(frozen=True, slots=True)
class OpeningRehearsalComparisonV1(OpeningCashValue):
    content_digest: str
    mode: Literal["snapshot_repeatability", "successive_reconciliations"]
    candidate_evidence_complete: bool
    blockers: tuple[OpeningBlockerV1, ...]
    report_digests: tuple[str, ...]
    activation_ready: Literal[False] = False


@dataclass(frozen=True, slots=True)
class OpeningRehearsalAssemblyPartitionV1(OpeningCashValue):
    """One declared source-account to Starter-partition mapping and pristine target."""

    source_account_id: UUID
    source_account_key: str
    account_id: UUID
    currency: str
    precision: int
    idempotency_key: str
    expected_position_fingerprint: str
    posting_watermark: str


@dataclass(frozen=True, slots=True)
class OpeningRehearsalAssemblyV1(OpeningCashValue):
    """The explicit, Michael-owned assembly declaration, as one closed value.

    Its identity is `opening_rehearsal_assembly_digest`, the single definition
    used by signing, the rehearsal adapter and any later verifier. A digest
    identifies this declaration; it is neither approval nor proof of its truth.
    """

    scope: TenantScope | PlatformScope
    source_system: str
    recorded_cutoff: datetime
    source_authority_ref: str
    account_mapping_version: str
    cash_mapping_version: str
    receivable_mapping_version: str
    tax_mapping_version: str
    source_resolver_version: str
    collection_authority: str
    collection_mapping_version: str
    migration_authority_ref: str
    occurred_at: datetime
    starter_head_sha: str
    sub_exit_code: int
    binding: OpeningRehearsalBindingV1
    partitions: tuple[OpeningRehearsalAssemblyPartitionV1, ...]
    invoice_mappings: tuple[OpeningRehearsalInvoiceMappingV1, ...]
    discharge_mappings: tuple[OpeningRehearsalDischargeMappingV1, ...]
    accepted_manual_holds: tuple[str, ...]
    schema: Literal["billing.opening.rehearsal.assembly.v1"] = (
        "billing.opening.rehearsal.assembly.v1"
    )

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        # Instants are UTC in the signed declaration; other offsets are refused
        # rather than normalised, so one instant has exactly one spelling.
        for instant in (self.recorded_cutoff, self.occurred_at):
            if instant.utcoffset() != timedelta(0):
                raise OpeningEvidenceError("assembly instants must be UTC")


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusBlockerV1(OpeningCashValue):
    code: str
    count: int
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusDeltaV1(OpeningCashValue):
    lane: Literal["receivable", "cash"]
    source_minor: int | None
    preview_minor: int
    unclassified_delta_minor: int | None


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusPartitionV1(OpeningCashValue):
    source_account_id: str
    source_account_key: str
    currency: str
    precision: int
    admissible: bool
    cash_residual_minor: int
    receivable_residual_minor: int
    blockers: tuple[OpeningRehearsalCensusBlockerV1, ...]
    deltas: tuple[OpeningRehearsalCensusDeltaV1, ...]


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusProvenanceV1(OpeningCashValue):
    sub_source_repository_sha: str
    starter_head_sha: str
    population_version: str
    content_fingerprint: str
    snapshot_id: str
    schema_heads: tuple[str, ...]
    captured_at: str
    sub_exit_code: int
    assembly_digest: str
    binding_digest: str
    report_content_digest: str
    manifest_fingerprint: str


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusCountV1(OpeningCashValue):
    key: str
    count: int


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusUnmappedV1(OpeningCashValue):
    accounts: tuple[str, ...]
    invoices: tuple[str, ...]
    settlements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusSidecarV1(OpeningCashValue):
    unavailable_count: int
    by_requirement: tuple[OpeningRehearsalCensusCountV1, ...]
    by_reason: tuple[OpeningRehearsalCensusCountV1, ...]
    invoice_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusRollupV1(OpeningCashValue):
    partitions_total: int
    partitions_admissible: int
    complete_exact_reconciliation: bool
    blocked_by_reason: tuple[OpeningRehearsalCensusCountV1, ...]
    accepted_manual_holds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpeningRehearsalCensusV1(OpeningCashValue):
    """Counted blocker census; never qualifies cash or activates authority."""

    schema: Literal["sub-starter-opening-rehearsal-census/v1"]
    provenance: OpeningRehearsalCensusProvenanceV1
    partitions: tuple[OpeningRehearsalCensusPartitionV1, ...]
    report_blockers: tuple[OpeningRehearsalCensusBlockerV1, ...]
    adapter_blockers: tuple[OpeningRehearsalCensusBlockerV1, ...]
    unmapped: OpeningRehearsalCensusUnmappedV1
    v3_documentary_sidecar: OpeningRehearsalCensusSidecarV1
    dropped_source_fields: tuple[str, ...]
    rollup: OpeningRehearsalCensusRollupV1

    @property
    def blocked(self) -> bool:
        return bool(
            self.report_blockers
            or self.adapter_blockers
            or self.unmapped.accounts
            or self.unmapped.invoices
            or self.unmapped.settlements
            or any(entry.blockers for entry in self.partitions)
        )
