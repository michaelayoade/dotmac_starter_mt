"""Behavior of configurable tax determination, reports and return lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from dotmac_tax import (
    StatutoryReportBoxInput,
    TaxAuthorityInput,
    TaxConflict,
    TaxFact,
    TaxJurisdictionInput,
    TaxRuleBandInput,
    TaxRuleInput,
    TaxRuleViolation,
    TaxSubjectClassificationInput,
    accept_tax_return,
    approve_tax_return,
    create_filing_obligation,
    create_statutory_report_definition,
    create_tax_authority,
    create_tax_code,
    create_tax_jurisdiction,
    create_tax_return,
    determine_tax,
    determine_tax_set,
    file_tax_return,
    generate_statutory_report,
    prepare_tax_return,
    publish_tax_rule,
    publish_tax_subject_classification,
)
from dotmac_tax import service as tax_service
from dotmac_tax.models import (
    TENANT_MODELS,
    TaxDetermination,
    TaxDeterminationSet,
    TaxReturnEvent,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def _a1_fingerprint(fact: TaxFact) -> str:
    payload = {
        "jurisdiction_id": str(fact.jurisdiction_id),
        "occurred_on": fact.occurred_on.isoformat(),
        "fact_kind": fact.fact_kind,
        "recognition_basis_code": fact.recognition_basis_code,
        "transaction_side": fact.transaction_side,
        "amount": str(fact.base_amount.amount),
        "currency_code": fact.base_amount.currency.code,
        "minor_units": fact.base_amount.currency.minor_units,
        "source_ref": fact.source_ref,
        "source_version": fact.source_version,
        "evidence_ref": fact.evidence_ref,
        "party_category": fact.party_category,
        "supply_category": fact.supply_category,
        "place_code": fact.place_code,
        "counterparty_ref": fact.counterparty_ref,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_tax": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, *(model.__table__ for model in TENANT_MODELS)],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> Tenant:
    tenant = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(tenant)
    db.flush()
    return tenant


def _masters(db: Session, tenant_id):
    authority = create_tax_authority(
        db,
        tenant_id=tenant_id,
        command=TaxAuthorityInput(code="AUTH", name="Configured authority"),
    )
    jurisdiction = create_tax_jurisdiction(
        db,
        tenant_id=tenant_id,
        command=TaxJurisdictionInput(
            authority_id=authority.id,
            code="JUR",
            name="Configured jurisdiction",
            country_code="NG",
            currency=NGN,
        ),
    )
    code = create_tax_code(
        db,
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code="OUTPUT-CASH",
        name="Configured output levy",
        tax_kind_code="tenant-defined-kind",
    )
    return authority, jurisdiction, code


def test_tax_rule_selection_and_rate_are_effective_dated_data(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    rule = publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0.10"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
        ),
    )
    result = determine_tax(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:100",
            source_version="1",
            evidence_ref="settlement:100",
        ),
        determined_at=NOW,
    )

    assert result.rule_id == rule.id
    assert result.base_amount == Decimal("1000.00")
    assert result.tax_amount == Decimal("100.00")

    replay = determine_tax(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:100",
            source_version="1",
            evidence_ref="settlement:100",
        ),
        determined_at=NOW,
    )
    assert replay.id == result.id

    with pytest.raises(TaxConflict, match="reused with different facts"):
        determine_tax(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                base_amount=Money.of("1001", NGN),
                source_ref="receipt:100",
                source_version="1",
                evidence_ref="settlement:100",
            ),
            determined_at=NOW,
        )

    with pytest.raises(TaxRuleViolation, match="no applicable tax rule"):
        determine_tax(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="invoice-issued",
                recognition_basis_code="accrual",
                transaction_side="output",
                base_amount=Money.of("1000", NGN),
                source_ref="invoice:100",
                source_version="1",
                evidence_ref="invoice:100",
            ),
            determined_at=NOW,
        )


def test_single_tax_api_replays_published_a1_fingerprint(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    rule = publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0.10"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
        ),
    )
    fact = TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=date(2026, 7, 10),
        fact_kind="cash-receipt",
        recognition_basis_code="cash-received",
        transaction_side="output",
        base_amount=Money.of("1000", NGN),
        source_ref="receipt:a1",
        source_version="1",
        evidence_ref="settlement:a1",
    )
    legacy = TaxDetermination(
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        tax_code_id=code.id,
        rule_id=rule.id,
        rule_version=rule.version,
        occurred_on=fact.occurred_on,
        fact_kind=fact.fact_kind,
        recognition_basis_code=fact.recognition_basis_code,
        transaction_side=fact.transaction_side,
        base_amount=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        recoverable_amount=Decimal("0.00"),
        non_recoverable_amount=Decimal("100.00"),
        currency_code="NGN",
        minor_units=2,
        source_ref=fact.source_ref,
        source_version=fact.source_version,
        source_fingerprint=_a1_fingerprint(fact),
        evidence_ref=fact.evidence_ref,
        determined_at=NOW,
    )
    db.add(legacy)
    db.flush()

    replay = determine_tax(
        db,
        tenant_id=tenant.id,
        fact=fact,
        determined_at=NOW,
    )

    assert replay.id == legacy.id
    assert replay.determination_set_id is None

    with pytest.raises(TaxConflict, match="legacy single-component API"):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=fact,
            determined_at=NOW,
        )
    assert db.scalar(select(func.count(TaxDeterminationSet.id))) == 0


def test_progressive_bands_are_configured_and_snapshotted(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="employee-income",
            recognition_basis_code="payroll-finalized",
            transaction_side="withholding",
            calculation_method="progressive",
            rate=None,
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
            bands=(
                TaxRuleBandInput(
                    sequence=1,
                    lower_bound=Decimal("0"),
                    upper_bound=Decimal("1000"),
                    rate=Decimal("0"),
                ),
                TaxRuleBandInput(
                    sequence=2,
                    lower_bound=Decimal("1000"),
                    upper_bound=None,
                    rate=Decimal("0.20"),
                ),
            ),
        ),
    )
    result = determine_tax(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 31),
            fact_kind="employee-income",
            recognition_basis_code="payroll-finalized",
            transaction_side="withholding",
            base_amount=Money.of("1500", NGN),
            source_ref="payroll:employee-1:2026-07",
            source_version="1",
            evidence_ref="payroll-calculation:1",
        ),
        determined_at=NOW,
    )
    assert result.tax_amount == Decimal("100.00")
    assert [line.tax_amount for line in result.lines] == [
        Decimal("0.00"),
        Decimal("100.00"),
    ]


def test_one_source_fact_produces_multiple_ordered_tax_components(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, jurisdiction, vat_code = _masters(db, tenant.id)
    levy_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="SERVICE-LEVY",
        name="Configured service levy",
        tax_kind_code="tenant-defined-service-levy",
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=vat_code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0.075"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
            treatment_code="standard_rated",
            calculation_sequence=10,
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=levy_code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="fixed",
            rate=None,
            fixed_amount=Money.of("25", NGN),
            inclusive=False,
            recoverable_rate=Decimal("0"),
            treatment_code="standard_rated",
            calculation_sequence=20,
        ),
    )

    with pytest.raises(TaxRuleViolation, match="determine_tax_set API"):
        determine_tax(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                base_amount=Money.of("1000", NGN),
                source_ref="receipt:single-api-refusal",
                source_version="1",
                evidence_ref="settlement:single-api-refusal",
            ),
            determined_at=NOW,
        )
    assert (
        db.scalar(
            select(func.count())
            .select_from(TaxDeterminationSet)
            .where(
                TaxDeterminationSet.tenant_id == tenant.id,
                TaxDeterminationSet.source_ref == "receipt:single-api-refusal",
            )
        )
        == 0
    )

    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:multi-tax",
            source_version="1",
            evidence_ref="settlement:multi-tax",
        ),
        determined_at=NOW,
    )

    assert [component.tax_code_id for component in result.components] == [
        vat_code.id,
        levy_code.id,
    ]
    assert [component.tax_amount for component in result.components] == [
        Decimal("75.00"),
        Decimal("25.00"),
    ]
    assert result.net_amount == Decimal("1000.00")
    assert result.tax_amount == Decimal("100.00")
    assert result.gross_amount == Decimal("1100.00")

    replay = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:multi-tax",
            source_version="1",
            evidence_ref="settlement:multi-tax",
        ),
        determined_at=NOW,
    )
    assert replay.id == result.id


def test_each_configured_tax_code_requires_an_explicit_matching_treatment(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, jurisdiction, vat_code = _masters(db, tenant.id)
    levy_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="SERVICE-LEVY",
        name="Configured service levy",
        tax_kind_code="tenant-defined-service-levy",
    )
    for code, supply_category, sequence in (
        (vat_code, None, 10),
        (levy_code, "telecom-service", 20),
    ):
        publish_tax_rule(
            db,
            tenant_id=tenant.id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=10,
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                calculation_method="percentage",
                rate=Decimal("0.075"),
                fixed_amount=None,
                inclusive=False,
                recoverable_rate=Decimal("0"),
                supply_category=supply_category,
                calculation_sequence=sequence,
            ),
        )

    with pytest.raises(
        TaxRuleViolation,
        match="no applicable tax rule for SERVICE-LEVY",
    ):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                base_amount=Money.of("1000", NGN),
                source_ref="receipt:missing-levy-treatment",
                source_version="1",
                evidence_ref="settlement:missing-levy-treatment",
                supply_category="hardware",
            ),
            determined_at=NOW,
        )
    assert db.scalar(select(func.count(TaxDeterminationSet.id))) == 0


def test_compound_tax_uses_declared_sequence_and_prior_tax(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, first_code = _masters(db, tenant.id)
    compound_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="COMPOUND",
        name="Configured compound charge",
        tax_kind_code="tenant-defined-compound",
    )
    for code, sequence, base_code, rate in (
        (first_code, 10, "source_amount", Decimal("0.10")),
        (compound_code, 20, "source_plus_prior_tax", Decimal("0.02")),
    ):
        publish_tax_rule(
            db,
            tenant_id=tenant.id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=10,
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                calculation_method="percentage",
                rate=rate,
                fixed_amount=None,
                inclusive=False,
                recoverable_rate=Decimal("0"),
                treatment_code="standard_rated",
                calculation_sequence=sequence,
                calculation_base_code=base_code,
            ),
        )

    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:compound",
            source_version="1",
            evidence_ref="settlement:compound",
        ),
        determined_at=NOW,
    )

    assert [component.base_amount for component in result.components] == [
        Decimal("1000.00"),
        Decimal("1100.00"),
    ]
    assert [component.tax_amount for component in result.components] == [
        Decimal("100.00"),
        Decimal("22.00"),
    ]
    assert result.gross_amount == Decimal("1122.00")


def test_tax_specific_classifications_select_exempt_and_standard_components(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, jurisdiction, vat_code = _masters(db, tenant.id)
    levy_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="CONNECTIVITY-LEVY",
        name="Connectivity levy",
        tax_kind_code="tenant-defined-connectivity-levy",
    )
    counterparty_ref = "party:customer-100"
    classification = publish_tax_subject_classification(
        db,
        tenant_id=tenant.id,
        command=TaxSubjectClassificationInput(
            tax_code_id=vat_code.id,
            subject_kind="party",
            subject_ref=counterparty_ref,
            category_code="documented-vat-exemption",
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
            basis_code="exemption-certificate",
            evidence_ref="certificate:vat:100",
            published_by_ref="user:finance-controller",
            source_ref="tax-policy:party-100:vat",
            source_version="1",
        ),
        published_at=NOW,
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=vat_code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=20,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
            party_category="documented-vat-exemption",
            treatment_code="exempt",
            calculation_sequence=10,
        ),
    )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=levy_code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0.02"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
            treatment_code="standard_rated",
            calculation_sequence=20,
        ),
    )

    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:classified",
            source_version="1",
            evidence_ref="settlement:classified",
            counterparty_ref=counterparty_ref,
        ),
        determined_at=NOW,
    )

    assert [component.treatment_code for component in result.components] == [
        "exempt",
        "standard_rated",
    ]
    assert result.components[0].party_classification_id == classification.id
    assert result.components[0].tax_amount == Decimal("0.00")
    assert result.components[1].tax_amount == Decimal("20.00")


def test_party_supply_and_place_classifications_are_snapshotted_per_tax_code(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    references = {
        "party": ("party:100", "registered-business"),
        "supply": ("service-spec:fiber", "telecommunications"),
        "place": ("service-location:abuja", "fct"),
    }
    classifications = {}
    for subject_kind, (subject_ref, category_code) in references.items():
        classifications[subject_kind] = publish_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=TaxSubjectClassificationInput(
                tax_code_id=code.id,
                subject_kind=subject_kind,
                subject_ref=subject_ref,
                category_code=category_code,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                basis_code=f"approved-{subject_kind}-classification",
                evidence_ref=f"evidence:{subject_kind}:100",
                published_by_ref="user:tax-controller",
                source_ref=f"classification:{subject_kind}:100",
                source_version="1",
            ),
            published_at=NOW,
        )
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="percentage",
            rate=Decimal("0.075"),
            fixed_amount=None,
            inclusive=False,
            recoverable_rate=Decimal("0"),
            party_category="registered-business",
            supply_category="telecommunications",
            place_code="fct",
            treatment_code="standard_rated",
        ),
    )

    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:all-classifications",
            source_version="1",
            evidence_ref="settlement:all-classifications",
            counterparty_ref=references["party"][0],
            supply_ref=references["supply"][0],
            place_ref=references["place"][0],
        ),
        determined_at=NOW,
    )

    component = result.components[0]
    assert component.party_classification_id == classifications["party"].id
    assert component.supply_classification_id == classifications["supply"].id
    assert component.place_classification_id == classifications["place"].id
    assert component.party_category == "registered-business"
    assert component.supply_category == "telecommunications"
    assert component.place_code == "fct"


def test_zero_amount_treatments_retain_distinct_legal_identity(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, standard_code = _masters(db, tenant.id)
    codes = [standard_code]
    for code, name in (("EXEMPT", "Exempt"), ("OUT", "Out of scope")):
        codes.append(
            create_tax_code(
                db,
                tenant_id=tenant.id,
                jurisdiction_id=jurisdiction.id,
                code=code,
                name=name,
                tax_kind_code=f"tenant-defined-{code.lower()}",
            )
        )
    for code, treatment, sequence in zip(
        codes,
        ("zero_rated", "exempt", "out_of_scope"),
        (10, 20, 30),
        strict=True,
    ):
        publish_tax_rule(
            db,
            tenant_id=tenant.id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=10,
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                calculation_method="percentage",
                rate=Decimal("0"),
                fixed_amount=None,
                inclusive=False,
                recoverable_rate=Decimal("0"),
                treatment_code=treatment,
                calculation_sequence=sequence,
            ),
        )

    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:zero-identities",
            source_version="1",
            evidence_ref="settlement:zero-identities",
        ),
        determined_at=NOW,
    )

    assert [component.treatment_code for component in result.components] == [
        "zero_rated",
        "exempt",
        "out_of_scope",
    ]
    assert [component.tax_amount for component in result.components] == [
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]


def test_classification_source_replays_and_conflicts_by_fingerprint(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, _, code = _masters(db, tenant.id)
    command = TaxSubjectClassificationInput(
        tax_code_id=code.id,
        subject_kind="party",
        subject_ref="party:100",
        category_code="registered-business",
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        basis_code="tax-registration",
        evidence_ref="registration:100",
        published_by_ref="user:tax-controller",
        source_ref="classification:party:100",
        source_version="1",
    )
    first = publish_tax_subject_classification(
        db, tenant_id=tenant.id, command=command, published_at=NOW
    )
    replay = publish_tax_subject_classification(
        db, tenant_id=tenant.id, command=command, published_at=NOW
    )
    assert replay.id == first.id

    with pytest.raises(TaxConflict, match="reused with different facts"):
        publish_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=TaxSubjectClassificationInput(
                tax_code_id=code.id,
                subject_kind="party",
                subject_ref="party:100",
                category_code="exempt-business",
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                basis_code="tax-registration",
                evidence_ref="registration:100",
                published_by_ref="user:tax-controller",
                source_ref="classification:party:100",
                source_version="1",
            ),
            published_at=NOW,
        )


def test_classification_versions_form_an_effective_dated_override_chain(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, _, code = _masters(db, tenant.id)
    common = {
        "tax_code_id": code.id,
        "subject_kind": "party",
        "subject_ref": "party:100",
        "basis_code": "tax-registration",
        "published_by_ref": "user:tax-controller",
    }
    publish_tax_subject_classification(
        db,
        tenant_id=tenant.id,
        command=TaxSubjectClassificationInput(
            **common,
            category_code="registered-business",
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            evidence_ref="registration:100",
            source_ref="classification:party:100",
            source_version="1",
        ),
        published_at=NOW,
    )
    replacement = publish_tax_subject_classification(
        db,
        tenant_id=tenant.id,
        command=TaxSubjectClassificationInput(
            **common,
            category_code="documented-vat-exemption",
            version=2,
            effective_from=date(2026, 7, 1),
            effective_to=date(2026, 12, 31),
            evidence_ref="exemption:100",
            source_ref="classification:party:100",
            source_version="2",
        ),
        published_at=NOW,
    )

    before, before_row = tax_service._subject_classification(
        db,
        tenant_id=tenant.id,
        tax_code_id=code.id,
        subject_kind="party",
        subject_ref="party:100",
        direct_category=None,
        occurred_on=date(2026, 6, 30),
    )
    during, during_row = tax_service._subject_classification(
        db,
        tenant_id=tenant.id,
        tax_code_id=code.id,
        subject_kind="party",
        subject_ref="party:100",
        direct_category=None,
        occurred_on=date(2026, 7, 1),
    )
    after, after_row = tax_service._subject_classification(
        db,
        tenant_id=tenant.id,
        tax_code_id=code.id,
        subject_kind="party",
        subject_ref="party:100",
        direct_category=None,
        occurred_on=date(2027, 1, 1),
    )

    assert before_row is not None
    assert during_row is not None
    assert after_row is not None
    assert (before, before_row.version) == ("registered-business", 1)
    assert (during, during_row.id) == ("documented-vat-exemption", replacement.id)
    assert (after, after_row.version) == ("registered-business", 1)

    with pytest.raises(TaxConflict, match="next tax classification version"):
        publish_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=TaxSubjectClassificationInput(
                **common,
                category_code="another-category",
                version=4,
                effective_from=date(2027, 1, 1),
                effective_to=None,
                evidence_ref="registration:100:replacement",
                source_ref="classification:party:100",
                source_version="4",
            ),
            published_at=NOW,
        )


def test_duplicate_component_sequence_fails_closed(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, first_code = _masters(db, tenant.id)
    second_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="SECOND",
        name="Second tax",
        tax_kind_code="tenant-defined-second",
    )
    for code in (first_code, second_code):
        publish_tax_rule(
            db,
            tenant_id=tenant.id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=10,
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                calculation_method="percentage",
                rate=Decimal("0.01"),
                fixed_amount=None,
                inclusive=False,
                recoverable_rate=Decimal("0"),
                calculation_sequence=10,
            ),
        )

    with pytest.raises(TaxRuleViolation, match="sequence is ambiguous"):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                base_amount=Money.of("1000", NGN),
                source_ref="receipt:ambiguous-sequence",
                source_version="1",
                evidence_ref="settlement:ambiguous-sequence",
            ),
            determined_at=NOW,
        )


def test_inclusive_tax_cannot_mix_with_another_component(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, vat_code = _masters(db, tenant.id)
    levy_code = create_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="EXCLUSIVE-LEVY",
        name="Exclusive levy",
        tax_kind_code="tenant-defined-exclusive-levy",
    )
    for code, sequence, inclusive in (
        (vat_code, 10, True),
        (levy_code, 20, False),
    ):
        publish_tax_rule(
            db,
            tenant_id=tenant.id,
            command=TaxRuleInput(
                tax_code_id=code.id,
                version=1,
                effective_from=date(2026, 1, 1),
                effective_to=None,
                priority=10,
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                calculation_method="percentage",
                rate=Decimal("0.05"),
                fixed_amount=None,
                inclusive=inclusive,
                recoverable_rate=Decimal("0"),
                calculation_sequence=sequence,
            ),
        )

    with pytest.raises(
        TaxRuleViolation,
        match="inclusive tax cannot be combined",
    ):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=TaxFact(
                jurisdiction_id=jurisdiction.id,
                occurred_on=date(2026, 7, 10),
                fact_kind="cash-receipt",
                recognition_basis_code="cash-received",
                transaction_side="output",
                base_amount=Money.of("1000", NGN),
                source_ref="receipt:mixed-inclusive",
                source_version="1",
                evidence_ref="settlement:mixed-inclusive",
            ),
            determined_at=NOW,
        )


def test_report_definition_boxes_and_due_dates_are_crud_data(db: Session) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="fixed",
            rate=None,
            fixed_amount=Money.of("25", NGN),
            inclusive=False,
            recoverable_rate=Decimal("0"),
        ),
    )
    determine_tax(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 10),
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            base_amount=Money.of("1000", NGN),
            source_ref="receipt:report",
            source_version="1",
            evidence_ref="receipt:report",
        ),
        determined_at=NOW,
    )
    definition = create_statutory_report_definition(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="RETURN-X",
        name="Configured return",
        currency=NGN,
        payable_box_code="BOX-TAX",
        boxes=(
            StatutoryReportBoxInput(
                box_code="BOX-BASE",
                label="Taxable base",
                sequence=1,
                tax_code_id=code.id,
                value_source="base_amount",
                multiplier=Decimal("1"),
            ),
            StatutoryReportBoxInput(
                box_code="BOX-TAX",
                label="Tax payable",
                sequence=2,
                tax_code_id=code.id,
                value_source="tax_amount",
                multiplier=Decimal("1"),
            ),
        ),
    )
    obligation = create_filing_obligation(
        db,
        tenant_id=tenant.id,
        definition_id=definition.id,
        obligation_ref="RETURN-X:2026-07",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_on=date(2026, 8, 18),
        taxpayer_ref="taxpayer:local",
    )
    report = generate_statutory_report(
        db,
        tenant_id=tenant.id,
        obligation_id=obligation.id,
        generated_by_id=uuid4(),
        generated_at=NOW,
    )
    values = {value.box_code: value.amount for value in report.values}
    assert values == {"BOX-BASE": Decimal("1000.00"), "BOX-TAX": Decimal("25.00")}
    assert obligation.due_on == date(2026, 8, 18)
    assert report.total_payable == Decimal("25.00")


def test_return_lifecycle_has_separation_and_an_append_only_timeline(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, jurisdiction, code = _masters(db, tenant.id)
    publish_tax_rule(
        db,
        tenant_id=tenant.id,
        command=TaxRuleInput(
            tax_code_id=code.id,
            version=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            priority=10,
            fact_kind="cash-receipt",
            recognition_basis_code="cash-received",
            transaction_side="output",
            calculation_method="fixed",
            rate=None,
            fixed_amount=Money.of("10", NGN),
            inclusive=False,
            recoverable_rate=Decimal("0"),
        ),
    )
    definition = create_statutory_report_definition(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        code="RETURN-Y",
        name="Return Y",
        currency=NGN,
        payable_box_code="PAYABLE",
        boxes=(
            StatutoryReportBoxInput(
                box_code="PAYABLE",
                label="Payable",
                sequence=1,
                tax_code_id=code.id,
                value_source="tax_amount",
                multiplier=Decimal("1"),
            ),
        ),
    )
    obligation = create_filing_obligation(
        db,
        tenant_id=tenant.id,
        definition_id=definition.id,
        obligation_ref="RETURN-Y:2026-07",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_on=date(2026, 8, 31),
        taxpayer_ref="taxpayer:local",
    )
    report = generate_statutory_report(
        db,
        tenant_id=tenant.id,
        obligation_id=obligation.id,
        generated_by_id=uuid4(),
        generated_at=NOW,
    )
    preparer, approver, filer = uuid4(), uuid4(), uuid4()
    tax_return = create_tax_return(
        db,
        tenant_id=tenant.id,
        report_id=report.id,
        adjustment=Money.of("0", NGN),
        created_by_id=preparer,
        created_at=NOW,
    )
    prepare_tax_return(
        db,
        tenant_id=tenant.id,
        return_id=tax_return.id,
        prepared_by_id=preparer,
        prepared_at=NOW,
    )

    with pytest.raises(TaxRuleViolation, match="preparer cannot approve"):
        approve_tax_return(
            db,
            tenant_id=tenant.id,
            return_id=tax_return.id,
            approved_by_id=preparer,
            approved_at=NOW,
        )

    approve_tax_return(
        db,
        tenant_id=tenant.id,
        return_id=tax_return.id,
        approved_by_id=approver,
        approved_at=NOW,
    )
    file_tax_return(
        db,
        tenant_id=tenant.id,
        return_id=tax_return.id,
        filed_by_id=filer,
        filed_at=NOW,
        filing_reference="authority-receipt:1",
    )
    accept_tax_return(
        db,
        tenant_id=tenant.id,
        return_id=tax_return.id,
        recorded_by_id=uuid4(),
        recorded_at=NOW,
        authority_reference="authority-accepted:1",
    )

    assert tax_return.status == "accepted"
    events = db.scalars(
        select(TaxReturnEvent)
        .where(TaxReturnEvent.return_id == tax_return.id)
        .order_by(TaxReturnEvent.sequence)
    ).all()
    assert [event.to_status for event in events] == [
        "draft",
        "prepared",
        "approved",
        "filed",
        "accepted",
    ]
