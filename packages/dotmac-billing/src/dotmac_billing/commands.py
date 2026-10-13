"""Typed in-process commands for Billing's own lifecycle.

These are service inputs, not published cross-application allocation/coverage
contracts. The versioned external surfaces live in ``contracts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money
from sqlalchemy.orm import Session

from dotmac_billing.contracts import (
    AppliedFxSnapshotV1,
    AppliedTaxSnapshotV1,
    DueDateBasisV1,
    PartyDocumentSnapshotV1,
    PartyTaxIdentitySnapshotV1,
    PaymentInstructionsSnapshotV1,
    PresentationAssetReferenceV1,
    WithholdingCertificateObservationV1,
    WithholdingTaxEvidenceV1,
)


class NumberingProvider(Protocol):
    """Assembly adapter over the independently owned numbering capability."""

    def allocate(
        self,
        db: Session,
        *,
        scope: Scope,
        series_code: str,
        reference_date: date,
        idempotency_key: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class CreateDraftDocument:
    obligation_id: UUID
    description: str
    quantity: Decimal
    unit_code: str
    seller_snapshot: PartyDocumentSnapshotV1
    customer_snapshot: PartyDocumentSnapshotV1
    payment_instructions: PaymentInstructionsSnapshotV1
    brand_asset: PresentationAssetReferenceV1
    locale: str
    timezone: str
    document_profile_code: str
    document_profile_version: str
    due_date_basis: DueDateBasisV1
    party_tax_identities: tuple[PartyTaxIdentitySnapshotV1, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftDocumentLine:
    obligation_id: UUID
    description: str
    quantity: Decimal
    unit_code: str
    unit_amount: Money


@dataclass(frozen=True, slots=True)
class CreateMultiLineDraftDocument:
    billing_account_id: UUID
    idempotency_key: str
    lines: tuple[DraftDocumentLine, ...]
    seller_snapshot: PartyDocumentSnapshotV1
    customer_snapshot: PartyDocumentSnapshotV1
    payment_instructions: PaymentInstructionsSnapshotV1
    brand_asset: PresentationAssetReferenceV1
    locale: str
    timezone: str
    document_profile_code: str
    document_profile_version: str
    due_date_basis: DueDateBasisV1
    party_tax_identities: tuple[PartyTaxIdentitySnapshotV1, ...] = ()


@dataclass(frozen=True, slots=True)
class IssueDocument:
    document_id: UUID
    series_code: str
    reference_date: date
    due_at: datetime | None
    due_date_basis: DueDateBasisV1
    actor_ref: str
    correlation_id: str
    native: bool = True
    collectible: bool = True


@dataclass(frozen=True, slots=True)
class IssueCreditNote:
    original_document_id: UUID
    pre_tax_amount: Money
    tax_amount: Money
    total_amount: Money
    reason: str
    series_code: str
    reference_date: date
    actor_ref: str
    correlation_id: str
    occurred_at: datetime
    document_profile_code: str
    document_profile_version: str
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...] = ()
    fx_snapshot: AppliedFxSnapshotV1 | None = None


@dataclass(frozen=True, slots=True)
class AllocationCommand:
    settlement_id: UUID
    document_id: UUID
    amount: Money
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class ApplyAvailableCreditCommand:
    billing_account_id: UUID
    document_id: UUID
    amount: Money
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class DeallocationCommand:
    allocation_id: UUID
    amount: Money
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class ReallocationCommand:
    settlement_id: UUID
    from_document_id: UUID
    to_document_id: UUID
    amount: Money
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class RefundCommand:
    settlement_id: UUID
    amount: Money
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class ReversePostingGroupCommand:
    posting_group_id: UUID
    occurred_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class AcceptWithholdingDischarge:
    scope: Scope
    billing_account_id: UUID
    document_id: UUID
    idempotency_key: str
    source_owner: str
    source_ref: str
    claim_ref: str
    claimed_amount: Money
    accepted_amount: Money
    tax_evidence: WithholdingTaxEvidenceV1
    authorization_actor_ref: str
    authorization_evidence_ref: str
    occurred_at: datetime
    certificate_observation: WithholdingCertificateObservationV1 | None = None
    settlement_id: UUID | None = None
    allocation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReverseWithholdingDischarge:
    scope: Scope
    discharge_id: UUID
    idempotency_key: str
    source_ref: str
    authorization_actor_ref: str
    authorization_evidence_ref: str
    occurred_at: datetime


__all__ = [
    "AcceptWithholdingDischarge",
    "ReverseWithholdingDischarge",
    "AllocationCommand",
    "ApplyAvailableCreditCommand",
    "CreateDraftDocument",
    "CreateMultiLineDraftDocument",
    "DraftDocumentLine",
    "DeallocationCommand",
    "IssueCreditNote",
    "IssueDocument",
    "NumberingProvider",
    "ReallocationCommand",
    "RefundCommand",
    "ReversePostingGroupCommand",
]
