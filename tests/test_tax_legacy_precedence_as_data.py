"""T1: Sub's legacy VAT precedence and WHT expressed purely as tax data.

Tests only. Every legacy branch is a published rule or a subject
classification; no product code path decides tax. Unmatched facts fail closed.

Legacy precedence (highest wins), mapped to dotmac-tax:

* customer VAT exemption   -> party classification + ``exempt`` rule (priority 400)
* per-offer VAT treatment  -> supply classification + rule (priority 300)
* address / account VAT    -> place or party classification + rule (priority 200)
* default                  -> category-free rule at the lowest priority (10)
* inclusive pricing        -> ``inclusive`` flag on the rule
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


def _rule(code, version, priority, rate, **kw):
    treatment = kw.pop("treatment_code", "standard_rated")
    return TaxRuleInput(
        tax_code_id=code.id,
        version=version,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=priority,
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side=kw.pop("transaction_side", "output"),
        calculation_method="percentage",
        rate=Decimal(rate),
        fixed_amount=None,
        inclusive=kw.pop("inclusive", False),
        recoverable_rate=Decimal(0),
        treatment_code=treatment,
        **kw,
    )


def _classify(
    db, tenant_id, code, kind, ref, category, version=1, start=None, end=None
):
    return publish_tax_subject_classification(
        db,
        tenant_id=tenant_id,
        command=TaxSubjectClassificationInput(
            tax_code_id=code.id,
            subject_kind=kind,
            subject_ref=ref,
            category_code=category,
            version=version,
            effective_from=start or date(2026, 1, 1),
            effective_to=end,
            basis_code="owner-evidence",
            evidence_ref=f"ev:{kind}:{ref}:{version}",
            published_by_ref="principal:tax-owner",
            source_ref=f"src:{kind}:{ref}",
            source_version=str(version),
        ),
    )


def _fact(jurisdiction, *, side="output", **kw):
    return TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=DAY,
        fact_kind="invoice",
        recognition_basis_code="issued",
        transaction_side=side,
        base_amount=Money.of("1000", NGN),
        source_ref=kw.pop("source_ref", "invoice:1"),
        source_version="1",
        evidence_ref="invoice:1",
        **kw,
    )


def _vat_world(db):
    tenant = helpers._tenant(db)
    _, jurisdiction, vat = helpers._masters(db, tenant.id)
    t = tenant.id
    publish_tax_rule(db, tenant_id=t, command=_rule(vat, 1, 10, "0.075"))  # default
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(
            vat, 2, 200, "0", place_code="free-zone", treatment_code="zero_rated"
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(
            vat, 3, 300, "0", supply_category="vat-zero", treatment_code="zero_rated"
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(
            vat, 4, 400, "0", party_category="vat-exempt", treatment_code="exempt"
        ),
    )
    return t, jurisdiction, vat


def _only(result):
    assert len(result.components) == 1
    return result.components[0]


def test_default_is_lowest_priority_rule(db):
    t, j, _ = _vat_world(db)
    comp = _only(determine_tax_set(db, tenant_id=t, fact=_fact(j), determined_at=NOW))
    assert comp.rule_version == 1
    assert comp.treatment_code == "standard_rated"
    assert comp.tax_amount == Money.of("75", NGN)


def test_place_classification_beats_default(db):
    t, j, vat = _vat_world(db)
    _classify(db, t, vat, "place", "addr:lekki", "free-zone")
    result = determine_tax_set(
        db, tenant_id=t, fact=_fact(j, place_ref="addr:lekki"), determined_at=NOW
    )
    comp = _only(result)
    assert (comp.rule_version, comp.treatment_code) == (2, "zero_rated")
    assert comp.place_classification_id is not None
    assert comp.tax_amount.amount == 0


def test_offer_supply_classification_beats_place(db):
    t, j, vat = _vat_world(db)
    _classify(db, t, vat, "place", "addr:lekki", "free-zone")
    _classify(db, t, vat, "supply", "offer:basic", "vat-zero")
    result = determine_tax_set(
        db,
        tenant_id=t,
        fact=_fact(j, place_ref="addr:lekki", supply_ref="offer:basic"),
        determined_at=NOW,
    )
    assert _only(result).rule_version == 3


def test_customer_exemption_beats_offer_and_place(db):
    t, j, vat = _vat_world(db)
    _classify(db, t, vat, "place", "addr:lekki", "free-zone")
    _classify(db, t, vat, "supply", "offer:basic", "vat-zero")
    _classify(db, t, vat, "party", "cust:1", "vat-exempt")
    result = determine_tax_set(
        db,
        tenant_id=t,
        fact=_fact(
            j,
            counterparty_ref="cust:1",
            place_ref="addr:lekki",
            supply_ref="offer:basic",
        ),
        determined_at=NOW,
    )
    comp = _only(result)
    assert (comp.rule_version, comp.treatment_code) == (4, "exempt")
    assert comp.party_classification_id is not None
    assert result.reportable_zero_components == (comp,)


def test_expired_exemption_reveals_earlier_classification(db):
    t, j, vat = _vat_world(db)
    _classify(db, t, vat, "party", "cust:2", "vat-exempt", version=1)
    _classify(
        db,
        t,
        vat,
        "party",
        "cust:2",
        "unregistered",
        version=2,
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
    )
    # June: the temporary override has lapsed, exemption is effective again.
    comp = _only(
        determine_tax_set(
            db, tenant_id=t, fact=_fact(j, counterparty_ref="cust:2"), determined_at=NOW
        )
    )
    assert comp.treatment_code == "exempt"


def test_inclusive_pricing_is_a_rule_flag(db):
    tenant = helpers._tenant(db)
    _, j, vat = helpers._masters(db, tenant.id)
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=_rule(vat, 1, 10, "0.075", inclusive=True),
    )
    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=_fact(j),
        determined_at=NOW,
    )
    comp = _only(result)
    assert comp.inclusive is True
    assert comp.tax_amount == Money.of("69.77", NGN)  # 1000 * 0.075 / 1.075
    assert result.net_amount == Money.of("930.23", NGN)
    assert result.gross_amount == Money.of("1000", NGN)


def test_unmatched_facts_fail_closed_without_default(db):
    tenant = helpers._tenant(db)
    _, j, vat = helpers._masters(db, tenant.id)
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=_rule(
            vat, 1, 400, "0", party_category="vat-exempt", treatment_code="exempt"
        ),
    )
    # No classification at all, and an unmatched direct category: both refuse.
    with pytest.raises(TaxRuleViolation):
        determine_tax_set(db, tenant_id=tenant.id, fact=_fact(j), determined_at=NOW)
    with pytest.raises(TaxRuleViolation):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=_fact(j, party_category="other"),
            determined_at=NOW,
        )


def test_no_rule_for_fact_kind_fails_closed(db):
    t, j, _ = _vat_world(db)
    with pytest.raises(TaxRuleViolation, match="no applicable tax rule"):
        determine_tax_set(
            db, tenant_id=t, fact=_fact(j, side="input"), determined_at=NOW
        )


def test_equal_rank_overlap_fails_closed(db):
    t, j, vat = _vat_world(db)
    publish_tax_rule(
        db,
        tenant_id=t,
        command=_rule(vat, 5, 10, "0.05"),  # second default at same rank
    )
    with pytest.raises(TaxRuleViolation, match="ambiguous"):
        determine_tax_set(db, tenant_id=t, fact=_fact(j), determined_at=NOW)


def _wht_world(db):
    tenant = helpers._tenant(db)
    _, j, _ = helpers._masters(db, tenant.id)
    wht = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=j.id,
        code="WHT",
        name="Withholding",
        tax_kind_code="withholding",
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=_rule(
            wht,
            1,
            100,
            "0.05",
            transaction_side="withholding",
            party_category="wht-elected",
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=_rule(
            wht,
            2,
            100,
            "0",
            transaction_side="withholding",
            party_category="wht-not-elected",
            treatment_code="out_of_scope",
        ),
    )
    return tenant.id, j, wht


def test_wht_is_withholding_side_code_plus_party_classification(db):
    t, j, wht = _wht_world(db)
    _classify(db, t, wht, "party", "cust:wht", "wht-elected")
    result = determine_tax_set(
        db,
        tenant_id=t,
        fact=_fact(j, side="withholding", counterparty_ref="cust:wht"),
        determined_at=NOW,
    )
    comp = _only(result)
    assert result.transaction_side == "withholding"
    assert comp.tax_amount == Money.of("50", NGN)
    assert comp.party_category == "wht-elected"
    assert comp.party_classification_id is not None


def test_wht_not_elected_is_explicit_zero_and_unclassified_fails_closed(db):
    t, j, wht = _wht_world(db)
    _classify(db, t, wht, "party", "cust:no", "wht-not-elected")
    result = determine_tax_set(
        db,
        tenant_id=t,
        fact=_fact(j, side="withholding", counterparty_ref="cust:no"),
        determined_at=NOW,
    )
    comp = _only(result)
    assert comp.treatment_code == "out_of_scope" and comp.tax_amount.amount == 0
    assert result.reportable_zero_components == (comp,)
    # No election recorded: applicability is unknown, never assumed.
    with pytest.raises(TaxRuleViolation):
        determine_tax_set(
            db,
            tenant_id=t,
            fact=_fact(
                j,
                side="withholding",
                counterparty_ref="cust:unknown",
                source_ref="invoice:2",
            ),
            determined_at=NOW,
        )


def test_wht_rules_do_not_leak_into_output_side(db):
    t, j, wht = _wht_world(db)
    _classify(db, t, wht, "party", "cust:wht", "wht-elected")
    with pytest.raises(TaxRuleViolation):
        determine_tax_set(
            db,
            tenant_id=t,
            fact=_fact(j, counterparty_ref="cust:wht"),
            determined_at=NOW,
        )
