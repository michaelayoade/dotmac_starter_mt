"""Behavior of configurable tax determination, reports and return lifecycle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from dotmac_tax import (
    StatutoryReportBoxInput,
    TaxAuthorityInput,
    TaxAuthorityV1,
    TaxCodeV1,
    TaxConflict,
    TaxDeterminationComponentV1,
    TaxDeterminationLineV1,
    TaxDeterminationSetV1,
    TaxFact,
    TaxJurisdictionInput,
    TaxJurisdictionV1,
    TaxRuleBandInput,
    TaxRuleInput,
    TaxRuleV1,
    TaxRuleViolation,
    TaxSubjectClassificationInput,
    TaxSubjectClassificationV1,
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
    ensure_tax_authority,
    ensure_tax_code,
    ensure_tax_jurisdiction,
    ensure_tax_rule,
    ensure_tax_subject_classification,
    file_tax_return,
    generate_statutory_report,
    get_tax_authority,
    get_tax_code,
    get_tax_jurisdiction,
    get_tax_rule,
    get_tax_subject_classification,
    prepare_tax_return,
    publish_tax_rule,
    publish_tax_subject_classification,
)
from dotmac_tax import service as tax_service
from dotmac_tax.models import (
    TENANT_MODELS,
    TaxAuthority,
    TaxCode,
    TaxDetermination,
    TaxDeterminationSet,
    TaxJurisdiction,
    TaxReturnEvent,
    TaxRule,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

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


def test_policy_ensure_seam_returns_contracts_and_replays_before_parent_state(
    db: Session,
) -> None:
    tenant = _tenant(db)
    authority_command = TaxAuthorityInput(
        code=" FIRS ",
        name=" Federal authority ",
        authority_level_code=" federal ",
    )
    authority = ensure_tax_authority(db, tenant_id=tenant.id, command=authority_command)
    assert isinstance(authority, TaxAuthorityV1)
    assert get_tax_authority(db, tenant_id=tenant.id, code="FIRS") == authority

    jurisdiction_command = TaxJurisdictionInput(
        authority_id=authority.authority_id,
        code=" NG-FED ",
        name=" Nigeria federal ",
        country_code="ng",
        currency=NGN,
    )
    jurisdiction = ensure_tax_jurisdiction(
        db, tenant_id=tenant.id, command=jurisdiction_command
    )
    assert isinstance(jurisdiction, TaxJurisdictionV1)
    assert get_tax_jurisdiction(db, tenant_id=tenant.id, code="NG-FED") == jurisdiction

    code = ensure_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.jurisdiction_id,
        code="PAYE",
        name="Pay as you earn",
        tax_kind_code="progressive-income-tax",
        description="Approved progressive policy",
    )
    assert isinstance(code, TaxCodeV1)
    assert (
        get_tax_code(
            db,
            tenant_id=tenant.id,
            jurisdiction_id=jurisdiction.jurisdiction_id,
            code="PAYE",
        )
        == code
    )
    rule_command = TaxRuleInput(
        tax_code_id=code.tax_code_id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=10,
        fact_kind="taxable-pay",
        recognition_basis_code="earned",
        transaction_side="liability",
        calculation_method="progressive",
        rate=None,
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal("0"),
        party_category="paye-standard",
        bands=(
            TaxRuleBandInput(1, Decimal("0"), Decimal("300000"), Decimal("0.07")),
            TaxRuleBandInput(2, Decimal("300000"), None, Decimal("0.11")),
        ),
    )
    rule = ensure_tax_rule(db, tenant_id=tenant.id, command=rule_command)
    assert isinstance(rule, TaxRuleV1)
    assert (
        get_tax_rule(
            db,
            tenant_id=tenant.id,
            tax_code_id=code.tax_code_id,
            version=1,
        )
        == rule
    )

    classification_command = TaxSubjectClassificationInput(
        tax_code_id=code.tax_code_id,
        subject_kind="party",
        subject_ref="employee:42",
        category_code="paye-standard",
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        basis_code="approved-payroll-profile",
        evidence_ref="approval:42",
        published_by_ref="principal:reviewer",
        source_ref="erp:employee-tax-profile:42",
        source_version="cv1:profile",
    )
    classification = ensure_tax_subject_classification(
        db,
        tenant_id=tenant.id,
        command=classification_command,
    )
    assert isinstance(classification, TaxSubjectClassificationV1)
    assert (
        get_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            source_ref=classification_command.source_ref,
            source_version=classification_command.source_version,
        )
        == classification
    )

    authority_row = db.get(TaxAuthority, authority.authority_id)
    jurisdiction_row = db.get(TaxJurisdiction, jurisdiction.jurisdiction_id)
    code_row = db.get(TaxCode, code.tax_code_id)
    assert authority_row is not None
    assert jurisdiction_row is not None
    assert code_row is not None
    authority_row.status = "retired"
    assert (
        ensure_tax_jurisdiction(db, tenant_id=tenant.id, command=jurisdiction_command)
        == jurisdiction
    )
    jurisdiction_row.status = "retired"
    assert (
        ensure_tax_code(
            db,
            tenant_id=tenant.id,
            jurisdiction_id=jurisdiction.jurisdiction_id,
            code="PAYE",
            name="Pay as you earn",
            tax_kind_code="progressive-income-tax",
            description="Approved progressive policy",
        )
        == code
    )
    code_row.status = "retired"
    assert ensure_tax_rule(db, tenant_id=tenant.id, command=rule_command) == rule
    assert (
        ensure_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=classification_command,
        )
        == classification
    )


def test_policy_ensure_seam_refuses_drift_under_every_natural_identity(
    db: Session,
) -> None:
    tenant = _tenant(db)
    authority = ensure_tax_authority(
        db,
        tenant_id=tenant.id,
        command=TaxAuthorityInput(code="AUTH", name="Authority"),
    )
    with pytest.raises(TaxConflict, match="different current content"):
        ensure_tax_authority(
            db,
            tenant_id=tenant.id,
            command=TaxAuthorityInput(code="AUTH", name="Different"),
        )
    jurisdiction = ensure_tax_jurisdiction(
        db,
        tenant_id=tenant.id,
        command=TaxJurisdictionInput(
            authority_id=authority.authority_id,
            code="JUR",
            name="Jurisdiction",
            country_code="NG",
            currency=NGN,
        ),
    )
    with pytest.raises(TaxConflict, match="different current content"):
        ensure_tax_jurisdiction(
            db,
            tenant_id=tenant.id,
            command=TaxJurisdictionInput(
                authority_id=authority.authority_id,
                code="JUR",
                name="Changed jurisdiction",
                country_code="NG",
                currency=NGN,
            ),
        )
    code = ensure_tax_code(
        db,
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.jurisdiction_id,
        code="VAT",
        name="VAT",
        tax_kind_code="consumption",
    )
    with pytest.raises(TaxConflict, match="different current content"):
        ensure_tax_code(
            db,
            tenant_id=tenant.id,
            jurisdiction_id=jurisdiction.jurisdiction_id,
            code="VAT",
            name="Changed VAT",
            tax_kind_code="consumption",
        )
    rule_command = TaxRuleInput(
        tax_code_id=code.tax_code_id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=10,
        fact_kind="invoice-line",
        recognition_basis_code="accrual",
        transaction_side="output",
        calculation_method="percentage",
        rate=Decimal("0.075"),
        fixed_amount=None,
        inclusive=False,
        recoverable_rate=Decimal("0"),
    )
    ensure_tax_rule(db, tenant_id=tenant.id, command=rule_command)
    with pytest.raises(TaxConflict, match="different current content"):
        ensure_tax_rule(
            db,
            tenant_id=tenant.id,
            command=replace(rule_command, rate=Decimal("0.08")),
        )
    classification_command = TaxSubjectClassificationInput(
        tax_code_id=code.tax_code_id,
        subject_kind="party",
        subject_ref="customer:1",
        category_code="standard",
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        basis_code="profile",
        evidence_ref="approval:1",
        published_by_ref="principal:1",
        source_ref="erp:profile:1",
        source_version="cv1:1",
    )
    ensure_tax_subject_classification(
        db, tenant_id=tenant.id, command=classification_command
    )
    with pytest.raises(TaxConflict, match="different current content"):
        ensure_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=replace(classification_command, category_code="exempt"),
        )
    with pytest.raises(TaxConflict, match="version exists.*different"):
        ensure_tax_subject_classification(
            db,
            tenant_id=tenant.id,
            command=replace(
                classification_command,
                source_ref="erp:profile:alternate",
                source_version="cv1:alternate",
            ),
        )


def test_policy_rule_read_refuses_to_round_corrupt_persisted_fixed_money(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, _, code = _masters(db, tenant.id)
    row = TaxRule(
        tenant_id=tenant.id,
        tax_code_id=code.id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        priority=1,
        fact_kind="invoice-line",
        recognition_basis_code="accrual",
        transaction_side="output",
        calculation_method="fixed",
        rate=None,
        fixed_amount=Decimal("1.234567"),
        inclusive=False,
        recoverable_rate=Decimal("0"),
        party_category=None,
        supply_category=None,
        place_code=None,
        treatment_code="standard_rated",
        calculation_sequence=100,
        calculation_base_code="source_amount",
        published_at=NOW,
    )
    db.add(row)
    db.flush()

    with pytest.raises(TaxConflict, match="exceeds.*minor units"):
        get_tax_rule(
            db,
            tenant_id=tenant.id,
            tax_code_id=code.id,
            version=1,
        )


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
    assert isinstance(result, TaxDetermination)
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

    conflicting_set = TaxDeterminationSet(
        id=uuid4(),
        tenant_id=tenant.id,
        jurisdiction_id=jurisdiction.id,
        occurred_on=fact.occurred_on,
        fact_kind=fact.fact_kind,
        recognition_basis_code=fact.recognition_basis_code,
        transaction_side=fact.transaction_side,
        source_amount=Decimal("1000.00"),
        net_amount=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        gross_amount=Decimal("1100.00"),
        currency_code="NGN",
        minor_units=2,
        source_ref=fact.source_ref,
        source_version=fact.source_version,
        source_fingerprint="f" * 64,
        result_seal_state="sealed",
        result_fingerprint=f"rv1:{'0' * 64}",
        evidence_ref=fact.evidence_ref,
        determined_at=NOW,
    )
    db.add(conflicting_set)
    db.flush()

    for api in (determine_tax, determine_tax_set):
        with pytest.raises(TaxConflict, match="conflicting determination owners"):
            api(
                db,
                tenant_id=tenant.id,
                fact=fact,
                determined_at=NOW,
            )


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

    read_result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=TaxFact(
            jurisdiction_id=jurisdiction.id,
            occurred_on=date(2026, 7, 31),
            fact_kind="employee-income",
            recognition_basis_code="payroll-finalized",
            transaction_side="withholding",
            base_amount=Money.of("1500", NGN),
            source_ref="payroll:employee-2:2026-07",
            source_version="1",
            evidence_ref="payroll-calculation:2",
        ),
        determined_at=NOW,
    )
    assert [line.sequence for line in read_result.components[0].lines] == [1, 2]
    assert [line.tax_amount for line in read_result.components[0].lines] == [
        Money.of("0.00", NGN),
        Money.of("100.00", NGN),
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

    fact = TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=date(2026, 7, 10),
        fact_kind="cash-receipt",
        recognition_basis_code="cash-received",
        transaction_side="output",
        base_amount=Money.of("1000", NGN),
        source_ref="receipt:multi-tax",
        source_version="1",
        evidence_ref="settlement:multi-tax",
    )
    with pytest.raises(TaxRuleViolation, match="determined_at must be timezone-aware"):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=fact,
            determined_at=NOW.replace(tzinfo=None),
        )
    with pytest.raises(TaxRuleViolation, match="determined_at must be timezone-aware"):
        determine_tax(
            db,
            tenant_id=tenant.id,
            fact=fact,
            determined_at=NOW.replace(tzinfo=None),
        )
    result = determine_tax_set(
        db,
        tenant_id=tenant.id,
        fact=fact,
        determined_at=NOW,
    )

    assert isinstance(result, TaxDeterminationSetV1)
    assert result.tenant_id == tenant.id
    assert result.determination_set_id == result.id
    assert len(result.source_fingerprint) == 64
    assert result.result_fingerprint.startswith("rv1:")
    assert len(result.result_fingerprint) == 68
    assert [component.tax_code_id for component in result.components] == [
        vat_code.id,
        levy_code.id,
    ]
    assert all(
        isinstance(component, TaxDeterminationComponentV1)
        for component in result.components
    )
    assert all(
        component.determination_set_id == result.determination_set_id
        for component in result.components
    )
    assert len({component.determination_id for component in result.components}) == 2
    assert [component.tax_amount for component in result.components] == [
        Money.of("75.00", NGN),
        Money.of("25.00", NGN),
    ]
    assert result.net_amount == Money.of("1000.00", NGN)
    assert result.tax_amount == Money.of("100.00", NGN)
    assert result.gross_amount == Money.of("1100.00", NGN)
    assert isinstance(result.components[0].lines[0], TaxDeterminationLineV1)
    assert result.components[0].lines[0].taxable_amount == Money.of("1000.00", NGN)

    db.expire_all()
    with pytest.raises(
        TaxConflict, match="determination set time must be timezone-aware"
    ):
        determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=fact,
            determined_at=NOW + timedelta(days=1),
        )

    persisted = db.get(TaxDeterminationSet, result.id)
    assert persisted is not None
    # SQLite discarded the offset on reload. Restore the original typed instant
    # in memory only, after proving the public projector refuses it, so this
    # canary can still prove numeric/relationship round-trip fingerprint stability.
    persisted.determined_at = result.determined_at
    for persisted_component in persisted.components:
        persisted_component.determined_at = result.determined_at
    with db.no_autoflush:
        replay = determine_tax_set(
            db,
            tenant_id=tenant.id,
            fact=fact,
            determined_at=NOW + timedelta(days=1),
        )
    assert isinstance(replay, TaxDeterminationSetV1)
    assert replay == result

    # Sensitivity proof: malformed persisted evidence is refused at the public
    # projector. The defects stay in memory so the fixture database is not
    # rewritten merely to prove the read boundary fails closed.
    original_components = list(persisted.components)
    first = original_components[0]
    first_line = first.lines[0]

    def assert_replay_refused(pattern: str) -> None:
        with db.no_autoflush, pytest.raises(TaxConflict, match=pattern):
            determine_tax_set(
                db,
                tenant_id=tenant.id,
                fact=fact,
                determined_at=NOW,
            )

    persisted.components = []
    assert_replay_refused("at least one component")
    persisted.components = original_components

    persisted.components = list(reversed(original_components))
    assert_replay_refused("strict unique ordering")
    persisted.components = original_components

    original_seal_state = persisted.result_seal_state
    original_result_fingerprint = persisted.result_fingerprint
    assert original_seal_state == "sealed"
    assert original_result_fingerprint is not None
    persisted.result_fingerprint = None
    assert_replay_refused("sealed determination has no result fingerprint")
    persisted.result_seal_state = None
    assert_replay_refused("predates the rv1 result-content seal")
    persisted.result_seal_state = original_seal_state
    persisted.result_fingerprint = original_result_fingerprint

    original_tax_code_id = first.tax_code_id
    first.tax_code_id = uuid4()
    assert_replay_refused("result fingerprint does not match")
    first.tax_code_id = original_tax_code_id

    duplicate_field_mutations = {
        "tenant_id": uuid4(),
        "jurisdiction_id": uuid4(),
        "occurred_on": fact.occurred_on + timedelta(days=1),
        "fact_kind": "other-fact",
        "recognition_basis_code": "other-basis",
        "transaction_side": "input",
        "currency_code": "USD",
        "minor_units": 3,
        "source_ref": "receipt:other",
        "source_version": "other-version",
        "source_fingerprint": "f" * 64,
        "evidence_ref": "settlement:other",
        "counterparty_ref": "counterparty:other",
        "determined_at": NOW + timedelta(hours=1),
    }
    assert set(duplicate_field_mutations) == set(tax_service._COMPONENT_SET_FIELDS)
    for field, changed in duplicate_field_mutations.items():
        original = getattr(first, field)
        setattr(first, field, changed)
        assert_replay_refused(f"component {field.replace('_', ' ')} differs")
        setattr(first, field, original)

    original_line_tenant_id = first_line.tenant_id
    first_line.tenant_id = uuid4()
    assert_replay_refused("line tenant differs")
    first_line.tenant_id = original_line_tenant_id

    original_line_determination_id = first_line.determination_id
    first_line.determination_id = uuid4()
    assert_replay_refused("line belongs to another component")
    first_line.determination_id = original_line_determination_id

    original_set_determined_at = persisted.determined_at
    persisted.determined_at = original_set_determined_at.replace(tzinfo=None)
    assert_replay_refused("determination set time must be timezone-aware")
    persisted.determined_at = original_set_determined_at

    original_component_determined_at = first.determined_at
    first.determined_at = original_component_determined_at.replace(tzinfo=None)
    assert_replay_refused("determination component time must be timezone-aware")
    first.determined_at = original_component_determined_at

    original_tax = persisted.tax_amount
    original_gross = persisted.gross_amount
    persisted.tax_amount = original_tax + Decimal("0.01")
    persisted.gross_amount = original_gross + Decimal("0.01")
    assert_replay_refused("total the set tax")
    persisted.tax_amount = original_tax
    persisted.gross_amount = original_gross

    original_recoverable = first.recoverable_amount
    first.recoverable_amount = original_recoverable + Decimal("0.01")
    assert_replay_refused("recovery split")
    first.recoverable_amount = original_recoverable

    original_treatment = first.treatment_code
    first.treatment_code = "exempt"
    assert_replay_refused("must have zero tax")
    first.treatment_code = original_treatment

    first.inclusive = True
    assert_replay_refused("cannot be combined")
    first.inclusive = False

    original_set_id = first.determination_set_id
    first.determination_set_id = uuid4()
    assert_replay_refused("belongs to another set")
    first.determination_set_id = original_set_id

    line = result.components[0].lines[0]
    usd = Currency("USD", 2)
    usd_line = replace(
        line,
        taxable_amount=Money.of(line.taxable_amount.amount, usd),
        tax_amount=Money.of(line.tax_amount.amount, usd),
    )
    with pytest.raises(ValueError, match="component currency"):
        replace(result.components[0], lines=(usd_line,))
    with pytest.raises(ValueError, match="exact finite Decimal"):
        replace(line, rate=Decimal("NaN"))
    foreign_component = replace(result.components[0], determination_set_id=uuid4())
    with pytest.raises(ValueError, match="another determination set"):
        replace(
            result,
            components=(foreign_component, result.components[1]),
        )
    with pytest.raises(ValueError, match="fingerprint is required"):
        replace(result, source_fingerprint=" ")
    with pytest.raises(ValueError, match="rv1 SHA-256"):
        replace(result, result_fingerprint="rv1:not-a-digest")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(result, determined_at=NOW.replace(tzinfo=None))


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
        Money.of("1000.00", NGN),
        Money.of("1100.00", NGN),
    ]
    assert [component.tax_amount for component in result.components] == [
        Money.of("100.00", NGN),
        Money.of("22.00", NGN),
    ]
    assert result.gross_amount == Money.of("1122.00", NGN)


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
    assert result.components[0].tax_amount == Money.of("0.00", NGN)
    assert result.components[1].tax_amount == Money.of("20.00", NGN)


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
        Money.of("0.00", NGN),
        Money.of("0.00", NGN),
        Money.of("0.00", NGN),
    ]
    assert result.reportable_zero_components == result.components
    assert all(
        component.is_reportable_zero and not component.has_tax_consequence
        for component in result.components
    )


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
            calculation_method="fixed",
            rate=None,
            fixed_amount=Money.of("25", NGN),
            inclusive=False,
            recoverable_rate=Decimal("0"),
        ),
    )
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
            source_ref="receipt:report",
            source_version="1",
            evidence_ref="receipt:report",
        ),
        determined_at=NOW,
    )
    # PRECONDITION, restored rather than inherited.
    #
    # The report path re-reads persisted determination sets. This suite
    # deliberately relies on SQLite dropping tzinfo so that
    # `_require_aware_persisted` can be exercised (that refusal is asserted
    # further down, on a row inserted for exactly that purpose). So a
    # determination whose in-memory state is lost comes back NAIVE, and
    # `generate_statutory_report` then raises for a reason this test is not
    # about.
    #
    # An earlier fix held strong references, reasoning about CPython's GC. That
    # guards the wrong mechanism. Two probes settle it: forcing `gc.collect()`
    # here leaves this test GREEN — the references do their job — while forcing
    # `db.expire_all()` reproduces the failure exactly. Expiry, not collection,
    # is therefore the mechanism. The event that causes expiry in the
    # order-sensitive suite path is still unidentified; adding two unrelated
    # test FILES elsewhere in `tests/unit` is enough to expose it.
    #
    # References are still held, because they are cheap and correct as far as
    # they go. The invariant is then RESTORED explicitly, so the test states
    # what it needs instead of hoping the session was left undisturbed.
    _live_rows = [
        (row, list(row.components))
        for row in db.scalars(select(TaxDeterminationSet)).all()
    ]
    assert _live_rows, "no determination sets to report on — setup did not run"

    def _restore_written_offsets() -> None:
        """Put back what SQLite cannot store.

        `_validate_persisted_result_structure` checks the SET's timestamp AND
        every COMPONENT's, so both have to be restored — fixing only the set
        moves the failure one line down rather than resolving it. Values are
        the exact ones written. `set_committed_value` repairs identity-map
        state without making the persistent attributes dirty, so a later
        autoflush cannot turn this dialect workaround into a database update.
        """
        for row, components in _live_rows:
            if row.determined_at.tzinfo is None:
                set_committed_value(row, "determined_at", NOW)
            for component in components:
                if component.determined_at.tzinfo is None:
                    set_committed_value(component, "determined_at", NOW)

    _restore_written_offsets()
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
    # Re-assert immediately before the persisted report read. Expiry is the
    # proven mechanism, but its triggering event in the order-sensitive suite
    # path is still unidentified; neither intervening service above expires
    # the session itself. This makes the required precondition explicit at the
    # point where it is consumed without attributing the expiry to an unproved
    # source.
    _restore_written_offsets()
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

    legacy_fact = TaxFact(
        jurisdiction_id=jurisdiction.id,
        occurred_on=date(2026, 7, 11),
        fact_kind="cash-receipt",
        recognition_basis_code="cash-received",
        transaction_side="output",
        base_amount=Money.of("1000", NGN),
        source_ref="receipt:legacy-report-refusal",
        source_version="1",
        evidence_ref="receipt:legacy-report-refusal",
    )
    db.add(
        TaxDetermination(
            tenant_id=tenant.id,
            jurisdiction_id=jurisdiction.id,
            tax_code_id=code.id,
            rule_id=rule.id,
            rule_version=rule.version,
            occurred_on=legacy_fact.occurred_on,
            fact_kind=legacy_fact.fact_kind,
            recognition_basis_code=legacy_fact.recognition_basis_code,
            transaction_side=legacy_fact.transaction_side,
            base_amount=Decimal("1000.00"),
            tax_amount=Decimal("25.00"),
            recoverable_amount=Decimal("0.00"),
            non_recoverable_amount=Decimal("25.00"),
            currency_code="NGN",
            minor_units=2,
            source_ref=legacy_fact.source_ref,
            source_version=legacy_fact.source_version,
            source_fingerprint=_a1_fingerprint(legacy_fact),
            evidence_ref=legacy_fact.evidence_ref,
            determined_at=NOW,
        )
    )
    db.flush()
    with pytest.raises(TaxConflict, match="legacy unsealed determinations cannot feed"):
        generate_statutory_report(
            db,
            tenant_id=tenant.id,
            obligation_id=obligation.id,
            generated_by_id=uuid4(),
            generated_at=NOW,
        )


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
