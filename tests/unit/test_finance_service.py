"""Persistence behavior for asset books and their accounting consequences."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_finance import (
    AccountingModel,
    AccountMapping,
    CapitalizeAssetBook,
    DepreciationMethod,
    DisposalCommand,
    FinanceConflict,
    FinanceRuleViolation,
    ImpairmentCommand,
    RevaluationCommand,
    calculate_depreciation_run,
    capitalize_asset_book,
    dispose_asset_book,
    impair_asset_book,
    post_depreciation_run,
    revalue_asset_book,
)
from dotmac_finance.models import (
    TENANT_MODELS,
    AccountingConsequence,
    AccountingConsequenceLine,
    AccountingEvent,
    AssetBook,
    DepreciationLine,
)
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_finance": None}},
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


def _accounts() -> AccountMapping:
    return AccountMapping(
        asset="account:ppe",
        accumulated_depreciation="account:accum-dep",
        accumulated_impairment="account:accum-impairment",
        depreciation_expense="account:dep-expense",
        impairment_loss="account:impairment",
        revaluation_reserve="account:revaluation-reserve",
        disposal_gain_loss="account:disposal-gain-loss",
    )


def _book(
    db: Session,
    tenant_id,
    *,
    asset_id=None,
    actor_id=None,
    accounting_model: AccountingModel = AccountingModel.COST,
) -> AssetBook:
    return capitalize_asset_book(
        db,
        tenant_id=tenant_id,
        command=CapitalizeAssetBook(
            asset_id=asset_id or uuid4(),
            book_code="IFRS",
            available_for_use_on=date(2026, 1, 1),
            acquisition_cost=Money.of("12000", NGN),
            functional_cost=Money.of("12000", NGN),
            residual_value=Money.of("0", NGN),
            useful_life_months=60,
            method=DepreciationMethod.STRAIGHT_LINE,
            accounting_model=accounting_model,
            accounts=_accounts(),
            source_ref=f"ap-line:{uuid4()}",
            source_version="1",
            evidence_ref="invoice:INV-001",
            actor_id=actor_id or uuid4(),
        ),
        recorded_at=NOW,
    )


def _balanced(db: Session, consequence_id) -> tuple[Decimal, Decimal]:
    lines = db.scalars(
        select(AccountingConsequenceLine).where(
            AccountingConsequenceLine.consequence_id == consequence_id
        )
    ).all()
    debit = sum((line.amount for line in lines if line.side == "debit"), Decimal(0))
    credit = sum((line.amount for line in lines if line.side == "credit"), Decimal(0))
    return debit, credit


def test_capitalization_uses_an_opaque_asset_and_preserves_source_identity(
    db: Session,
) -> None:
    tenant = _tenant(db)
    asset_id = uuid4()
    book = _book(db, tenant.id, asset_id=asset_id)

    assert book.asset_id == asset_id
    assert book.carrying_amount == Decimal("12000.00")
    assert book.version == 1
    event = db.scalar(select(AccountingEvent).where(AccountingEvent.book_id == book.id))
    assert event is not None and event.event_type == "capitalized"

    with pytest.raises(FinanceConflict, match="already has book"):
        _book(db, tenant.id, asset_id=asset_id)


def test_depreciation_calculation_is_non_mutating_until_a_different_actor_posts(
    db: Session,
) -> None:
    tenant = _tenant(db)
    creator = uuid4()
    book = _book(db, tenant.id)
    run = calculate_depreciation_run(
        db,
        tenant_id=tenant.id,
        run_ref="DEP-2026-01",
        period_ref="2026-01",
        through_date=date(2026, 1, 31),
        created_by_id=creator,
        calculated_at=NOW,
    )

    assert run.status == "calculated"
    assert book.carrying_amount == Decimal("12000.00")
    line = db.scalar(select(DepreciationLine).where(DepreciationLine.run_id == run.id))
    assert line is not None and line.depreciation_amount == Decimal("200.00")

    with pytest.raises(FinanceRuleViolation, match="creator cannot post"):
        post_depreciation_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            posted_by_id=creator,
            posted_at=NOW,
        )

    posted = post_depreciation_run(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        posted_by_id=uuid4(),
        posted_at=NOW,
    )
    assert posted.status == "posted"
    assert book.carrying_amount == Decimal("11800.00")
    assert book.accumulated_depreciation == Decimal("200.00")
    consequence = db.scalar(
        select(AccountingConsequence).where(AccountingConsequence.source_id == run.id)
    )
    assert consequence is not None
    assert _balanced(db, consequence.id) == (Decimal("200.00"), Decimal("200.00"))


def test_posting_refuses_a_stale_calculated_book_snapshot(db: Session) -> None:
    tenant = _tenant(db)
    book = _book(db, tenant.id)
    run = calculate_depreciation_run(
        db,
        tenant_id=tenant.id,
        run_ref="DEP-STALE",
        period_ref="2026-01",
        through_date=date(2026, 1, 31),
        created_by_id=uuid4(),
        calculated_at=NOW,
    )
    book.version += 1
    db.flush()

    with pytest.raises(FinanceConflict, match="stale"):
        post_depreciation_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            posted_by_id=uuid4(),
            posted_at=NOW,
        )


def test_impairment_revaluation_and_disposal_each_emit_balanced_consequences(
    db: Session,
) -> None:
    tenant = _tenant(db)
    requester, approver = uuid4(), uuid4()
    book = _book(db, tenant.id, accounting_model=AccountingModel.REVALUATION)

    impaired = impair_asset_book(
        db,
        tenant_id=tenant.id,
        command=ImpairmentCommand(
            book_id=book.id,
            expected_version=book.version,
            effective_on=date(2026, 2, 28),
            fair_value_less_costs_of_disposal=Money.of("10800", NGN),
            value_in_use=Money.of("11000", NGN),
            basis="value_in_use",
            evidence_ref="assessment:1",
            approval_ref="approval:1",
            requested_by_id=requester,
            approved_by_id=approver,
        ),
        recorded_at=NOW,
    )
    assert book.carrying_amount == Decimal("11000.00")
    assert _balanced(db, impaired.id)[0] == _balanced(db, impaired.id)[1]

    with pytest.raises(FinanceRuleViolation, match="reverse impairment first"):
        revalue_asset_book(
            db,
            tenant_id=tenant.id,
            command=RevaluationCommand(
                book_id=book.id,
                expected_version=book.version,
                effective_on=date(2026, 3, 31),
                fair_value=Money.of("13000", NGN),
                valuation_method="market",
                evidence_ref="valuation:1",
                approval_ref="approval:2",
                requested_by_id=requester,
                approved_by_id=approver,
            ),
            recorded_at=NOW,
        )

    reversal = impair_asset_book(
        db,
        tenant_id=tenant.id,
        command=ImpairmentCommand(
            book_id=book.id,
            expected_version=book.version,
            effective_on=date(2026, 3, 31),
            fair_value_less_costs_of_disposal=Money.of("12000", NGN),
            value_in_use=None,
            basis="fair_value_less_costs_of_disposal",
            evidence_ref="assessment:2",
            approval_ref="approval:3",
            requested_by_id=requester,
            approved_by_id=approver,
        ),
        recorded_at=NOW,
    )
    assert _balanced(db, reversal.id)[0] == _balanced(db, reversal.id)[1]

    revaluation = revalue_asset_book(
        db,
        tenant_id=tenant.id,
        command=RevaluationCommand(
            book_id=book.id,
            expected_version=book.version,
            effective_on=date(2026, 4, 30),
            fair_value=Money.of("13000", NGN),
            valuation_method="market",
            evidence_ref="valuation:2",
            approval_ref="approval:4",
            requested_by_id=requester,
            approved_by_id=approver,
        ),
        recorded_at=NOW,
    )
    assert _balanced(db, revaluation.id)[0] == _balanced(db, revaluation.id)[1]

    disposal = dispose_asset_book(
        db,
        tenant_id=tenant.id,
        command=DisposalCommand(
            book_id=book.id,
            expected_version=book.version,
            asset_disposal_ref="assets:disposal:42",
            effective_on=date(2026, 5, 31),
            proceeds=Money.of("14000", NGN),
            costs_of_disposal=Money.of("500", NGN),
            clearing_account_ref="account:disposal-clearing",
            evidence_ref="receipt:42",
            approval_ref="approval:5",
            requested_by_id=requester,
            approved_by_id=approver,
        ),
        recorded_at=NOW,
    )
    assert book.status == "derecognized"
    assert book.carrying_amount == Decimal("0.00")
    assert _balanced(db, disposal.id)[0] == _balanced(db, disposal.id)[1]


def test_creator_cannot_supply_their_own_approval(db: Session) -> None:
    tenant = _tenant(db)
    actor = uuid4()
    book = _book(db, tenant.id)
    with pytest.raises(FinanceRuleViolation, match="separation of duties"):
        impair_asset_book(
            db,
            tenant_id=tenant.id,
            command=ImpairmentCommand(
                book_id=book.id,
                expected_version=book.version,
                effective_on=date(2026, 2, 28),
                fair_value_less_costs_of_disposal=Money.of("10000", NGN),
                value_in_use=None,
                basis="market",
                evidence_ref="assessment:bad",
                approval_ref="approval:bad",
                requested_by_id=actor,
                approved_by_id=actor,
            ),
            recorded_at=NOW,
        )
