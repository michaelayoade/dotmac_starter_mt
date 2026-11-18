"""Real-migration canaries for governed account maintenance (ac_0004)."""

from __future__ import annotations

import uuid

import pytest
from dotmac_accounting.contracts import (
    AccountKind,
    InvalidAccount,
    RecodeAccount,
    RetireAccount,
    UpdateAccountFlags,
)
from dotmac_accounting.service import (
    recode_account,
    retire_account,
    update_account_flags,
)
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_accounting_posted_content import (
    ACCOUNTING_VERSIONS,
    NOW,
    _config,
    _context,
    _seed,
    database,
    migrated_finance,
)

__all__ = ["database", "migrated_finance"]

COUNT = "SELECT count(*) FROM mod_accounting.account_changes"


def _account_id(conn, tenant: uuid.UUID) -> uuid.UUID:
    return conn.scalar(
        text("SELECT id FROM mod_accounting.accounts WHERE tenant_id=:t"),
        {"t": tenant},
    )


def _recode(db: Session, tenant: uuid.UUID, account: uuid.UUID, code: str, key: str):
    return recode_account(
        db,
        scope=TenantScope(tenant),
        command=RecodeAccount(
            account, code, "chart recode", "user:finance", code_pattern=r"[0-9]{4}"
        ),
        idempotency_key=key,
        idempotency_expires_at=None,
        changed_at=NOW,
    )


def test_service_recode_keeps_identity_and_posted_code(migrated_finance) -> None:
    admin_url, _ = migrated_finance
    evidence = _seed(admin_url, posted=True)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        account = _account_id(conn, evidence.tenant)
    with Session(engine) as db, db.begin():
        recoded = _recode(db, evidence.tenant, account, "1100", "recode:1")
        assert recoded.id == account and recoded.code == "1100"
    with Session(engine) as db, db.begin():  # replay
        assert _recode(db, evidence.tenant, account, "1100", "recode:1").code == "1100"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT change_kind, old_code, new_code, applied_txid IS NOT NULL "
                "FROM mod_accounting.account_changes WHERE account_id=:a"
            ),
            {"a": account},
        ).all()
        assert rows == [("RECODE", "1000", "1100", True)]
        # History: the posted ledger lines still carry the code they were posted
        # under, and still point at the same account id.
        posted = conn.execute(
            text(
                "SELECT DISTINCT account_code, account_id FROM "
                "mod_accounting.posted_ledger_lines WHERE tenant_id=:t"
            ),
            {"t": evidence.tenant},
        ).all()
        assert posted == [("1000", account)]
    engine.dispose()


def test_database_refuses_unrecorded_or_foreign_transaction_changes(
    migrated_finance,
) -> None:
    admin_url, _ = migrated_finance
    evidence = _seed(admin_url, posted=True)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        account = _account_id(conn, evidence.tenant)
    for statement in (
        "UPDATE mod_accounting.accounts SET code='9999' WHERE id=:a",
        "UPDATE mod_accounting.accounts SET is_active=false WHERE id=:a",
        "UPDATE mod_accounting.accounts SET kind='CONTROL',posting_allowed=false "
        "WHERE id=:a",
        "UPDATE mod_accounting.accounts SET id=gen_random_uuid() WHERE id=:a",
    ):
        with (
            engine.connect() as conn,
            pytest.raises(DBAPIError, match="account"),
        ):
            with conn.begin():
                conn.execute(text(statement), {"a": account})
    # A history row from an EARLIER transaction cannot authorise a later change.
    with Session(engine) as db, db.begin():
        _recode(db, evidence.tenant, account, "1100", "recode:1")
    with engine.connect() as conn, pytest.raises(DBAPIError, match="recorded"):
        with conn.begin():
            conn.execute(
                text("UPDATE mod_accounting.accounts SET code='1100x' WHERE id=:a"),
                {"a": account},
            )
    with engine.connect() as conn, pytest.raises(DBAPIError, match="recorded"):
        with conn.begin():
            conn.execute(
                text("UPDATE mod_accounting.accounts SET code='1000' WHERE id=:a"),
                {"a": account},
            )
    # A forged same-transaction row that does not match the new state also fails.
    with engine.connect() as conn, pytest.raises(DBAPIError, match="recorded"):
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO mod_accounting.account_changes (id,tenant_id,"
                    "account_id,change_kind,old_code,new_code,old_kind,new_kind,"
                    "old_posting_allowed,new_posting_allowed,old_is_active,"
                    "new_is_active,reason,actor_ref,evidence,occurred_at) "
                    "SELECT gen_random_uuid(),tenant_id,id,'RECODE',code,'4000',"
                    "kind,kind,posting_allowed,posting_allowed,is_active,is_active,"
                    "'r','a','{}'::jsonb,now() FROM mod_accounting.accounts "
                    "WHERE id=:a"
                ),
                {"a": account},
            )
            conn.execute(
                text("UPDATE mod_accounting.accounts SET code='5000' WHERE id=:a"),
                {"a": account},
            )
    engine.dispose()


