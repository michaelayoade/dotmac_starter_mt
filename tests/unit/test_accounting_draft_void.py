"""Accounting 0.1.0a2: composed-safe creators and the governed draft void."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_accounting import models
from dotmac_accounting.contracts import (
    AccountClass,
    AccountKind,
    CreateAccount,
    CreateAccountCategory,
    CreateFiscalPeriod,
    CreateFiscalYear,
    CreateJournal,
    InvalidTransition,
    JournalKind,
    JournalLineInput,
    NormalBalance,
    SourceIdentity,
    VoidDraftJournal,
)
from dotmac_accounting.service import (
    create_account,
    create_account_category,
    create_fiscal_period,
    create_fiscal_year,
    create_journal,
    open_period,
    post_journal,
    reverse_journal,
    void_draft_journal,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _setup(db: Session):
    scope = TenantScope(TENANT)
    assets = create_account_category(
        db,
        scope=scope,
        command=CreateAccountCategory("a", "Assets", AccountClass.ASSET),
    )
    equity = create_account_category(
        db,
        scope=scope,
        command=CreateAccountCategory("e", "Equity", AccountClass.EQUITY),
    )
    cash = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "1000", "Cash", assets.id, AccountKind.POSTING, NormalBalance.DEBIT
        ),
    )
    capital = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "3000", "Capital", equity.id, AccountKind.POSTING, NormalBalance.CREDIT
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
        db, scope=scope, period_id=period.id, actor_ref="user:c", occurred_at=NOW
    )
    return scope, cash, capital


def _draft(db: Session, scope, cash, capital, number: str = "JNL-1"):
    return create_journal(
        db,
        scope=scope,
        command=CreateJournal(
            number=number,
            kind=JournalKind.STANDARD,
            entry_date=date(2026, 8, 19),
            posting_date=date(2026, 8, 19),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            description="Orphan",
            source=SourceIdentity(
                "erp.manual", "manual_journal", number, "1", "a" * 64
            ),
            lines=(
                JournalLineInput(cash.id, debit=Decimal("10")),
                JournalLineInput(capital.id, credit=Decimal("10")),
            ),
        ),
        idempotency_key=f"create:{number}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )


def _void(db, scope, journal_id, *, key="void:1", **overrides):
    values = {
        "journal_id": journal_id,
        "reason": "Rejected request left an orphan draft",
        "voided_by": "user:requester",
        "approved_by": "user:approver",
        "approval_reference": "approval:void:1",
    }
    values.update(overrides)
    return void_draft_journal(
        db,
        scope=scope,
        command=VoidDraftJournal(**values),
        idempotency_key=key,
        idempotency_expires_at=None,
        voided_at=NOW,
    )


def test_creators_work_in_a_composed_app_without_the_kernel_reference_runtime() -> None:
    """No DATABASE_URL, injected session: ``dotmac_kernel.db`` must never load."""
    program = textwrap.dedent(
        """
        import sys
        from datetime import date
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from dotmac_accounting import models
        from dotmac_accounting.contracts import *
        from dotmac_accounting.service import *
        from dotmac_kernel.cache import TenantScope
        from dotmac_kernel.idempotency_models import IdempotencyRecord
        from dotmac_kernel.models import Tenant
        import uuid
        t = uuid.uuid4()
        e = create_engine("sqlite:///:memory:",
            execution_options={"schema_translate_map": {"mod_accounting": None}})
        Tenant.__table__.create(e); IdempotencyRecord.__table__.create(e)
        for n in models.TABLES: models.metadata_table(n).create(e)
        with Session(e) as db:
            db.add(Tenant(id=t, slug="a", name="A")); db.flush()
            s = TenantScope(t)
            c = create_account_category(db, scope=s,
                command=CreateAccountCategory("a", "A", AccountClass.ASSET))
            create_account(db, scope=s, command=CreateAccount(
                "1000", "Cash", c.id, AccountKind.POSTING, NormalBalance.DEBIT))
            y = create_fiscal_year(db, scope=s, command=CreateFiscalYear(
                "fy", "FY", date(2027, 1, 1), date(2027, 12, 31)))
            create_fiscal_period(db, scope=s, command=CreateFiscalPeriod(
                y.id, 1, "Jan", date(2027, 1, 1), date(2027, 1, 31)))
            d = create_dimension(db, scope=s, command=CreateDimension(
                "cc", "Cost centre"))
            create_dimension_value(db, scope=s, command=CreateDimensionValue(
                d.id, "hq", "HQ"))
        assert "dotmac_kernel.db" not in sys.modules, "kernel reference runtime loaded"
        print("ok")
        """
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "MIGRATION_DATABASE_URL"}
    }
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"


def test_duplicate_creator_still_conflicts_and_keeps_the_session_usable(
    db: Session,
) -> None:
    from dotmac_accounting.contracts import Conflict

    scope = TenantScope(TENANT)
    command = CreateAccountCategory("a", "Assets", AccountClass.ASSET)
    create_account_category(db, scope=scope, command=command)
    with pytest.raises(Conflict):
        create_account_category(db, scope=scope, command=command)
    create_account_category(
        db, scope=scope, command=CreateAccountCategory("b", "B", AccountClass.EQUITY)
    )


def test_draft_void_records_reason_actors_audit_and_replays(db: Session) -> None:
    scope, cash, capital = _setup(db)
    draft = _draft(db, scope, cash, capital)
    voided = _void(db, scope, draft.id)
    replay = _void(db, scope, draft.id)
    assert voided.id == replay.id == draft.id
    assert voided.status.value == "VOID"
    events = list(
        db.scalars(
            select(models.PeriodEvent).where(
                models.PeriodEvent.event_kind == "JOURNAL_VOID"
            )
        )
    )
    assert len(events) == 1
    event = events[0]
    assert event.actor_ref == "user:requester"
    assert event.approval_reference == "approval:void:1"
    assert event.evidence["approved_by"] == "user:approver"
    assert event.evidence["journal_id"] == str(draft.id)
    assert "orphan draft" in str(event.evidence["reason"])
    assert db.scalar(select(func.count()).select_from(models.PostedLedgerLine)) == 0


def test_void_refuses_same_actor_blank_reason_and_a_second_new_key(db: Session) -> None:
    scope, cash, capital = _setup(db)
    draft = _draft(db, scope, cash, capital)
    with pytest.raises(InvalidTransition, match="differ"):
        _void(db, scope, draft.id, approved_by="USER:Requester")
    with pytest.raises(Exception, match="void reason"):
        _void(db, scope, draft.id, reason="  ")
    _void(db, scope, draft.id)
    with pytest.raises(InvalidTransition):
        _void(db, scope, draft.id, key="void:other")


def test_posted_and_reversed_journals_can_never_be_voided(db: Session) -> None:
    from dotmac_accounting.contracts import ReverseJournal

    scope, cash, capital = _setup(db)
    draft = _draft(db, scope, cash, capital)
    post_journal(
        db,
        scope=scope,
        journal_id=draft.id,
        approval_reference="approval:1",
        posted_by="user:p",
        idempotency_key="post:1",
        idempotency_expires_at=None,
        posted_at=NOW,
    )
    with pytest.raises(InvalidTransition, match="reversal"):
        _void(db, scope, draft.id)
    reverse_journal(
        db,
        scope=scope,
        command=ReverseJournal(
            draft.id, "REV-1", date(2026, 8, 20), "mistake", "approval:r", "user:r"
        ),
        idempotency_key="rev:1",
        idempotency_expires_at=None,
        reversed_at=NOW,
    )
    with pytest.raises(InvalidTransition):
        _void(db, scope, draft.id, key="void:2")
    assert (
        db.scalar(
            select(func.count())
            .select_from(models.PeriodEvent)
            .where(models.PeriodEvent.event_kind == "JOURNAL_VOID")
        )
        == 0
    )
