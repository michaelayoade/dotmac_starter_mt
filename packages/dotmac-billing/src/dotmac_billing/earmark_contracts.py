"""Immutable whole-receipt claims and current funding observations.

A producer reference is opaque provenance. It conveys neither correction
permission nor an editable reserved balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.money import Money

from dotmac_billing._opening_values import minor_units_of
from dotmac_billing.contracts import SettlementFundingLane
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningEvidenceError


def _invalid() -> None:
    raise BillingRuleViolation("earmark_input_invalid", "invalid exact earmark input")


def _identity(*values: str) -> None:
    if any(
        type(value) is not str or not value.strip() or len(value) > 255
        for value in values
    ):
        _invalid()


def _scope(scope: Scope, *identities: UUID) -> None:
    if (
        type(scope) not in (TenantScope, PlatformScope)
        or (type(scope) is TenantScope and type(scope.tenant_id) is not UUID)
        or any(type(value) is not UUID for value in identities)
    ):
        _invalid()


def _instant(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _invalid()


def _quantity(value: Money, *, positive: bool = True) -> int:
    try:
        quantity = minor_units_of(value)
    except OpeningEvidenceError as exc:
        raise BillingRuleViolation(
            "earmark_money_invalid", "earmark requires exact stored Money"
        ) from exc
    if quantity < 0 or (positive and quantity == 0):
        _invalid()
    return quantity


@dataclass(frozen=True, slots=True)
class SettlementEarmarkTargetV1:
    source_system: str
    source_kind: str
    source_fact_id: str
    source_fact_version: str
    amount: Money

    def __post_init__(self) -> None:
        _identity(
            self.source_system,
            self.source_kind,
            self.source_fact_id,
            self.source_fact_version,
        )
        if any(
            len(value) > 120
            for value in (
                self.source_system,
                self.source_kind,
                self.source_fact_version,
            )
        ):
            _invalid()
        _quantity(self.amount)

    @property
    def source_identity(self) -> tuple[str, str, str, str]:
        return (
            self.source_system,
            self.source_kind,
            self.source_fact_id,
            self.source_fact_version,
        )


@dataclass(frozen=True, slots=True)
class EarmarkSettlementV1:
    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID
    amount: Money
    producer_ref: str
    targets: tuple[SettlementEarmarkTargetV1, ...]
    source_ref: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _scope(self.scope, self.billing_account_id, self.settlement_id)
        _identity(self.producer_ref, self.source_ref)
        _instant(self.occurred_at)
        whole = _quantity(self.amount)
        if type(self.targets) is not tuple or any(
            type(item) is not SettlementEarmarkTargetV1 for item in self.targets
        ):
            _invalid()
        for item in self.targets:
            item.__post_init__()
            if item.amount.currency != self.amount.currency:
                _invalid()
        if len({item.source_identity for item in self.targets}) != len(self.targets):
            _invalid()
        if (
            self.targets
            and sum(_quantity(item.amount) for item in self.targets) != whole
        ):
            _invalid()


@dataclass(frozen=True, slots=True)
class SettlementEarmarkV1:
    earmark_id: UUID
    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID
    amount: Money
    producer_ref: str
    targets: tuple[SettlementEarmarkTargetV1, ...]
    source_ref: str
    occurred_at: datetime
    evidence_fingerprint: str


@dataclass(frozen=True, slots=True)
class GetFundingAvailabilityV1:
    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID | None = None

    def __post_init__(self) -> None:
        _scope(self.scope, self.billing_account_id)
        if self.settlement_id is not None and type(self.settlement_id) is not UUID:
            _invalid()


@dataclass(frozen=True, slots=True)
class FundingAvailabilityV1:
    """Current cash-only room; re-observe for each admission decision.

    Posted is unspent confirmed receipts plus supported opening cash lots.
    Noncash credit and withholding are excluded. Source queries select one receipt.
    The account lock remains owned by the caller transaction.
    """

    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID | None
    posted: Money
    held: Money
    unrestricted: Money
    availability_fingerprint: str


@dataclass(frozen=True, slots=True)
class GetConfirmedSettlementV1:
    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID

    def __post_init__(self) -> None:
        _scope(self.scope, self.billing_account_id, self.settlement_id)


@dataclass(frozen=True, slots=True)
class ConfirmedSettlementObservationV1:
    """Original admitted receipt scalars and current backing, never a new proof.

    Confirmation evidence is the classification stored by accept_settlement.
    This observation does not independently re-attest external provider evidence.
    """

    scope: Scope
    billing_account_id: UUID
    settlement_id: UUID
    amount: Money
    source_system: str
    source_settlement_key: str
    source_version: str
    request_fingerprint: str
    occurred_at: datetime
    observed_at: datetime
    confirmation_evidence: str
    funding_lane: SettlementFundingLane
    active_backing: bool
