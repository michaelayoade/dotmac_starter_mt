"""Pure contract canaries; execution belongs to hosted CI."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from dotmac_billing import CashCreditDrawState, GetCurrentCashCreditDrawV1
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money


@pytest.mark.parametrize("scope", [PlatformScope(), TenantScope(uuid4())])
def test_exact_draw_request_validation(scope):
    request = GetCurrentCashCreditDrawV1(
        scope, uuid4(), uuid4(), uuid4(), Money.of("1", Currency("NGN", 2)), "renewal:1"
    )
    for field, value in (
        ("scope", None),
        ("billing_account_id", "bad"),
        ("document_id", None),
        ("source_ref", ""),
        ("source_ref", " " * 3),
        ("amount", Money.of("0", Currency("NGN", 2))),
    ):
        with pytest.raises(BillingRuleViolation):
            replace(request, **{field: value})
    with pytest.raises(FrozenInstanceError):
        request.source_ref = "changed"
    assert {state.value for state in CashCreditDrawState} == {
        "current",
        "reduced",
        "removed",
        "inactive_backing",
    }


@pytest.mark.parametrize("scope", [PlatformScope(), TenantScope(uuid4())])
def test_current_receivable_position_request_is_exact(scope):
    from dotmac_billing import GetCurrentReceivablePositionV1

    request = GetCurrentReceivablePositionV1(scope, uuid4(), Currency("NGN", 2))
    for field, value in (
        ("scope", None),
        ("billing_account_id", "bad"),
        ("currency", "NGN"),
        ("currency", Currency("NGN", 7)),
    ):
        with pytest.raises(BillingRuleViolation):
            replace(request, **{field: value})
