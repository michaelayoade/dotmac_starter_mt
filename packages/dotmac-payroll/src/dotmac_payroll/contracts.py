"""Typed payroll configuration and calculation commands."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Money


@dataclass(frozen=True, slots=True)
class PayComponentInput:
    component_code: str
    name: str
    kind: str
    expense_account_ref: str | None
    liability_account_ref: str | None
    liability_destination_ref: str | None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class PayRuleInput:
    component_id: UUID
    sequence: int
    calculation_method: str
    fixed_amount: Money | None
    rate: Decimal | None
    basis_component_ids: tuple[UUID, ...]
    prorates: bool


@dataclass(frozen=True, slots=True)
class EmployeeComponentInput:
    component_id: UUID
    amount: Money
    evidence_ref: str


__all__ = ["EmployeeComponentInput", "PayComponentInput", "PayRuleInput"]
