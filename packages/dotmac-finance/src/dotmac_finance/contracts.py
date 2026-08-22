"""Typed commands and closed vocabulary for fixed-asset accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from uuid import UUID

from dotmac_kernel.money import Money


class DepreciationMethod(str, Enum):
    STRAIGHT_LINE = "straight_line"
    DECLINING_BALANCE = "declining_balance"
    DOUBLE_DECLINING = "double_declining"


class AccountingModel(str, Enum):
    COST = "cost"
    REVALUATION = "revaluation"


class BookStatus(str, Enum):
    ACTIVE = "active"
    DERECOGNIZED = "derecognized"


@dataclass(frozen=True, slots=True)
class AccountMapping:
    asset: str
    accumulated_depreciation: str
    accumulated_impairment: str
    depreciation_expense: str
    impairment_loss: str
    revaluation_reserve: str | None
    disposal_gain_loss: str
    cost_center: str | None = None

    def __post_init__(self) -> None:
        required = {
            "asset": self.asset,
            "accumulated_depreciation": self.accumulated_depreciation,
            "accumulated_impairment": self.accumulated_impairment,
            "depreciation_expense": self.depreciation_expense,
            "impairment_loss": self.impairment_loss,
            "disposal_gain_loss": self.disposal_gain_loss,
        }
        for label, value in required.items():
            if not value.strip():
                raise ValueError(f"{label} account reference must not be blank")
        if (
            self.revaluation_reserve is not None
            and not self.revaluation_reserve.strip()
        ):
            raise ValueError("revaluation reserve account reference must not be blank")


@dataclass(frozen=True, slots=True)
class CapitalizeAssetBook:
    asset_id: UUID
    book_code: str
    available_for_use_on: date
    acquisition_cost: Money
    functional_cost: Money
    residual_value: Money
    useful_life_months: int
    method: DepreciationMethod
    accounting_model: AccountingModel
    accounts: AccountMapping
    source_ref: str
    source_version: str
    evidence_ref: str
    actor_id: UUID


@dataclass(frozen=True, slots=True)
class ImpairmentCommand:
    book_id: UUID
    expected_version: int
    effective_on: date
    fair_value_less_costs_of_disposal: Money | None
    value_in_use: Money | None
    basis: str
    evidence_ref: str
    approval_ref: str
    requested_by_id: UUID
    approved_by_id: UUID


@dataclass(frozen=True, slots=True)
class RevaluationCommand:
    book_id: UUID
    expected_version: int
    effective_on: date
    fair_value: Money
    valuation_method: str
    evidence_ref: str
    approval_ref: str
    requested_by_id: UUID
    approved_by_id: UUID


@dataclass(frozen=True, slots=True)
class DisposalCommand:
    book_id: UUID
    expected_version: int
    asset_disposal_ref: str
    effective_on: date
    proceeds: Money
    costs_of_disposal: Money
    clearing_account_ref: str
    evidence_ref: str
    approval_ref: str
    requested_by_id: UUID
    approved_by_id: UUID


__all__ = [
    "AccountMapping",
    "AccountingModel",
    "BookStatus",
    "CapitalizeAssetBook",
    "DepreciationMethod",
    "DisposalCommand",
    "ImpairmentCommand",
    "RevaluationCommand",
]
