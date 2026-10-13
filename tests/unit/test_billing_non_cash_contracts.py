"""Commercial grant identity remains ordinary non-tax deeply immutable evidence."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_billing.contracts import (
    NonCashCreditClassificationV1,
    NonCashCreditEffectV1,
    NonCashCreditGrantV1,
    NonCashCreditPolicyParameterV1,
)
from dotmac_kernel.cache import PlatformScope
from dotmac_kernel.money import Currency, Money


def _money(value):
    return Money.of(value, Currency("NGN", 2))


def _classification():
    return NonCashCreditClassificationV1(
        "compensation",
        "policy",
        "commercial:1",
        "opaque-v2",
        "a" * 64,
        (NonCashCreditPolicyParameterV1("reason", "service interruption"),),
    )


def test_ordinary_credit_is_non_tax_and_retains_accepted_configuration():
    value = NonCashCreditGrantV1(
        PlatformScope(),
        uuid4(),
        uuid4(),
        _money("50"),
        _classification(),
        "care",
        "case:1",
        "opaque-v1",
        "finance",
        "decision:1",
        datetime.now(UTC),
    )
    assert value.subtotal == value.total == value.original_amount
    assert value.tax_total.amount == 0
    assert value.classification.config_version == "opaque-v2"
    with pytest.raises(FrozenInstanceError):
        value.classification.category = "changed"
    with pytest.raises(ValueError):
        replace(
            value.classification,
            accepted_snapshot=list(value.classification.accepted_snapshot),
        )


@pytest.mark.parametrize(
    "kind,delta,invoice,original",
    [
        ("grant", "50", False, False),
        ("application", "-30", True, False),
        ("application_reversal", "10", True, True),
        ("unused_reduction", "-20", False, False),
    ],
)
def test_credit_effects_have_distinct_signed_money_and_link_shapes(
    kind, delta, invoice, original
):
    value = NonCashCreditEffectV1(
        PlatformScope(),
        uuid4(),
        uuid4(),
        uuid4(),
        kind,
        _money(delta),
        "care",
        "case:1",
        "v1",
        "finance",
        "decision:1",
        datetime.now(UTC),
        uuid4() if invoice else None,
        uuid4() if original else None,
    )
    assert value.availability_delta == _money(delta)
    with pytest.raises(ValueError):
        replace(value, availability_delta=_money(str(-value.availability_delta.amount)))
