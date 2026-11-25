"""Two deduction-at-source codes (WHT and contract stamp duty) are tax data.

Tests only. Stamp duty that a government agency deducts from a contract
payment is a second dotmac-tax code on the ``withholding`` side with its own
rate, party classification and ``calculation_sequence``. It is not a treatment
code (those are the closed VAT list). A catch-all zero rule (empty party
category, ``out_of_scope``) keeps non-agency customers from failing with
"no applicable tax rule".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from dotmac_kernel.money import Money
from dotmac_tax import (
    TaxFact,
    TaxRuleInput,
    TaxRuleViolation,
    TaxSubjectClassificationInput,
    create_tax_code,
    determine_tax_set,
    publish_tax_rule,
    publish_tax_subject_classification,
)

from tests.unit import test_tax_service as helpers

NGN = helpers.NGN
NOW = helpers.NOW
db = helpers.db
DAY = date(2026, 6, 1)

WHT_SEQUENCE = 100
STAMP_SEQUENCE = 200


def _rule(code, version, priority, rate, *, sequence, **kw):
    return TaxRuleInput(
        tax_code_id=code.id,
        version=version,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=priority,
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side="withholding",
        calculation_method="percentage",
        rate=Decimal(rate),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal(0),
        treatment_code=kw.pop("treatment_code", "standard_rated"),
        calculation_sequence=sequence,
        **kw,
    )


def _classify(db, tenant_id, code, ref, category):
    return publish_tax_subject_classification(
        db,
        tenant_id=tenant_id,
        command=TaxSubjectClassificationInput(
            tax_code_id=code.id,
            subject_kind="party",
            subject_ref=ref,
            category_code=category,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            basis_code="owner-evidence",
            evidence_ref=f"ev:{code.code}:{ref}",
            published_by_ref="principal:tax-owner",
            source_ref=f"src:{code.code}:{ref}",
            source_version="1",
        ),
    )


def _fact(jurisdiction, ref):
    return TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=DAY,
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side="withholding",
        base_amount=Money.of("1000", NGN),
        source_ref="invoice:1",
        source_version="1",
        evidence_ref="invoice:1",
        counterparty_ref=ref,
    )


def _world(db, *, catch_all=True, stamp_sequence=STAMP_SEQUENCE):
    tenant = helpers._tenant(db)
    _, j, _ = helpers._masters(db, tenant.id)
    t = tenant.id
    wht = create_tax_code(
        db,
        tenant_id=t,
        jurisdiction_id=j.id,
        code="WHT",
        name="Withholding",
        tax_kind_code="withholding",
    )
    stamp = create_tax_code(
        db,
        tenant_id=t,
        jurisdiction_id=j.id,
        code="STAMP-DUTY",
        name="Contract stamp duty deducted by agencies",
        tax_kind_code="stamp-duty",
    )
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(
            wht, 1, 100, "0.05", sequence=WHT_SEQUENCE, party_category="wht-elected"
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(
            stamp,
            1,
            100,
            "0.005",
            sequence=stamp_sequence,
            party_category="govt-agency",
        ),
    )
    if catch_all:
        publish_tax_rule(
            db,
            tenant_id=t,
            command=_rule(
                stamp,
                2,
                10,
                "0",
                sequence=stamp_sequence,
                treatment_code="out_of_scope",
            ),
        )
    # Both customers elect WHT; only the agency is a stamp-duty deductor.
    _classify(db, t, wht, "cust:agency", "wht-elected")
    _classify(db, t, stamp, "cust:agency", "govt-agency")
    _classify(db, t, wht, "cust:private", "wht-elected")
    return t, j, wht, stamp


def test_agency_customer_gets_two_components_in_sequence_order(db):
    t, j, wht, stamp = _world(db)
    result = determine_tax_set(
        db, tenant_id=t, fact=_fact(j, "cust:agency"), determined_at=NOW
    )
    assert result.transaction_side == "withholding"
    assert [c.tax_code_id for c in result.components] == [wht.id, stamp.id]
    first, second = result.components
    assert (first.tax_amount, second.tax_amount) == (
        Money.of("50", NGN),
        Money.of("5", NGN),
    )
    assert first.party_category == "wht-elected"
    assert second.party_category == "govt-agency"
    assert second.party_classification_id is not None
    assert {c.treatment_code for c in result.components} == {"standard_rated"}
    assert first.component_sequence != second.component_sequence


def test_non_agency_customer_gets_zero_stamp_duty_without_refusal(db):
    t, j, wht, stamp = _world(db)
    result = determine_tax_set(
        db, tenant_id=t, fact=_fact(j, "cust:private"), determined_at=NOW
    )
    by_code = {c.tax_code_id: c for c in result.components}
    assert set(by_code) == {wht.id, stamp.id}
    assert by_code[wht.id].tax_amount == Money.of("50", NGN)
    zero = by_code[stamp.id]
    assert zero.treatment_code == "out_of_scope"
    assert zero.tax_amount.amount == 0
    assert zero.party_category is None
    assert zero in result.reportable_zero_components


def test_without_the_catch_all_zero_rule_non_agency_is_refused(db):
    t, j, _, _ = _world(db, catch_all=False)
    with pytest.raises(TaxRuleViolation, match="no applicable tax rule"):
        determine_tax_set(
            db, tenant_id=t, fact=_fact(j, "cust:private"), determined_at=NOW
        )


def test_the_two_codes_must_not_share_a_calculation_sequence(db):
    t, j, _, _ = _world(db, stamp_sequence=WHT_SEQUENCE)
    with pytest.raises(TaxRuleViolation, match="sequence is ambiguous"):
        determine_tax_set(
            db, tenant_id=t, fact=_fact(j, "cust:agency"), determined_at=NOW
        )
