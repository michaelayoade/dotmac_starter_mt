"""Whole-settlement earmark inputs retain exact immutable source identity."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_billing import (
    EarmarkSettlementV1,
    GetFundingAvailabilityV1,
    SettlementEarmarkTargetV1,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money

NOW = datetime(2026, 10, 16, tzinfo=UTC)


def cash(amount="100", precision=2):
    return Money.of(amount, Currency("NGN", precision))


def target(amount="100", source="period:1"):
    return SettlementEarmarkTargetV1("sub", "rated_period", source, "1", cash(amount))


def claim():
    return EarmarkSettlementV1(
        TenantScope(uuid4()),
        uuid4(),
        uuid4(),
        cash(),
        "purchase:1",
        (target(),),
        "earmark:1",
        NOW,
    )


def test_whole_claim_is_frozen_and_allows_two_exact_period_targets():
    command = replace(claim(), targets=(target("40"), target("60", "period:2")))
    assert sum(item.amount.amount for item in command.targets) == command.amount.amount
    with pytest.raises(FrozenInstanceError):
        command.producer_ref = "purchase:2"
    with pytest.raises(FrozenInstanceError):
        command.targets[0].source_fact_version = "2"


def test_empty_targets_hold_exceptional_capture_without_automatic_eligibility():
    assert replace(claim(), targets=()).targets == ()


@pytest.mark.parametrize(
    "change",
    [
        {"targets": (target("99"),)},
        {"targets": (target("50"), target("50"))},
        {"targets": [target()]},
        {"amount": cash("0")},
        {"amount": cash("-100")},
        {"amount": cash("100", 7)},
        {"producer_ref": " "},
        {"source_ref": ""},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"billing_account_id": "not-uuid"},
        {"settlement_id": "not-uuid"},
        {"scope": "platform"},
    ],
)
def test_invalid_claim_shape_refuses_without_rounding_or_partial_claim(change):
    with pytest.raises(BillingRuleViolation):
        replace(claim(), **change)


def test_target_money_currency_precision_and_identity_are_exact():
    for field in (
        "source_system",
        "source_kind",
        "source_fact_id",
        "source_fact_version",
    ):
        with pytest.raises(BillingRuleViolation):
            replace(target(), **{field: " "})
    with pytest.raises(BillingRuleViolation):
        replace(
            claim(),
            targets=(replace(target(), amount=Money.of("100", Currency("USD", 2))),),
        )
    with pytest.raises(BillingRuleViolation):
        replace(claim(), targets=(replace(target(), amount=cash("100", 3)),))


def test_availability_request_is_scoped_and_optionally_source_specific():
    account_id, settlement_id = uuid4(), uuid4()
    request = GetFundingAvailabilityV1(PlatformScope(), account_id, settlement_id)
    assert replace(request, settlement_id=None).billing_account_id == account_id
    with pytest.raises(FrozenInstanceError):
        request.settlement_id = None
    for field in ("billing_account_id", "settlement_id"):
        with pytest.raises(BillingRuleViolation):
            replace(request, **{field: "not-uuid"})


def test_forged_release_change_is_revalidated_before_it_can_reach_verifier():
    from dotmac_billing import CorrectSettlementEarmarkV1, ReleaseEarmarkV1

    scope, earmark_id = TenantScope(uuid4()), uuid4()
    for field, invalid in (
        ("amount", cash("-60")),
        ("occurred_at", NOW.replace(tzinfo=None)),
        ("source_ref", " "),
    ):
        release = ReleaseEarmarkV1(scope, earmark_id, cash("60"), "release:1", NOW)
        object.__setattr__(release, field, invalid)
        with pytest.raises(BillingRuleViolation):
            CorrectSettlementEarmarkV1(scope, (earmark_id,), release)


def test_exact_money_is_rechecked_and_target_sums_ignore_ambient_decimal_precision():
    from decimal import Decimal, localcontext

    forged = cash()
    object.__setattr__(forged, "amount", Decimal("100.001"))
    with pytest.raises(BillingRuleViolation):
        replace(target(), amount=forged)
    command = claim()
    exact_targets = (target("40"), target("60", "period:2"))
    with localcontext() as context:
        context.prec = 1
        assert replace(command, targets=exact_targets).amount == command.amount
