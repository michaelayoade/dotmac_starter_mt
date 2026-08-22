"""Behavior of configurable tax determination, reports and return lifecycle."""

from __future__ import annotations

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
    accept_tax_return,
    approve_tax_return,
    create_filing_obligation,
    create_statutory_report_definition,
    create_tax_authority,
    create_tax_code,
    create_tax_jurisdiction,
    create_tax_return,
    determine_tax,
    file_tax_return,
    generate_statutory_report,
    prepare_tax_return,
    publish_tax_rule,
)
from dotmac_tax.models import TENANT_MODELS, TaxReturnEvent
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


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
