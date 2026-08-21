"""ERP parity canaries for the extracted accounting owner."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_accounting import models
from dotmac_accounting.contracts import (
    AccountClass,
    AccountKind,
    CloseCheck,
    CreateAccount,
    CreateAccountCategory,
    CreateDimension,
    CreateDimensionValue,
    CreateFiscalPeriod,
    CreateFiscalYear,
    CreateJournal,
    InvalidJournal,
    InvalidPeriod,
    JournalKind,
    JournalLineInput,
    NormalBalance,
    PeriodCloseEvidence,
    PeriodStatus,
    ReverseJournal,
    SourceIdentity,
)
from dotmac_accounting.service import (
    create_account,
    create_account_category,
    create_dimension,
    create_dimension_value,
    create_fiscal_period,
    create_fiscal_year,
    create_journal,
    lock_period,
    open_period,
    post_journal,
    reopen_period,
    reverse_journal,
    soft_close_period,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_accounting": None}},
    )
    Tenant.__table__.create(engine)
    IdempotencyRecord.__table__.create(engine)
    for table_name in models.TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, slug="alpha", name="Alpha"))
        session.flush()
        yield session
    engine.dispose()


def _chart_and_period(db: Session):
    scope = TenantScope(TENANT)
    assets = create_account_category(
        db,
        scope=scope,
        command=CreateAccountCategory("asset", "Assets", AccountClass.ASSET),
    )
    equity = create_account_category(
        db,
        scope=scope,
        command=CreateAccountCategory("equity", "Equity", AccountClass.EQUITY),
    )
    cash = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            code="1000",
            name="Cash",
            category_id=assets.id,
            kind=AccountKind.POSTING,
            normal_balance=NormalBalance.DEBIT,
        ),
    )
    capital = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            code="3000",
            name="Capital",
            category_id=equity.id,
            kind=AccountKind.POSTING,
            normal_balance=NormalBalance.CREDIT,
        ),
    )
    year = create_fiscal_year(
        db,
        scope=scope,
        command=CreateFiscalYear(
            "fy26", "FY 2026", date(2026, 1, 1), date(2026, 12, 31)
        ),
    )
    period = create_fiscal_period(
        db,
        scope=scope,
        command=CreateFiscalPeriod(
            year.id, 8, "August", date(2026, 8, 1), date(2026, 8, 31)
        ),
    )
    open_period(
        db,
        scope=scope,
        period_id=period.id,
        actor_ref="user:controller",
        occurred_at=NOW,
    )
    return scope, cash, capital, period


def _journal_command(cash_id: uuid.UUID, capital_id: uuid.UUID) -> CreateJournal:
    return CreateJournal(
        number="JNL-2026-0001",
        kind=JournalKind.STANDARD,
        entry_date=date(2026, 8, 19),
        posting_date=date(2026, 8, 19),
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        description="Opening capital",
        source=SourceIdentity(
            owner="erp.manual",
            document_kind="manual_journal",
            document_id="JNL-2026-0001",
            version="1",
            fingerprint="a" * 64,
        ),
        lines=(
            JournalLineInput(cash_id, debit=Decimal("1000")),
            JournalLineInput(capital_id, credit=Decimal("1000")),
        ),
    )


def test_balanced_journal_posts_once_and_appends_ledger_evidence(db: Session) -> None:
    scope, cash, capital, period = _chart_and_period(db)
    journal = create_journal(
        db,
        scope=scope,
        command=_journal_command(cash.id, capital.id),
        idempotency_key="journal:create:1",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    posted = post_journal(
        db,
        scope=scope,
        journal_id=journal.id,
        approval_reference="approval:journal:1",
        posted_by="user:controller",
        idempotency_key="journal:post:1",
        idempotency_expires_at=None,
        posted_at=NOW,
    )
    replay = post_journal(
        db,
        scope=scope,
        journal_id=journal.id,
        approval_reference="approval:journal:1",
        posted_by="user:controller",
        idempotency_key="journal:post:1",
        idempotency_expires_at=None,
        posted_at=NOW,
    )
    assert posted.id == replay.id == journal.id
    assert posted.status.value == "POSTED"
    assert posted.fiscal_period_id == period.id
    assert db.scalar(select(func.count()).select_from(models.PostedLedgerLine)) == 2
    assert db.scalar(select(func.sum(models.PostedLedgerLine.debit))) == Decimal(
        "1000.000000"
    )
    assert db.scalar(select(func.sum(models.PostedLedgerLine.credit))) == Decimal(
        "1000.000000"
    )


def test_unbalanced_and_two_sided_lines_fail_before_persistence(db: Session) -> None:
    scope, cash, capital, _ = _chart_and_period(db)
    base = _journal_command(cash.id, capital.id)
    unbalanced = replace(
        base,
        number="BAD-1",
        source=SourceIdentity("test", "journal", "bad-1", "1", "b" * 64),
        lines=(
            JournalLineInput(cash.id, debit=Decimal("10")),
            JournalLineInput(capital.id, credit=Decimal("9")),
        ),
    )
    with pytest.raises(InvalidJournal, match="balance"):
        create_journal(
            db,
            scope=scope,
            command=unbalanced,
            idempotency_key="bad:1",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
    with pytest.raises(InvalidJournal, match="one side"):
        JournalLineInput(cash.id, debit=Decimal("1"), credit=Decimal("1")).validate()


def test_open_dimension_assignments_are_snapshotted_on_post(db: Session) -> None:
    scope, cash, capital, _ = _chart_and_period(db)
    dimension = create_dimension(
        db, scope=scope, command=CreateDimension("project", "Project")
    )
    value = create_dimension_value(
        db,
        scope=scope,
        command=CreateDimensionValue(dimension.id, "fibre", "Fibre build"),
    )
    command = _journal_command(cash.id, capital.id)
    command = replace(
        command,
        number="JNL-2026-0002",
        source=SourceIdentity("test", "journal", "2", "1", "c" * 64),
        lines=(
            JournalLineInput(
                cash.id,
                debit=Decimal("1000"),
                dimension_value_ids=(value.id,),
            ),
            JournalLineInput(capital.id, credit=Decimal("1000")),
        ),
    )
    journal = create_journal(
        db,
        scope=scope,
        command=command,
        idempotency_key="journal:create:2",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    post_journal(
        db,
        scope=scope,
        journal_id=journal.id,
        approval_reference="approval:2",
        posted_by="user:controller",
        idempotency_key="journal:post:2",
        idempotency_expires_at=None,
        posted_at=NOW,
    )
    snapshot = db.scalar(select(models.PostedLedgerDimension))
    assert snapshot is not None
    assert (snapshot.dimension_code, snapshot.value_code) == ("PROJECT", "FIBRE")


def test_reversal_posts_opposite_evidence_without_mutating_original(
    db: Session,
) -> None:
    scope, cash, capital, _ = _chart_and_period(db)
    journal = create_journal(
        db,
        scope=scope,
        command=_journal_command(cash.id, capital.id),
        idempotency_key="journal:create:3",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    post_journal(
        db,
        scope=scope,
        journal_id=journal.id,
        approval_reference="approval:3",
        posted_by="user:controller",
        idempotency_key="journal:post:3",
        idempotency_expires_at=None,
        posted_at=NOW,
    )
    reversal = reverse_journal(
        db,
        scope=scope,
        command=ReverseJournal(
            journal_id=journal.id,
            number="REV-2026-0001",
            posting_date=date(2026, 8, 20),
            reason="Incorrect opening",
            approval_reference="approval:reverse:3",
            reversed_by="user:controller",
        ),
        idempotency_key="journal:reverse:3",
        idempotency_expires_at=None,
        reversed_at=NOW + timedelta(days=1),
    )
    assert reversal.reverses_journal_id == journal.id
    assert journal.status.value == "REVERSED"
    assert db.scalar(select(func.count()).select_from(models.PostedLedgerLine)) == 4


def test_soft_close_reopen_and_lock_are_explicit_and_lock_is_terminal(
    db: Session,
) -> None:
    scope, _, _, period = _chart_and_period(db)
    evidence = PeriodCloseEvidence(
        checks=(CloseCheck("unposted", True, "evidence:unposted", "d" * 64),)
    )
    soft_close_period(
        db,
        scope=scope,
        period_id=period.id,
        evidence=evidence,
        actor_ref="user:controller",
        approval_reference="approval:close",
        occurred_at=NOW,
    )
    assert period.status == PeriodStatus.SOFT_CLOSED
    reopen_period(
        db,
        scope=scope,
        period_id=period.id,
        actor_ref="user:cfo",
        approval_reference="approval:reopen",
        reason="Late audited adjustment",
        occurred_at=NOW,
    )
    assert period.status == PeriodStatus.REOPENED
    assert period.reopen_token is not None
    soft_close_period(
        db,
        scope=scope,
        period_id=period.id,
        evidence=evidence,
        actor_ref="user:controller",
        approval_reference="approval:close:2",
        occurred_at=NOW,
    )
    lock_period(
        db,
        scope=scope,
        period_id=period.id,
        actor_ref="user:cfo",
        approval_reference="approval:lock",
        occurred_at=NOW,
    )
    with pytest.raises(InvalidPeriod, match="locked"):
        reopen_period(
            db,
            scope=scope,
            period_id=period.id,
            actor_ref="user:cfo",
            approval_reference="approval:impossible",
            reason="No",
            occurred_at=NOW,
        )
