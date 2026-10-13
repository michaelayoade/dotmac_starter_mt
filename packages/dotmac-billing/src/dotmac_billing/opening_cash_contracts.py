"""Immutable opening cash facts and derived room; recognition is not new cash."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_billing._opening_values import (
    OpeningCashValue,
    minor_units_of,
    money_from_minor,
)
from dotmac_billing.contracts import AccountingAllocationEffectV1, AccountingEffectV1
from dotmac_billing.opening_authority import (
    OpeningCashRefundCorrectionVerificationV1,
    OpeningCashRefundVerificationV1,
    OpeningImportReversalVerificationV1,
    OpeningImportVerificationV1,
)
from dotmac_billing.opening_commands import OpeningCashMovementEvidenceV1
from dotmac_billing.opening_contracts import (
    OpeningCashLotV1,
    OpeningEvidenceError,
    OpeningManifestV1,
    OpeningPartitionV1,
    OpeningTargetV1,
)

OpeningCashEffectKind = Literal[
    "recognition",
    "application",
    "application_inverse",
    "refund",
    "refund_correction",
    "import_unwind",
]
OpeningCashPostingKind = Literal[
    "opening_cash_imported",
    "opening_cash_applied",
    "cash_credit_draw_reversed",
    "opening_cash_refunded",
    "opening_cash_refund_corrected",
    "opening_cash_import_reversed",
]


@dataclass(frozen=True, slots=True)
class OpeningImportReceiptV1(OpeningCashValue):
    scope: Scope
    receipt_id: UUID
    receipt_kind: Literal["import", "unwind"]
    billing_account_id: UUID
    manifest: OpeningManifestV1
    manifest_fingerprint: str
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    verification: OpeningImportVerificationV1 | OpeningImportReversalVerificationV1
    total_residual: Money
    lot_ids: tuple[UUID, ...]
    posting_group_id: UUID
    original_receipt_id: UUID | None
    request_identity: str
    request_digest: str
    occurred_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningCashLotFactV1(OpeningCashValue):
    scope: Scope
    lot_id: UUID
    receipt_id: UUID
    billing_account_id: UUID
    posting_group_id: UUID
    sequence: int
    source: OpeningCashLotV1
    initial_amount: Money
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningCashEffectV1(OpeningCashValue):
    scope: Scope
    effect_id: UUID
    lot_id: UUID
    billing_account_id: UUID
    posting_group_id: UUID
    sequence: int
    effect_kind: OpeningCashEffectKind
    amount: Money
    document_id: UUID | None
    original_effect_id: UUID | None
    source_ref: str
    authorization_ref: str
    movement_evidence: OpeningCashMovementEvidenceV1 | None
    movement_verification: (
        OpeningCashRefundVerificationV1
        | OpeningCashRefundCorrectionVerificationV1
        | None
    )
    request_digest: str
    occurred_at: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if minor_units_of(self.amount) < 0 or (
            minor_units_of(self.amount) == 0 and self.effect_kind != "recognition"
        ):
            raise OpeningEvidenceError(
                "opening effect requires a positive exact amount"
            )
        if (self.effect_kind in {"application", "application_inverse"}) != (
            self.document_id is not None
        ):
            raise OpeningEvidenceError("opening application requires its exact invoice")
        if (self.effect_kind in {"application_inverse", "refund_correction"}) != (
            self.original_effect_id is not None
        ):
            raise OpeningEvidenceError("opening inverse requires its concrete original")

    @property
    def availability_delta(self) -> Money:
        sign = (
            -1 if self.effect_kind in {"application", "refund", "import_unwind"} else 1
        )
        return money_from_minor(
            sign * minor_units_of(self.amount),
            self.amount.currency.code,
            self.amount.currency.minor_units,
        )


@dataclass(frozen=True, slots=True)
class OpeningCashLotPositionV1(OpeningCashValue):
    lot: OpeningCashLotFactV1
    remaining: Money
    posting_watermark: UUID | None
    unwound: bool


@dataclass(frozen=True, slots=True)
class AccountingSettlementAllocationV4(OpeningCashValue):
    allocation_effect_id: UUID
    allocation: AccountingAllocationEffectV1


@dataclass(frozen=True, slots=True)
class CashCreditSourceOrderV1(OpeningCashValue):
    """Every concrete V4 source effect, including recognition/refund/unwind.

    Exactly one entry per actual effect, in persisted financial source order.
    This ordering is broader than draw-only source selection.
    """

    sequence: int
    allocation_effect_id: UUID | None
    opening_effect_id: UUID | None

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if (self.allocation_effect_id is None) == (self.opening_effect_id is None):
            raise OpeningEvidenceError(
                "cash source order requires exactly one actual effect"
            )


@dataclass(frozen=True, slots=True)
class AccountingFactV4(OpeningCashValue):
    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: Literal[4]
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal"]
    effect_kind: OpeningCashPostingKind
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    settlement_allocations: tuple[AccountingSettlementAllocationV4, ...]
    opening_cash_effects: tuple[OpeningCashEffectV1, ...]
    cash_source_order: tuple[CashCreditSourceOrderV1, ...]
    import_receipt: OpeningImportReceiptV1 | None
    corrects_posting_group_id: UUID | None
