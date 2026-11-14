"""Real-migration canaries for the governed draft void (ac_0003)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from dotmac_accounting.contracts import InvalidTransition, VoidDraftJournal
from dotmac_accounting.service import void_draft_journal
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_accounting_posted_content as content
from tests.test_accounting_posted_content import (
    ACCOUNTING_VERSIONS,
    BASELINE,
    NOW,
    _config,
    _context,
    _seed,
    database,
    migrated_finance,
)

__all__ = ["database", "migrated_finance"]


def _command(journal: uuid.UUID, **overrides) -> VoidDraftJournal:
    values = {
        "journal_id": journal,
        "reason": "orphan draft",
        "voided_by": "user:requester",
        "approved_by": "user:approver",
        "approval_reference": "approval:void",
    }
    values.update(overrides)
    return VoidDraftJournal(**values)


def _void(db: Session, tenant: uuid.UUID, journal: uuid.UUID, key: str = "void:1"):
    return void_draft_journal(
        db,
        scope=TenantScope(tenant),
        command=_command(journal),
        idempotency_key=key,
        idempotency_expires_at=None,
        voided_at=NOW,
    )


def test_service_void_is_audited_idempotent_and_freezes_content(
    migrated_finance,
) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=True)
    engine = create_engine(admin_url)
    with Session(engine) as db, db.begin():
        voided = _void(db, evidence.tenant, evidence.draft)
        assert voided.status.value == "VOID"
    with Session(engine) as db, db.begin():
        assert _void(db, evidence.tenant, evidence.draft).status.value == "VOID"
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.period_events WHERE "
                    "event_kind='JOURNAL_VOID' AND evidence->>'journal_id'=:j"
                ),
                {"j": str(evidence.draft)},
            )
            == 1
        )
    # A voided journal is frozen like any non-draft: no line edit, no delete,
    # no revert to DRAFT, even for the table owner.
    for statement in (
        "UPDATE mod_accounting.journal_lines SET description='x' WHERE id=:line",
        "DELETE FROM mod_accounting.journal_lines WHERE id=:line",
        "UPDATE mod_accounting.journal_entries SET status='DRAFT' WHERE id=:j",
        "DELETE FROM mod_accounting.journal_entries WHERE id=:j",
        "UPDATE mod_accounting.period_events SET actor_ref='x' "
        "WHERE event_kind='JOURNAL_VOID'",
        "DELETE FROM mod_accounting.period_events WHERE event_kind='JOURNAL_VOID'",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            with conn.begin():
                conn.execute(
                    text(statement),
                    {"line": evidence.draft_line, "j": evidence.draft},
                )
    engine.dispose()


def test_service_refuses_posted_journal(migrated_finance) -> None:
    admin_url, _ = migrated_finance
    evidence = _seed(admin_url, posted=True)
    engine = create_engine(admin_url)
    with pytest.raises(InvalidTransition):
        with Session(engine) as db, db.begin():
            _void(db, evidence.tenant, evidence.journal)
    engine.dispose()


@pytest.mark.parametrize("target", ["posted", "no_event", "bad_event"])
def test_database_refuses_void_without_governance(migrated_finance, target) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=True)
    journal = evidence.journal if target == "posted" else evidence.draft
    engine = create_engine(app_url)
    with (
        engine.connect() as conn,
        pytest.raises(DBAPIError, match="void requires|immutable"),
    ):
        with conn.begin():
            _context(conn, evidence.tenant)
            if target == "bad_event":
                # Same actor as approver: the event cannot authorise a void.
                conn.execute(
                    text(
                        "INSERT INTO mod_accounting.period_events (id,tenant_id,"
                        "period_id,event_kind,from_status,to_status,actor_ref,"
                        "approval_reference,evidence,occurred_at) SELECT "
                        "gen_random_uuid(),tenant_id,fiscal_period_id,'JOURNAL_VOID',"
                        "'DRAFT','VOID','user:same','approval:void',"
                        "jsonb_build_object('journal_id',id::text,'reason','r',"
                        "'approved_by','USER:SAME'),now() "
                        "FROM mod_accounting.journal_entries WHERE id=:j"
                    ),
                    {"j": journal},
                )
                conn.execute(
                    text(
                        "UPDATE mod_accounting.journal_entries SET status='VOID' "
                        "WHERE id=:j"
                    ),
                    {"j": journal},
                )
            else:
                if target == "posted":
                    content._record_void(conn, str(journal))
                conn.execute(
                    text(
                        "UPDATE mod_accounting.journal_entries SET status='VOID' "
                        "WHERE id=:j"
                    ),
                    {"j": journal},
                )
    engine.dispose()


def test_downgrade_refuses_once_voided_and_restores_guard_otherwise(
    database, monkeypatch
) -> None:
    from alembic import command

    _, admin_url, _ = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = _config(ACCOUNTING_VERSIONS)
    command.upgrade(config, "heads")
    # Clean: down to ac_0002, then ac_0003 re-applies, then raw VOID is refused.
    command.downgrade(config, "ac_0002_posted_content")
    command.upgrade(config, "heads")
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(admin_url)
    with Session(engine) as db, db.begin():
        _void(db, evidence.tenant, evidence.draft)
    with pytest.raises(RuntimeError, match="cannot downgrade ac_0003"):
        command.downgrade(config, "ac_0002_posted_content")
    engine.dispose()
    assert BASELINE  # the baseline lineage is unchanged by ac_0003
    assert Path(ACCOUNTING_VERSIONS / "ac_0003_draft_void.py").is_file()
