from decimal import Decimal, localcontext

import pytest
from dotmac_billing.contracts import WithholdingCashRequirementV1
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.money import Currency, Money


def test_tax_owned_withholding_quote_does_not_create_cash_or_credit():
    ngn = Currency("NGN", 2)
    total, withheld = Money(Decimal("107500"), ngn), Money(Decimal("5000"), ngn)
    with localcontext() as context:
        context.prec = 2
        quote = WithholdingCashRequirementV1(total, withheld)
    assert quote.cash_required == Money(Decimal("102500"), ngn)
    assert quote.invoice_total == total
    assert quote.withholding_amount == withheld


@pytest.mark.parametrize("amount", ["0", "107500", "107501", "-1"])
def test_withholding_quote_rejects_nonpositive_or_no_cash_left(amount):
    ngn = Currency("NGN", 2)
    with pytest.raises(BillingRuleViolation):
        WithholdingCashRequirementV1(
            Money(Decimal("107500"), ngn), Money(Decimal(amount), ngn)
        )
