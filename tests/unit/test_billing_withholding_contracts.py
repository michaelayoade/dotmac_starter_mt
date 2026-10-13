"""WHT evidence never computes rates or substitutes cash for receivables."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing.contracts import WithholdingTaxEvidenceV1, WithholdingTaxLineV1
from dotmac_kernel.cache import PlatformScope
from dotmac_kernel.money import Currency, Money


def money(value):
    return Money.of(Decimal(value), Currency("NGN", 2))


def evidence():
    return WithholdingTaxEvidenceV1(
        "dotmac-tax",
        "determination:1",
        "source-v1",
        "a" * 64,
        "rv1:" + "b" * 64,
        "tax:evidence:1",
        "invoice:1",
        "component:1",
        "rule:1",
        "1",
        "WHT",
        "subtotal",
        False,
        money("100000"),
        money("5000"),
        (WithholdingTaxLineV1(1, money("100000"), Decimal("0.05"), money("5000")),),
        PlatformScope(),
        uuid4(),
        uuid4(),
        "invoice_issued",
        "invoice",
        "sale",
        datetime.now(UTC),
    )


def test_withholding_evidence_retains_exact_base_component_and_tax_lines():
    value = evidence()
    assert value.amount.amount == Decimal("5000")
    assert value.lines[0].rate == Decimal("0.05")
    assert (
        replace(value, lines=(replace(value.lines[0], rate=None),)).lines[0].rate
        is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_version", ""),
        ("source_fingerprint", ""),
        ("calculation_base_code", ""),
        ("amount", money("-1")),
    ],
)
def test_withholding_evidence_refuses_incomplete_or_invalid_money(field, value):
    with pytest.raises((ValueError, TypeError)):
        replace(evidence(), **{field: value})


def test_withholding_component_must_equal_exact_line_total():
    with pytest.raises(ValueError):
        replace(evidence(), amount=money("4999.99"))


def test_actual_tax_public_result_maps_losslessly_without_rate_reconstruction():
    from datetime import date

    from dotmac_tax.contracts import (
        TaxDeterminationComponentV1,
        TaxDeterminationLineV1,
        TaxDeterminationSetV1,
    )

    set_id, tenant_id = uuid4(), uuid4()
    component = TaxDeterminationComponentV1(
        determination_id=uuid4(),
        determination_set_id=set_id,
        component_sequence=1,
        tax_code_id=uuid4(),
        rule_id=uuid4(),
        rule_version=3,
        treatment_code="standard_rated",
        calculation_base_code="source_amount",
        inclusive=False,
        party_category="business",
        supply_category="service",
        place_code="NG",
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=money("100000"),
        tax_amount=money("5000"),
        recoverable_amount=money("5000"),
        non_recoverable_amount=money("0"),
        lines=(TaxDeterminationLineV1(2, money("100000"), None, money("5000")),),
    )
    result = TaxDeterminationSetV1(
        tenant_id=tenant_id,
        determination_set_id=set_id,
        jurisdiction_id=uuid4(),
        occurred_on=date(2026, 10, 13),
        fact_kind="invoice",
        recognition_basis_code="invoice_issued",
        transaction_side="sale",
        source_amount=money("100000"),
        net_amount=money("100000"),
        tax_amount=money("5000"),
        gross_amount=money("105000"),
        source_ref="invoice-source:1",
        source_version="opaque:version:4",
        source_fingerprint="source:opaque:fingerprint",
        result_fingerprint="rv1:" + "a" * 64,
        evidence_ref="tax:evidence:1",
        counterparty_ref="customer:1",
        supply_ref="service:1",
        place_ref="NG",
        determined_at=datetime.now(UTC),
        components=(component,),
    )
    from dotmac_kernel.cache import TenantScope

    mapped = WithholdingTaxEvidenceV1(
        source_owner="dotmac-tax",
        determination_ref=str(result.determination_set_id),
        source_version=result.source_version,
        source_fingerprint=result.source_fingerprint,
        result_fingerprint=result.result_fingerprint,
        evidence_ref=result.evidence_ref,
        source_ref=result.source_ref,
        component_ref=str(component.determination_id),
        rule_ref=str(component.rule_id),
        rule_version=str(component.rule_version),
        treatment_code=component.treatment_code,
        calculation_base_code=component.calculation_base_code,
        inclusive=component.inclusive,
        taxable_basis=component.base_amount,
        amount=component.tax_amount,
        lines=tuple(
            WithholdingTaxLineV1(
                line.sequence, line.taxable_amount, line.rate, line.tax_amount
            )
            for line in component.lines
        ),
        scope=TenantScope(result.tenant_id),
        billing_account_id=uuid4(),
        document_id=uuid4(),
        recognition_basis_code=result.recognition_basis_code,
        fact_kind=result.fact_kind,
        transaction_side=result.transaction_side,
        determined_at=result.determined_at,
    )
    assert mapped.source_version == "opaque:version:4"
    assert mapped.result_fingerprint == result.result_fingerprint
    assert mapped.lines[0].sequence == 2
    assert mapped.lines[0].rate is None
    assert mapped.amount == component.tax_amount


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "USD"),
        ("minor_units", 3),
        ("claimed_amount", Decimal("4999")),
        ("claim_ref", "different-claim"),
        ("settlement_id", uuid4()),
        ("allocation_id", uuid4()),
        ("reverses_discharge_id", uuid4()),
        ("document_id", uuid4()),
        ("billing_account_id", uuid4()),
        ("tax_source_owner", "different-owner"),
        ("determination_ref", "different-set"),
        ("component_ref", "different-component"),
    ],
)
def test_withholding_query_refuses_corrupt_persisted_envelope(field, value):
    from contextlib import nullcontext

    from dotmac_billing.contracts import WithholdingDischargeEffectV1
    from dotmac_billing.errors import BillingRuleViolation
    from dotmac_billing.queries import get_withholding_discharge
    from dotmac_billing.service import _jsonable

    tax = evidence()
    effect = WithholdingDischargeEffectV1(
        uuid4(),
        tax.document_id,
        "claim:1",
        money("5000"),
        money("5000"),
        tax,
        "finance:1",
        "audit:1",
    )
    row = {
        "id": effect.discharge_id,
        "document_id": effect.document_id,
        "billing_account_id": tax.billing_account_id,
        "amount_delta": Decimal("5000"),
        "currency": "NGN",
        "minor_units": 2,
        "claimed_amount": Decimal("5000"),
        "claim_ref": "claim:1",
        "settlement_id": None,
        "allocation_id": None,
        "reverses_discharge_id": None,
        "tax_source_owner": tax.source_owner,
        "determination_ref": tax.determination_ref,
        "component_ref": tax.component_ref,
        "evidence": _jsonable(effect),
    }

    class Result:
        def mappings(self):
            return self

        def one_or_none(self):
            return row

    class StoredSession:
        no_autoflush = nullcontext()

        def execute(self, statement):
            return Result()

    session = StoredSession()
    assert (
        get_withholding_discharge(
            session, scope=tax.scope, discharge_id=effect.discharge_id
        )
        == effect
    )
    row[field] = value
    with pytest.raises(BillingRuleViolation, match="stored withholding"):
        get_withholding_discharge(
            session, scope=tax.scope, discharge_id=effect.discharge_id
        )
