"""Behavior of configurable banking masters, observations and reconciliation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_banking import (
    BankAccountInput,
    BankInstitutionInput,
    BankStatementInput,
    CashObservationInput,
    MatchPolicyInput,
    MatchRuleViolation,
    StatementLineDirection,
    StatementLineInput,
    accept_match,
    approve_reconciliation,
    create_bank_account,
    create_bank_institution,
    create_match_policy,
    import_bank_statement,
    prepare_reconciliation,
    record_cash_observation,
    suggest_matches,
    update_bank_account,
    update_bank_institution,
)
from dotmac_banking.models import TENANT_MODELS, MatchAllocation
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_banking": None}},
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


def _account(db: Session, tenant_id):
    institution = create_bank_institution(
        db,
        tenant_id=tenant_id,
        command=BankInstitutionInput(
            code="bank-a",
            name="Configurable Bank",
            country_code="NG",
            clearing_code="001",
        ),
    )
    account = create_bank_account(
        db,
        tenant_id=tenant_id,
        command=BankAccountInput(
            institution_id=institution.id,
            account_code="OPERATING",
            account_name="Operating cash",
            account_identifier="0000000001",
            account_type_code="current",
            currency=NGN,
            cash_account_ref="ledger-account:cash-main",
        ),
    )
    return institution, account


def test_bank_institution_and_account_are_tenant_managed_crud(db: Session) -> None:
    tenant = _tenant(db)
    institution, account = _account(db, tenant.id)

    update_bank_institution(
        db,
        tenant_id=tenant.id,
        institution_id=institution.id,
        name="Renamed Bank",
        clearing_code="002",
    )
    update_bank_account(
        db,
        tenant_id=tenant.id,
        account_id=account.id,
        account_name="Primary operating cash",
        cash_account_ref="ledger-account:cash-primary",
    )

    assert institution.name == "Renamed Bank"
    assert institution.clearing_code == "002"
    assert account.account_name == "Primary operating cash"
    assert account.cash_account_ref == "ledger-account:cash-primary"


def test_statement_import_rejects_a_false_balance(db: Session) -> None:
    tenant = _tenant(db)
    _, account = _account(db, tenant.id)

    with pytest.raises(MatchRuleViolation, match="does not balance"):
        import_bank_statement(
            db,
            tenant_id=tenant.id,
            command=BankStatementInput(
                account_id=account.id,
                statement_ref="statement:bad",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                opening_balance=Money.of("1000", NGN),
                closing_balance=Money.of("1050", NGN),
                source_ref="upload:1",
                source_version="1",
                evidence_ref="file:sha256:bad",
                lines=(
                    StatementLineInput(
                        line_number=1,
                        transaction_date=date(2026, 7, 10),
                        direction=StatementLineDirection.CREDIT,
                        amount=Money.of("100", NGN),
                        external_ref="credit-1",
                        description="Receipt",
                    ),
                ),
            ),
            imported_at=NOW,
            imported_by_id=uuid4(),
        )


def test_matching_policy_is_data_driven_and_supports_multi_match(db: Session) -> None:
    tenant = _tenant(db)
    _, account = _account(db, tenant.id)
    statement = import_bank_statement(
        db,
        tenant_id=tenant.id,
        command=BankStatementInput(
            account_id=account.id,
            statement_ref="statement:jul",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            opening_balance=Money.of("1000", NGN),
            closing_balance=Money.of("1100", NGN),
            source_ref="upload:2",
            source_version="1",
            evidence_ref="file:sha256:good",
            lines=(
                StatementLineInput(
                    line_number=1,
                    transaction_date=date(2026, 7, 10),
                    direction=StatementLineDirection.CREDIT,
                    amount=Money.of("100", NGN),
                    external_ref="credit-2",
                    description="Customer receipts batch A",
                    reference="BATCH-A",
                ),
            ),
        ),
        imported_at=NOW,
        imported_by_id=uuid4(),
    )
    first = record_cash_observation(
        db,
        tenant_id=tenant.id,
        command=CashObservationInput(
            account_id=account.id,
            effective_on=date(2026, 7, 9),
            direction=StatementLineDirection.CREDIT,
            amount=Money.of("60", NGN),
            source_ref="receipt:1",
            source_version="1",
            evidence_ref="receipt:1:posted",
            description="Customer receipt",
            reference="BATCH-A",
        ),
        observed_at=NOW,
    )
    second = record_cash_observation(
        db,
        tenant_id=tenant.id,
        command=CashObservationInput(
            account_id=account.id,
            effective_on=date(2026, 7, 10),
            direction=StatementLineDirection.CREDIT,
            amount=Money.of("40", NGN),
            source_ref="receipt:2",
            source_version="1",
            evidence_ref="receipt:2:posted",
            description="Customer receipt",
            reference="BATCH-A",
        ),
        observed_at=NOW,
    )
    policy = create_match_policy(
        db,
        tenant_id=tenant.id,
        command=MatchPolicyInput(
            code="REFERENCE-NEAR-DATE",
            name="Reference and date policy",
            amount_tolerance=Decimal("0.00"),
            date_window_days=3,
            reference_match_mode="exact",
            amount_weight=50,
            date_weight=20,
            reference_weight=30,
            minimum_confidence=70,
        ),
    )
    line = statement.lines[0]

    suggestions = suggest_matches(
        db,
        tenant_id=tenant.id,
        statement_line_id=line.id,
        policy_id=policy.id,
    )
    assert {item.observation_id for item in suggestions} == {first.id, second.id}
    assert all(item.confidence >= 70 for item in suggestions)

    decision = accept_match(
        db,
        tenant_id=tenant.id,
        statement_line_id=line.id,
        allocations=((first.id, Money.of("60", NGN)), (second.id, Money.of("40", NGN))),
        decided_by_id=uuid4(),
        decided_at=NOW,
        policy_id=policy.id,
    )
    rows = db.scalars(
        select(MatchAllocation).where(MatchAllocation.decision_id == decision.id)
    ).all()
    assert sum((row.amount for row in rows), Decimal(0)) == Decimal("100.00")
    assert line.is_matched is True


def test_reconciliation_requires_zero_difference_and_separate_approval(
    db: Session,
) -> None:
    tenant = _tenant(db)
    _, account = _account(db, tenant.id)
    preparer, approver = uuid4(), uuid4()
    statement = import_bank_statement(
        db,
        tenant_id=tenant.id,
        command=BankStatementInput(
            account_id=account.id,
            statement_ref="statement:empty",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            opening_balance=Money.of("1000", NGN),
            closing_balance=Money.of("1000", NGN),
            source_ref="upload:3",
            source_version="1",
            evidence_ref="file:sha256:empty",
            lines=(),
        ),
        imported_at=NOW,
        imported_by_id=preparer,
    )
    reconciliation = prepare_reconciliation(
        db,
        tenant_id=tenant.id,
        statement_id=statement.id,
        cash_opening_balance=Money.of("1000", NGN),
        prepared_by_id=preparer,
        prepared_at=NOW,
    )
    assert reconciliation.difference == Decimal("0.00")

    with pytest.raises(MatchRuleViolation, match="preparer cannot approve"):
        approve_reconciliation(
            db,
            tenant_id=tenant.id,
            reconciliation_id=reconciliation.id,
            approved_by_id=preparer,
            approved_at=NOW,
        )

    approved = approve_reconciliation(
        db,
        tenant_id=tenant.id,
        reconciliation_id=reconciliation.id,
        approved_by_id=approver,
        approved_at=NOW,
    )
    assert approved.status == "approved"