def test_history_is_append_only_and_tenant_isolated(migrated_finance) -> None:
    admin_url, app_url = migrated_finance
    first = _seed(admin_url, posted=False)
    second = _seed(admin_url, posted=False)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        account = _account_id(conn, first.tenant)
    with Session(engine) as db, db.begin():
        _recode(db, first.tenant, account, "1100", "recode:1")
    for statement in (
        "UPDATE mod_accounting.account_changes SET reason='x'",
        "DELETE FROM mod_accounting.account_changes",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError, match="append-only"):
            with conn.begin():
                conn.execute(text(statement))
    app = create_engine(app_url)
    with app.connect() as conn, conn.begin():
        _context(conn, second.tenant)
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_accounting.account_changes"))
            == 0
        )
    with app.connect() as conn, conn.begin():
        _context(conn, first.tenant)
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_accounting.account_changes"))
            == 1
        )
    for statement in (
        "UPDATE mod_accounting.account_changes SET reason='x'",
        "DELETE FROM mod_accounting.account_changes",
    ):
        with app.connect() as conn, pytest.raises(DBAPIError):
            with conn.begin():
                _context(conn, first.tenant)
                conn.execute(text(statement))
    app.dispose()
    engine.dispose()


def test_service_retire_and_flags_on_postgres(migrated_finance) -> None:
    admin_url, _ = migrated_finance
    evidence = _seed(admin_url, posted=True)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        account = _account_id(conn, evidence.tenant)
    scope = TenantScope(evidence.tenant)
    # The seeded account holds a posted balance and a draft: retire is refused
    # and leaves neither history nor a changed account.
    with pytest.raises(InvalidAccount, match=r"draft journal|balance"):
        with Session(engine) as db, db.begin():
            retire_account(
                db,
                scope=scope,
                command=RetireAccount(account, "unused", "user:finance", 0),
                idempotency_key="retire:1",
                idempotency_expires_at=None,
                changed_at=NOW,
            )
    with Session(engine) as db, db.begin():
        flagged = update_account_flags(
            db,
            scope=scope,
            command=UpdateAccountFlags(
                account,
                "control flag corrected",
                "user:finance",
                kind=AccountKind.CONTROL,
                posting_allowed=False,
            ),
            idempotency_key="flags:1",
            idempotency_expires_at=None,
            changed_at=NOW,
        )
        assert (flagged.kind, flagged.posting_allowed) == (AccountKind.CONTROL, False)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.account_changes "
                    "WHERE account_id=:a"
                ),
                {"a": account},
            )
            == 1
        )
    engine.dispose()


def test_downgrade_refuses_with_history_and_is_clean_without(
    database, monkeypatch
) -> None:
    from alembic import command

    _, admin_url, _ = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = _config(ACCOUNTING_VERSIONS)
    command.upgrade(config, "heads")
    command.downgrade(config, "ac_0003_draft_void")
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT to_regclass('mod_accounting.account_changes') IS NULL")
            )
            is True
        )
    command.upgrade(config, "heads")
    evidence = _seed(admin_url, posted=False)
    with engine.connect() as conn:
        account = _account_id(conn, evidence.tenant)
    with Session(engine) as db, db.begin():
        _recode(db, evidence.tenant, account, "1100", "recode:1")
    with pytest.raises(RuntimeError, match="cannot downgrade ac_0004"):
        command.downgrade(config, "ac_0003_draft_void")
    engine.dispose()
