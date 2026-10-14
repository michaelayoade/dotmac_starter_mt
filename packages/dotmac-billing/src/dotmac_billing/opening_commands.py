"""Closed cash-only opening commands; none is caller-supplied authorization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_billing._opening_values import OpeningCashValue, minor_units_of
from dotmac_billing.opening_contracts import (
    OpeningEvidenceError,
    OpeningManifestV1,
    OpeningPartitionV1,
    OpeningTargetV1,
)


@dataclass(frozen=True, slots=True)
class ImportOpeningAccountV1(OpeningCashValue):
    scope: Scope
    manifest: OpeningManifestV1
    partition: OpeningPartitionV1
    target: OpeningTargetV1
    migration_authority_ref: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OpeningCashMovementEvidenceV1(OpeningCashValue):
    owner: str
    reference: str
    version: str
    digest: str
    outcome: str


@dataclass(frozen=True, slots=True)
class RecordOpeningCashRefundV1(OpeningCashValue):
    scope: Scope
    lot_id: UUID
    amount: Money
    evidence: OpeningCashMovementEvidenceV1
    authorization_ref: str
    idempotency_key: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if minor_units_of(self.amount) <= 0:
            raise OpeningEvidenceError("opening refund requires positive exact money")


@dataclass(frozen=True, slots=True)
class CorrectOpeningCashRefundV1(OpeningCashValue):
    scope: Scope
    refund_effect_id: UUID
    amount: Money
    evidence: OpeningCashMovementEvidenceV1
    authorization_ref: str
    idempotency_key: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if minor_units_of(self.amount) <= 0:
            raise OpeningEvidenceError(
                "refund correction requires positive exact money"
            )


@dataclass(frozen=True, slots=True)
class ReverseCashCreditDrawV1(OpeningCashValue):
    scope: Scope
    draw_posting_group_id: UUID
    amount: Money
    authorization_ref: str
    idempotency_key: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        if minor_units_of(self.amount) <= 0:
            raise OpeningEvidenceError("draw inverse requires positive exact money")


@dataclass(frozen=True, slots=True)
class ReverseOpeningAccountImportV1(OpeningCashValue):
    scope: Scope
    receipt_id: UUID
    migration_authority_ref: str
    idempotency_key: str
    occurred_at: datetime
