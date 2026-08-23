"""Persistence-free financial rules shared by both Billing planes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from dotmac_billing.errors import BillingRuleViolation


class EffectLane(str, Enum):
    RECEIVABLE = "receivable"
    AVAILABLE_CREDIT = "available_credit"
    PREPAID_FUNDING = "prepaid_funding"


class CoverageOutcome(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERPAID = "overpaid"


@dataclass(frozen=True, slots=True)
class Effect:
    lane: EffectLane
    amount: Decimal
    currency: str
    minor_units: int


@dataclass(frozen=True, slots=True)
class PositionState:
    collectible_receivable: Decimal
    available_credit: Decimal
    prepaid_funding: Decimal
    currency: str
    minor_units: int
    state_fingerprint: str


def require_same_currency(*, expected: str, offered: str) -> None:
    if expected != offered:
        raise BillingRuleViolation(
            "cross_currency_allocation",
            "cross-currency allocation is refused in Billing V1",
            expected=expected,
            offered=offered,
        )


def coverage_of(total: Decimal, allocated: Decimal, dust: Decimal) -> CoverageOutcome:
    if dust < 0:
        raise BillingRuleViolation("negative_dust", "coverage dust cannot be negative")
    remaining = total - allocated
    if allocated <= dust:
        return CoverageOutcome.UNPAID
    if remaining > dust:
        return CoverageOutcome.PARTIAL
    if allocated - total > dust:
        return CoverageOutcome.OVERPAID
    return CoverageOutcome.PAID


def rebuild_position(
    effects: Iterable[Effect], *, currency: str, minor_units: int
) -> PositionState:
    totals = {lane: Decimal("0") for lane in EffectLane}
    for effect in effects:
        require_same_currency(expected=currency, offered=effect.currency)
        if effect.minor_units != minor_units:
            raise BillingRuleViolation(
                "minor_units_mismatch",
                "one currency cannot carry two minor-unit precisions",
            )
        totals[effect.lane] += effect.amount
    canonical = {
        "available_credit": format(totals[EffectLane.AVAILABLE_CREDIT], "f"),
        "collectible_receivable": format(totals[EffectLane.RECEIVABLE], "f"),
        "currency": currency,
        "minor_units": minor_units,
        "prepaid_funding": format(totals[EffectLane.PREPAID_FUNDING], "f"),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PositionState(
        collectible_receivable=totals[EffectLane.RECEIVABLE],
        available_credit=totals[EffectLane.AVAILABLE_CREDIT],
        prepaid_funding=totals[EffectLane.PREPAID_FUNDING],
        currency=currency,
        minor_units=minor_units,
        state_fingerprint=digest,
    )


__all__ = [
    "BillingRuleViolation",
    "CoverageOutcome",
    "Effect",
    "EffectLane",
    "PositionState",
    "coverage_of",
    "rebuild_position",
    "require_same_currency",
]
