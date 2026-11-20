"""Accounting 0.1.0a3: governed recode, flag update, retire and activate."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from dotmac_accounting import models
from dotmac_accounting.contracts import (
    AccountingError,
    AccountKind,
    ActivateAccount,
    Conflict,
    CreateAccount,
    InvalidAccount,
    NormalBalance,
    RecodeAccount,
    RetireAccount,
    ReverseJournal,
    UpdateAccountFlags,
)
from dotmac_accounting.models import AccountChange, PostedLedgerLine
from dotmac_accounting.service import (
    activate_account,
    create_account,
    post_journal,
    recode_account,
    retire_account,
    reverse_journal,
    update_account_flags,
)
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.unit.test_accounting_draft_void import NOW, TENANT, _draft, _setup


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

_CHANGE_CLOCK = itertools.count(1)


def _next_change_time():
    # Account-change ids are random UUIDs and occurred_at is caller-supplied, so
    # two changes stamped with the same instant have no defined order. Give each
    # maintenance command its own strictly increasing instant so history order
    # is the causal order the tests assert.
    return NOW + timedelta(seconds=next(_CHANGE_CLOCK))


def _recode(db, scope, account, new_code="1100", *, key="recode:1", **overrides):
    values = {
        "account_id": account.id,
        "new_code": new_code,
        "reason": "Finance chart recode (D-M4)",
        "actor": "user:finance",
        "code_pattern": r"[0-9]{4}",
    }
    values.update(overrides)
    return recode_account(
        db,
        scope=scope,
        command=RecodeAccount(**values),
        idempotency_key=key,
        idempotency_expires_at=None,
        changed_at=_next_change_time(),
    )


def _retire(db, scope, account, *, key="retire:1", open_items=0, **overrides):
    values = {
        "account_id": account.id,
        "reason": "unused account",
        "actor": "user:finance",
        "open_subledger_items": open_items,
    }
    values.update(overrides)
    return retire_account(
        db,
        scope=scope,
        command=RetireAccount(**values),
        idempotency_key=key,
        idempotency_expires_at=None,
        changed_at=_next_change_time(),
    )


def _activate(db, scope, account, *, key="activate:1", **overrides):
    values = {
        "account_id": account.id,
        "reason": "needed again",
        "actor": "user:finance",
    }
    values.update(overrides)
    return activate_account(
        db,
        scope=scope,
        command=ActivateAccount(**values),
        idempotency_key=key,
        idempotency_expires_at=None,
        changed_at=_next_change_time(),
    )


def _flags(db, scope, account, *, key="flags:1", **overrides):
    values = {
        "account_id": account.id,
        "reason": "control flag corrected",
        "actor": "user:finance",
    }
    values.update(overrides)
    return update_account_flags(
        db,
        scope=scope,
        command=UpdateAccountFlags(**values),
        idempotency_key=key,
        idempotency_expires_at=None,
        changed_at=_next_change_time(),
    )


def _post(db, scope, journal, key="post:1"):
    return post_journal(
        db,
        scope=scope,
        journal_id=journal.id,
        approval_reference="approval:1",
        posted_by="user:p",
        idempotency_key=key,
        idempotency_expires_at=None,
        posted_at=NOW,
    )


def _history(db, account):
    return list(
        db.scalars(
            select(AccountChange)
            .where(AccountChange.account_id == account.id)
            .order_by(AccountChange.occurred_at, AccountChange.id)
        )
    )


def test_recode_keeps_identity_records_history_and_replays(db) -> None:
    scope, cash, _capital = _setup(db)
    identity = cash.id
    recoded = _recode(db, scope, cash)
    assert recoded.id == identity
    assert recoded.code == "1100"
    (change,) = _history(db, cash)
    assert (change.change_kind, change.old_code, change.new_code) == (
        "RECODE",
        "1000",
        "1100",
    )
    assert change.actor_ref == "user:finance"
    assert change.reason == "Finance chart recode (D-M4)"
    # Same key and payload replays without a second history row.
    assert _recode(db, scope, cash).code == "1100"
    assert len(_history(db, cash)) == 1
    # A new key for a no-op recode is refused, not silently recorded.
    with pytest.raises(InvalidAccount, match="equals the current code"):
        _recode(db, scope, cash, key="recode:2")
    # The same key with another payload is a kernel idempotency conflict.
    with pytest.raises(Exception, match=r"(?i)idempot|different|fingerprint"):
        _recode(db, scope, cash, new_code="1200")


def test_recode_refuses_policy_mismatch_duplicates_and_blank_inputs(db) -> None:
    scope, cash, capital = _setup(db)
    with pytest.raises(InvalidAccount, match="chart code policy"):
        _recode(db, scope, cash, "CASH-AT-BANK", key="r1")
    with pytest.raises(InvalidAccount, match="valid pattern"):
        _recode(db, scope, cash, "1100", key="r2", code_pattern="[")
    with pytest.raises(Conflict):
        _recode(db, scope, cash, capital.code, key="r3", code_pattern=None)
    with pytest.raises(AccountingError, match="reason is required"):
        _recode(db, scope, cash, key="r4", reason=" ")
    with pytest.raises(AccountingError, match="actor is required"):
        _recode(db, scope, cash, key="r5", actor="")
    assert _history(db, cash) == []
    assert cash.code == "1000"


def test_posted_lines_keep_their_code_and_reports_follow_the_account(db) -> None:
    scope, cash, capital = _setup(db)
    first = _draft(db, scope, cash, capital, "JNL-1")
    _post(db, scope, first)
    _recode(db, scope, cash, "1100")
    second = _draft(db, scope, cash, capital, "JNL-2")
    _post(db, scope, second, key="post:2")
    rows = list(
        db.scalars(
            select(PostedLedgerLine)
            .where(PostedLedgerLine.account_id == cash.id)
            .order_by(PostedLedgerLine.journal_number)
        )
    )
    # History keeps the code posted under; the identity is the account id.
    assert [(r.journal_number, r.account_code) for r in rows] == [
        ("JNL-1", "1000"),
        ("JNL-2", "1100"),
    ]
    assert {r.account_id for r in rows} == {cash.id}


def test_flag_update_corrects_control_flags_with_history(db) -> None:
    scope, cash, capital = _setup(db)
    updated = _flags(db, scope, cash, kind=AccountKind.CONTROL, posting_allowed=False)
    assert (updated.kind, updated.posting_allowed) == (AccountKind.CONTROL, False)
    (change,) = _history(db, cash)
    assert (change.old_kind, change.new_kind) == ("POSTING", "CONTROL")
    assert (change.old_posting_allowed, change.new_posting_allowed) == (True, False)
    with pytest.raises(InvalidAccount, match="at least one flag"):
        _flags(db, scope, capital, key="f2")
    with pytest.raises(InvalidAccount, match="already have"):
        _flags(db, scope, cash, key="f3", kind=AccountKind.CONTROL)
    with pytest.raises(InvalidAccount, match="only a posting account"):
        _flags(db, scope, cash, key="f4", posting_allowed=True)
    # Back to a posting account.
    back = _flags(
        db, scope, cash, key="f5", kind=AccountKind.POSTING, posting_allowed=True
    )
    assert back.kind == AccountKind.POSTING and back.posting_allowed is True
    assert len(_history(db, cash)) == 2


def test_flag_update_refuses_posting_parent(db) -> None:
    scope, cash, _capital = _setup(db)
    control = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "1900",
            "Receivables control",
            cash.category_id,
            AccountKind.CONTROL,
            NormalBalance.DEBIT,
            posting_allowed=False,
        ),
    )
    create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "1901",
            "Child",
            cash.category_id,
            AccountKind.POSTING,
            NormalBalance.DEBIT,
            parent_id=control.id,
        ),
    )
    with pytest.raises(InvalidAccount, match="parent account"):
        _flags(db, scope, control, kind=AccountKind.POSTING, posting_allowed=True)


def test_retire_and_activate_unused_account(db) -> None:
    scope, cash, capital = _setup(db)
    spare = create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "9000", "Spare", cash.category_id, AccountKind.POSTING, NormalBalance.DEBIT
        ),
    )
    retired = _retire(db, scope, spare)
    assert (retired.is_active, retired.posting_allowed) == (False, False)
    assert retired.id == spare.id and retired.code == "9000"
    with pytest.raises(InvalidAccount, match="already retired"):
        _retire(db, scope, spare, key="retire:2")
    # A retired account cannot be journalled.
    with pytest.raises(InvalidAccount, match="not open for posting"):
        _draft_on(db, scope, spare, capital)
    control = create_control(db, scope, cash)
    _retire(db, scope, control, key="retire:control")
    with pytest.raises(InvalidAccount, match="only a posting account"):
        _activate(db, scope, control, key="activate:control", posting_allowed=True)
    reactivated = _activate(db, scope, spare)
    assert (reactivated.is_active, reactivated.posting_allowed) == (True, False)
    with pytest.raises(InvalidAccount, match="already active"):
        _activate(db, scope, spare, key="activate:2")
    kinds = [c.change_kind for c in _history(db, spare)]
    assert kinds == ["RETIRE", "ACTIVATE"]


def _draft_on(db, scope, account, other):
    from decimal import Decimal

    from dotmac_accounting.contracts import (
        CreateJournal,
        JournalKind,
        JournalLineInput,
        SourceIdentity,
    )
    from dotmac_accounting.service import create_journal

    return create_journal(
        db,
        scope=scope,
        command=CreateJournal(
            number="JNL-R",
            kind=JournalKind.STANDARD,
            entry_date=date(2026, 8, 19),
            posting_date=date(2026, 8, 19),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            description="On retired",
            source=SourceIdentity("erp.manual", "manual_journal", "R", "1", "a" * 64),
            lines=(
                JournalLineInput(account.id, debit=Decimal("5")),
                JournalLineInput(other.id, credit=Decimal("5")),
            ),
        ),
        idempotency_key="create:JNL-R",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )


def create_control(db, scope, like):
    return create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "1990",
            "Control",
            like.category_id,
            AccountKind.CONTROL,
            NormalBalance.DEBIT,
            posting_allowed=False,
        ),
    )


def test_retire_refuses_balance_drafts_children_and_open_items(db) -> None:
    scope, cash, capital = _setup(db)
    # Draft usage refuses.
    draft = _draft(db, scope, cash, capital, "JNL-1")
    with pytest.raises(InvalidAccount, match="draft journal"):
        _retire(db, scope, cash, key="a")
    # Posted balance refuses.
    _post(db, scope, draft)
    with pytest.raises(InvalidAccount, match="balance"):
        _retire(db, scope, cash, key="b")
    # A fully reversed journal nets to zero and no longer blocks.
    reverse_journal(
        db,
        scope=scope,
        command=ReverseJournal(
            draft.id, "REV-1", date(2026, 8, 20), "undo", "approval:r", "user:r"
        ),
        idempotency_key="rev:1",
        idempotency_expires_at=None,
        reversed_at=NOW,
    )
    assert db.scalar(select(func.count()).select_from(PostedLedgerLine)) == 4
    # Caller-attested open subledger items refuse.
    with pytest.raises(InvalidAccount, match="open subledger"):
        _retire(db, scope, cash, key="c", open_items=2)
    assert _history(db, cash) == []
    retired = _retire(db, scope, cash, key="d")
    assert retired.is_active is False
    # Active children refuse.
    parent = create_control(db, scope, capital)
    create_account(
        db,
        scope=scope,
        command=CreateAccount(
            "1991",
            "Child",
            capital.category_id,
            AccountKind.POSTING,
            NormalBalance.DEBIT,
            parent_id=parent.id,
        ),
    )
    with pytest.raises(InvalidAccount, match="child"):
        _retire(db, scope, parent, key="e")


def test_negative_or_non_integer_open_item_attestation_is_refused(db) -> None:
    scope, cash, _ = _setup(db)
    with pytest.raises(InvalidAccount, match="negative"):
        _retire(db, scope, cash, key="n", open_items=-1)
    with pytest.raises(InvalidAccount, match="integer"):
        _retire(db, scope, cash, key="t", open_items=True)


def test_kind_change_away_from_posting_refused_once_posted(db) -> None:
    scope, cash, capital = _setup(db)
    _post(db, scope, _draft(db, scope, cash, capital, "JNL-1"))
    for kind in (AccountKind.CONTROL, AccountKind.STATISTICAL):
        with pytest.raises(InvalidAccount, match="posted ledger lines"):
            _flags(
                db,
                scope,
                cash,
                key=f"k:{kind.value}",
                kind=kind,
                posting_allowed=False,
            )
    assert cash.kind == AccountKind.POSTING
    assert _history(db, cash) == []
