"""Real-migration canaries for posted source content and draft concurrency.

The ac0001 sensitivity/upgrade test deliberately demonstrates the historical
reparenting hole before applying ac0002. No metadata-created schema is evidence.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from queue import Queue

import pytest
from dotmac_accounting.contracts import (
    AccountClass,
    AccountKind,
    CreateAccount,
    CreateAccountCategory,
    CreateDimension,
    CreateDimensionValue,
    CreateFiscalPeriod,
    CreateFiscalYear,
    CreateJournal,
    JournalKind,
    JournalLineInput,
    NormalBalance,
    ReverseJournal,
    SourceIdentity,
)
from dotmac_accounting.models import JournalEntry
from dotmac_accounting.service import (
    _append_ledger,
    create_account,
    create_account_category,
    create_dimension,
    create_dimension_value,
    create_fiscal_period,
    create_fiscal_year,
    create_journal,
    open_period,
    post_journal,
    reverse_journal,
)
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_accounting_payables_isolation as finance_tests
from tests import test_tax_released_migration_upgrade as tax_upgrade_tests

ACCOUNTING_VERSIONS = finance_tests.ACCOUNTING_VERSIONS
KERNEL_VERSIONS = finance_tests.KERNEL_VERSIONS
REPO_ROOT = finance_tests.REPO_ROOT
migrated_finance = finance_tests.migrated_finance
database = tax_upgrade_tests.database

NOW = datetime(2026, 8, 19, 9, tzinfo=UTC)
BASELINE = "ac_0001_accounting"
HEAD = "ac_0002_posted_content"
BASELINE_FILE = "ac_0001_accounting.py"


@dataclass(frozen=True)
class Evidence:
    tenant: uuid.UUID
    journal: uuid.UUID
    draft: uuid.UUID
    line: uuid.UUID
    draft_line: uuid.UUID
    dimension: uuid.UUID
    value: uuid.UUID
    assignment: uuid.UUID


def _context(conn: Connection, tenant: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant)},
    )


def _post(db: Session, tenant: uuid.UUID, journal: uuid.UUID) -> None:
    post_journal(
        db,
        scope=TenantScope(tenant),
        journal_id=journal,
        approval_reference="approval:test",
        posted_by="user:test",
        idempotency_key=f"post:{journal}",
        idempotency_expires_at=None,
        posted_at=NOW,
    )


def _seed(admin_url: str, *, posted: bool = True) -> Evidence:
    engine = create_engine(admin_url)
    tenant = uuid.uuid4()
    scope = TenantScope(tenant)
    with Session(engine) as db, db.begin():
        db.execute(
            text(
                "INSERT INTO public.tenants (id,slug,name) VALUES (:id,:slug,'Proof')"
            ),
            {"id": tenant, "slug": tenant.hex},
        )
        category = create_account_category(
            db,
            scope=scope,
            command=CreateAccountCategory("asset", "Assets", AccountClass.ASSET),
        )
        account = create_account(
            db,
            scope=scope,
            command=CreateAccount(
                "1000", "Cash", category.id, AccountKind.POSTING, NormalBalance.DEBIT
            ),
        )
        year = create_fiscal_year(
            db,
            scope=scope,
            command=CreateFiscalYear(
                "FY26", "FY26", date(2026, 1, 1), date(2026, 12, 31)
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
            db, scope=scope, period_id=period.id, actor_ref="user:test", occurred_at=NOW
        )
        dimension = create_dimension(
            db, scope=scope, command=CreateDimension("project", "Project")
        )
        value = create_dimension_value(
            db,
            scope=scope,
            command=CreateDimensionValue(dimension.id, "fibre", "Fibre"),
        )
        journals = []
        for number in ("source", "draft"):
            journal = create_journal(
                db,
                scope=scope,
                command=CreateJournal(
                    number=number,
                    kind=JournalKind.STANDARD,
                    entry_date=NOW.date(),
                    posting_date=NOW.date(),
                    currency_code="NGN",
                    exchange_rate=Decimal("1"),
                    description="Content proof",
                    source=SourceIdentity("test", "journal", number, "1", "a" * 64),
                    lines=(
                        JournalLineInput(
                            account.id,
                            debit=Decimal("100"),
                            dimension_value_ids=(value.id,),
                        ),
                        JournalLineInput(account.id, credit=Decimal("100")),
                    ),
                ),
                idempotency_key=f"create:{number}",
                idempotency_expires_at=None,
                recorded_at=NOW,
            )
            journals.append(journal.id)
        if posted:
            _post(db, tenant, journals[0])
        lines = [
            db.scalar(
                text(
                    "SELECT id FROM mod_accounting.journal_lines WHERE "
                    "journal_id=:j AND line_number=1"
                ),
                {"j": j},
            )
            for j in journals
        ]
        assignment = db.scalar(
            text(
                "SELECT id FROM mod_accounting.journal_line_dimensions WHERE "
                "journal_line_id=:line"
            ),
            {"line": lines[0]},
        )
        result = Evidence(
            tenant,
            journals[0],
            journals[1],
            lines[0],
            lines[1],
            dimension.id,
            value.id,
            assignment,
        )
    engine.dispose()
    return result


@pytest.mark.parametrize("role", ["owner", "app_user"])
def test_posted_children_cannot_escape_or_change(migrated_finance, role: str) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url)
    engine = create_engine(admin_url if role == "owner" else app_url)
    parameters = vars(evidence)
    statements = [
        "UPDATE mod_accounting.journal_lines SET journal_id=:draft,line_number=3 "
        "WHERE id=:line",
        "UPDATE mod_accounting.journal_lines SET debit=999,debit_functional=999 "
        "WHERE id=:line",
        "DELETE FROM mod_accounting.journal_lines WHERE id=:line",
        "INSERT INTO mod_accounting.journal_lines SELECT "
        ":new_id,tenant_id,journal_id,3,account_id,description,debit,credit,debit_f"
        "unctional,credit_functional,created_at,updated_at FROM "
        "mod_accounting.journal_lines WHERE id=:line",
        "UPDATE mod_accounting.journal_line_dimensions SET "
        "journal_line_id=:draft_line WHERE id=:assignment",
        "UPDATE mod_accounting.journal_line_dimensions SET id=:new_id WHERE "
        "id=:assignment",
        "DELETE FROM mod_accounting.journal_line_dimensions WHERE id=:assignment",
        "INSERT INTO mod_accounting.journal_line_dimensions "
        "(id,tenant_id,journal_line_id,dimension_id,dimension_value_id) VALUES "
        "(:new_id,:tenant,:line,:dimension,:value)",
        "DELETE FROM mod_accounting.journal_entries WHERE id=:journal",
    ]
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        for statement in statements:
            with (
                pytest.raises(DBAPIError, match="immutable|cannot be deleted"),
                conn.begin_nested(),
            ):
                conn.execute(text(statement), {**parameters, "new_id": uuid.uuid4()})
        assert (
            conn.scalar(
                text(
                    "SELECT journal_id FROM mod_accounting.journal_lines WHERE id=:line"
                ),
                parameters,
            )
            == evidence.journal
        )
    engine.dispose()


def test_draft_edits_reparent_and_cascade_remain_authorable(migrated_finance) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(app_url)
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        conn.execute(
            text(
                "UPDATE mod_accounting.journal_lines SET "
                "journal_id=:draft,line_number=3,description='edited' WHERE "
                "id=:line"
            ),
            vars(evidence),
        )
        conn.execute(
            text(
                "DELETE FROM mod_accounting.journal_line_dimensions WHERE "
                "journal_line_id=:draft_line"
            ),
            vars(evidence),
        )
        conn.execute(
            text(
                "UPDATE mod_accounting.journal_line_dimensions SET "
                "journal_line_id=:draft_line WHERE id=:assignment"
            ),
            vars(evidence),
        )
        conn.execute(
            text("DELETE FROM mod_accounting.journal_entries WHERE id=:draft"),
            vars(evidence),
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_accounting.journal_line_dimensions")
            )
            == 0
        )
        with pytest.raises(DBAPIError, match="parent"), conn.begin_nested():
            conn.execute(
                text(
                    "INSERT INTO mod_accounting.journal_line_dimensions "
                    "(id,tenant_id,journal_line_id,dimension_id,dimension_value_id)"
                    " VALUES (:id,:tenant,:line,:dimension,:value)"
                ),
                {**vars(evidence), "id": uuid.uuid4()},
            )
    engine.dispose()


def test_hidden_parent_refused_and_other_tenant_unchanged(migrated_finance) -> None:
    admin_url, app_url = migrated_finance
    own, other = _seed(admin_url, posted=False), _seed(admin_url)
    engine = create_engine(app_url)
    with engine.begin() as conn:
        _context(conn, own.tenant)
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.journal_entries WHERE id=:id"
                ),
                {"id": other.journal},
            )
            == 0
        )
        with pytest.raises(DBAPIError, match="parent"), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_accounting.journal_lines SET journal_id=:parent "
                    "WHERE id=:line"
                ),
                {"parent": other.journal, "line": own.line},
            )
    engine.dispose()


def test_reversal_keeps_original_account_amount_and_dimension_evidence(
    migrated_finance,
) -> None:
    admin_url, _ = migrated_finance
    evidence = _seed(admin_url)
    engine = create_engine(admin_url)
    with Session(engine) as db, db.begin():
        reversal = reverse_journal(
            db,
            scope=TenantScope(evidence.tenant),
            command=ReverseJournal(
                evidence.journal,
                "REV-1",
                NOW.date(),
                "Correction",
                "approval:reverse",
                "user:test",
            ),
            idempotency_key="reverse",
            idempotency_expires_at=None,
            reversed_at=NOW,
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.posted_ledger_lines src "
                    "JOIN mod_accounting.journal_lines sl ON "
                    "sl.id=src.journal_line_id JOIN mod_accounting.journal_lines "
                    "rl ON rl.journal_id=:reversal AND "
                    "rl.line_number=sl.line_number JOIN "
                    "mod_accounting.posted_ledger_lines rev ON "
                    "rev.journal_line_id=rl.id WHERE src.journal_id=:journal AND "
                    "src.account_id=rev.account_id AND src.debit=rev.credit AND "
                    "src.credit=rev.debit AND "
                    "src.original_debit=rev.original_credit AND "
                    "src.original_credit=rev.original_debit"
                ),
                {"journal": evidence.journal, "reversal": reversal.id},
            )
            == 2
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.posted_ledger_dimensions "
                    "src JOIN mod_accounting.posted_ledger_lines sl ON "
                    "sl.id=src.ledger_line_id JOIN "
                    "mod_accounting.posted_ledger_dimensions rev ON "
                    "rev.dimension_id=src.dimension_id AND "
                    "rev.dimension_value_id=src.dimension_value_id AND "
                    "rev.dimension_code=src.dimension_code AND "
                    "rev.value_code=src.value_code JOIN "
                    "mod_accounting.posted_ledger_lines rl ON "
                    "rl.id=rev.ledger_line_id WHERE sl.journal_id=:journal AND "
                    "rl.journal_id=:reversal"
                ),
                {"journal": evidence.journal, "reversal": reversal.id},
            )
            == 1
        )
        with pytest.raises(DBAPIError, match="immutable"), db.begin_nested():
            db.execute(
                text(
                    "UPDATE mod_accounting.journal_lines SET journal_id=:draft "
                    "WHERE id=:line"
                ),
                vars(evidence),
            )
    engine.dispose()


def _wait_blocked(conn: Connection, pid: int, blocker: int) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if conn.scalar(
            text("SELECT :blocker=ANY(pg_blocking_pids(:pid))"),
            {"pid": pid, "blocker": blocker},
        ):
            return
        time.sleep(0.02)
    pytest.fail(
        "expected competing statement to be observed waiting for its parent lock"
    )


@pytest.mark.parametrize("child", ["line", "dimension"])
@pytest.mark.parametrize("first", ["post", "edit"])
def test_post_and_content_edit_serialize_under_observed_lock(
    migrated_finance, child: str, first: str
) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(app_url)
    statement = (
        "UPDATE mod_accounting.journal_lines SET description='edited' WHERE id=:line"
        if child == "line"
        else "DELETE FROM mod_accounting.journal_line_dimensions WHERE id=:assignment"
    )
    pids: Queue[int] = Queue()

    def competing() -> str:
        with engine.connect() as conn:
            try:
                with conn.begin():
                    _context(conn, evidence.tenant)
                    conn.execute(text("SET LOCAL statement_timeout='12s'"))
                    pids.put(conn.scalar(text("SELECT pg_backend_pid()")))
                    if first == "post":
                        conn.execute(text(statement), vars(evidence))
                    else:
                        with Session(bind=conn) as db:
                            _post(db, evidence.tenant, evidence.journal)
                return "committed"
            except DBAPIError as exc:
                assert "immutable" in str(exc)
                return "refused"

    with engine.connect() as holder, engine.connect() as observer:
        transaction = holder.begin()
        _context(holder, evidence.tenant)
        holder.execute(text("SET LOCAL statement_timeout='12s'"))
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            if first == "post":
                with Session(bind=holder) as db:
                    _post(db, evidence.tenant, evidence.journal)
            else:
                holder.execute(text(statement), vars(evidence))
            future = pool.submit(competing)
            try:
                _wait_blocked(observer, pids.get(timeout=8), holder_pid)
            finally:
                transaction.commit()
            assert future.result(timeout=15) == (
                "refused" if first == "post" else "committed"
            )
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        assert (
            conn.scalar(
                text(
                    "SELECT status FROM mod_accounting.journal_entries WHERE "
                    "id=:journal"
                ),
                vars(evidence),
            )
            == "POSTED"
        )
        if child == "dimension":
            assert conn.scalar(
                text("SELECT count(*) FROM mod_accounting.posted_ledger_dimensions")
            ) == (1 if first == "post" else 0)
        else:
            assert conn.scalar(
                text(
                    "SELECT description FROM mod_accounting.posted_ledger_lines "
                    "WHERE journal_line_id=:line"
                ),
                vars(evidence),
            ) == ("edited" if first == "edit" else None)
    engine.dispose()


def test_dimension_ancestry_is_resolved_after_concurrent_line_reparent(
    migrated_finance,
) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(app_url)
    pids: Queue[int] = Queue()

    def edit_dimension() -> None:
        with engine.begin() as conn:
            _context(conn, evidence.tenant)
            conn.execute(text("SET LOCAL statement_timeout='12s'"))
            pids.put(conn.scalar(text("SELECT pg_backend_pid()")))
            with pytest.raises(DBAPIError, match="immutable"), conn.begin_nested():
                conn.execute(
                    text(
                        "DELETE FROM mod_accounting.journal_line_dimensions WHERE "
                        "id=:assignment"
                    ),
                    vars(evidence),
                )

    with engine.connect() as holder, engine.connect() as observer:
        transaction = holder.begin()
        _context(holder, evidence.tenant)
        holder.execute(
            text(
                "UPDATE mod_accounting.journal_lines SET "
                "journal_id=:draft,line_number=3 WHERE id=:line"
            ),
            vars(evidence),
        )
        holder.execute(
            text(
                "UPDATE mod_accounting.journal_entries SET status='VOID' WHERE "
                "id=:draft"
            ),
            vars(evidence),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit_dimension)
            try:
                _wait_blocked(
                    observer,
                    pids.get(timeout=8),
                    holder.scalar(text("SELECT pg_backend_pid()")),
                )
            finally:
                transaction.commit()
            future.result(timeout=15)
    engine.dispose()


def test_draft_cascade_deadlock_aborts_then_delete_can_retry(migrated_finance) -> None:
    admin_url, app_url = migrated_finance
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(app_url)
    pids: Queue[int] = Queue()

    def edit_line() -> str:
        try:
            with engine.begin() as conn:
                _context(conn, evidence.tenant)
                conn.execute(text("SET LOCAL statement_timeout='12s'"))
                pids.put(conn.scalar(text("SELECT pg_backend_pid()")))
                conn.execute(
                    text(
                        "UPDATE mod_accounting.journal_lines SET "
                        "description='racing edit' WHERE id=:line"
                    ),
                    vars(evidence),
                )
            return "committed"
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "40P01"
            return "deadlock"

    with engine.connect() as holder, engine.connect() as observer:
        transaction = holder.begin()
        _context(holder, evidence.tenant)
        holder.execute(text("SET LOCAL statement_timeout='12s'"))
        holder.execute(
            text(
                "SELECT id FROM mod_accounting.journal_entries WHERE id=:journal "
                "FOR UPDATE"
            ),
            vars(evidence),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit_line)
            try:
                _wait_blocked(
                    observer,
                    pids.get(timeout=8),
                    holder.scalar(text("SELECT pg_backend_pid()")),
                )
                holder.execute(
                    text(
                        "DELETE FROM mod_accounting.journal_entries WHERE id=:journal"
                    ),
                    vars(evidence),
                )
                transaction.commit()
                delete_result = "committed"
            except DBAPIError as exc:
                transaction.rollback()
                assert exc.orig.sqlstate == "40P01"
                delete_result = "deadlock"
            finally:
                if transaction.is_active:
                    transaction.rollback()
            assert sorted((delete_result, future.result(timeout=15))) == [
                "committed",
                "deadlock",
            ]
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        conn.execute(
            text("DELETE FROM mod_accounting.journal_entries WHERE id=:journal"),
            vars(evidence),
        )
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.journal_lines WHERE "
                    "journal_id=:journal"
                ),
                vars(evidence),
            )
            == 0
        )
    engine.dispose()


def _config(versions: Path):
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("version_locations", f"{KERNEL_VERSIONS} {versions}")
    return config


def _released_root(tmp_path: Path) -> Path:
    git = shutil.which("git")
    assert git is not None
    path = (
        "packages/dotmac-accounting/src/dotmac_accounting/migrations/versions/"
        f"{BASELINE_FILE}"
    )
    result = subprocess.run(  # noqa: S603 # nosec B603
        [git, "show", f"dotmac-accounting-v0.1.0a1:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    assert (
        hashlib.sha256(result.stdout).hexdigest()
        == "ed2aedd04df0c7446ae7a7b90d6e97d40c2f352f806b9c8eba26558521ef686e"
    )
    assert (ACCOUNTING_VERSIONS / BASELINE_FILE).read_bytes() == result.stdout
    versions = tmp_path / "released_accounting"
    versions.mkdir()
    (versions / BASELINE_FILE).write_bytes(result.stdout)
    return versions


def _snapshot(conn: Connection) -> tuple:
    return tuple(
        conn.scalar(
            text(
                "SELECT md5(string_agg(to_jsonb(row)::text, ',' ORDER BY id)) "  # noqa: S608 # nosec B608
                f"FROM mod_accounting.{table} row"
                # Closed literal table list below; no caller-supplied SQL identifiers.
            )
        )
        for table in (
            "journal_entries",
            "journal_lines",
            "journal_line_dimensions",
            "posted_ledger_lines",
            "posted_ledger_dimensions",
        )
    )


def test_real_published_predecessor_upgrade_closes_escape_without_rewriting_facts(
    database, tmp_path: Path, monkeypatch
) -> None:
    from alembic import command

    _, admin_url, app_url = database
    versions = _released_root(tmp_path)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    command.upgrade(_config(versions), "heads")
    evidence = _seed(admin_url)
    engine = create_engine(app_url)
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        before = _snapshot(conn)
        # Sensitivity: the exact shipped schema accepts this escape. Roll it
        # back so the additive upgrade is measured against unchanged facts.
        savepoint = conn.begin_nested()
        conn.execute(
            text(
                "UPDATE mod_accounting.journal_lines SET "
                "journal_id=:draft,line_number=3 WHERE id=:line"
            ),
            vars(evidence),
        )
        assert (
            conn.scalar(
                text(
                    "SELECT journal_id FROM mod_accounting.journal_lines WHERE id=:line"
                ),
                vars(evidence),
            )
            == evidence.draft
        )
        savepoint.rollback()
    shutil.copy2(ACCOUNTING_VERSIONS / "ac_0002_posted_content.py", versions)
    command.upgrade(_config(versions), "heads")
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        assert _snapshot(conn) == before
        with pytest.raises(DBAPIError, match="immutable"), conn.begin_nested():
            conn.execute(
                text(
                    "UPDATE mod_accounting.journal_lines SET "
                    "journal_id=:draft,line_number=3 WHERE id=:line"
                ),
                vars(evidence),
            )
    with pytest.raises(RuntimeError, match="cannot downgrade ac_0002"):
        command.downgrade(_config(versions), BASELINE)
    with engine.begin() as conn:
        _context(conn, evidence.tenant)
        assert _snapshot(conn) == before
        with pytest.raises(DBAPIError, match="immutable"), conn.begin_nested():
            conn.execute(
                text(
                    "DELETE FROM mod_accounting.journal_line_dimensions WHERE "
                    "id=:assignment"
                ),
                vars(evidence),
            )
    engine.dispose()


def test_empty_and_draft_only_downgrade_restore_exact_predecessor_guard(
    database, tmp_path: Path, monkeypatch
) -> None:
    from alembic import command

    _, admin_url, _ = database
    versions = _released_root(tmp_path)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = _config(versions)
    command.upgrade(config, "heads")
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        predecessor = conn.scalar(
            text(
                "SELECT "
                "pg_get_functiondef('mod_accounting.protect_journal_content()'::reg"
                "procedure)"
            )
        )
    shutil.copy2(ACCOUNTING_VERSIONS / "ac_0002_posted_content.py", versions)
    command.upgrade(config, "heads")
    command.downgrade(config, BASELINE)
    evidence = _seed(admin_url, posted=False)
    command.upgrade(config, "heads")
    command.downgrade(config, BASELINE)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT "
                    "pg_get_functiondef('mod_accounting.protect_journal_content()':"
                    ":regprocedure)"
                )
            )
            == predecessor
        )
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM mod_accounting.journal_entries WHERE "
                    "tenant_id=:tenant"
                ),
                vars(evidence),
            )
            == 2
        )
    engine.dispose()


@pytest.mark.parametrize("evidence_kind", ["void", "ledger"])
def test_downgrade_refuses_nonposted_frozen_or_appended_evidence(
    database, tmp_path: Path, monkeypatch, evidence_kind: str
) -> None:
    from alembic import command

    _, admin_url, _ = database
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = _config(ACCOUNTING_VERSIONS)
    command.upgrade(config, "heads")
    evidence = _seed(admin_url, posted=False)
    engine = create_engine(admin_url)
    if evidence_kind == "void":
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE mod_accounting.journal_entries SET status='VOID' WHERE "
                    "id=:journal"
                ),
                vars(evidence),
            )
    else:
        # Exercise the real draft-stage append phase, with no non-DRAFT row
        # for the downgrade journal-status check to hide behind.
        with Session(engine) as db, db.begin():
            journal = db.get(JournalEntry, evidence.journal)
            assert journal is not None
            _append_ledger(
                db,
                tenant_id=evidence.tenant,
                journal=journal,
                posted_by="user:test",
                posted_at=NOW,
            )
    with pytest.raises(RuntimeError, match="cannot downgrade ac_0002"):
        command.downgrade(config, BASELINE)
    engine.dispose()


def test_upgrade_waits_for_true_predecessor_writer(
    database, tmp_path: Path, monkeypatch
) -> None:
    from alembic import command

    _, admin_url, _ = database
    versions = _released_root(tmp_path)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    config = _config(versions)
    command.upgrade(config, "heads")
    evidence = _seed(admin_url, posted=False)
    shutil.copy2(ACCOUNTING_VERSIONS / "ac_0002_posted_content.py", versions)
    engine = create_engine(admin_url)
    with engine.connect() as holder, engine.connect() as observer:
        transaction = holder.begin()
        holder.execute(
            text(
                "UPDATE mod_accounting.journal_lines SET description='predecessor "
                "writer' WHERE id=:line"
            ),
            vars(evidence),
        )
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(command.upgrade, config, "heads")
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    # Activity/query snapshots otherwise persist for this
                    # observer transaction and can hide Alembic's later LOCK.
                    observer.execute(text("SELECT pg_catalog.pg_stat_clear_snapshot()"))
                    pid = observer.scalar(
                        text(
                            "SELECT pid FROM pg_stat_activity WHERE "
                            ":holder=ANY(pg_blocking_pids(pid)) AND query LIKE "
                            "'%LOCK TABLE mod_accounting.journal_entries%'"
                        ),
                        {"holder": holder_pid},
                    )
                    if pid is not None:
                        break
                    time.sleep(0.02)
                else:
                    pytest.fail(
                        "migration did not wait for the genuine predecessor writer"
                    )
            finally:
                transaction.commit()
            future.result(timeout=12)
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT description FROM mod_accounting.journal_lines WHERE "
                    "id=:line"
                ),
                vars(evidence),
            )
            == "predecessor writer"
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM alembic_version WHERE version_num=:head"),
                {"head": HEAD},
            )
            == 1
        )
    engine.dispose()


def test_migration_restores_callers_timeout_and_times_out_under_live_writer(
    database, tmp_path: Path, monkeypatch
) -> None:
    import importlib.util

    from alembic import command
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    _, admin_url, _ = database
    versions = _released_root(tmp_path)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    command.upgrade(_config(versions), "heads")
    evidence = _seed(admin_url, posted=False)
    spec = importlib.util.spec_from_file_location(
        "accounting_content_migration",
        ACCOUNTING_VERSIONS / "ac_0002_posted_content.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        with conn.begin(), Operations.context(MigrationContext.configure(conn)):
            conn.execute(text("SET LOCAL lock_timeout='9s'"))
            migration.upgrade()
            assert conn.scalar(text("SHOW lock_timeout")) == "9s"
            migration.downgrade()
            assert conn.scalar(text("SHOW lock_timeout")) == "9s"
    with engine.connect() as holder, engine.connect() as waiter:
        held = holder.begin()
        holder.execute(
            text(
                "UPDATE mod_accounting.journal_lines SET description='blocking "
                "migration' WHERE id=:line"
            ),
            vars(evidence),
        )
        original = waiter.scalar(text("SHOW lock_timeout"))
        waiter.rollback()
        try:
            with pytest.raises(DBAPIError) as caught:
                with (
                    waiter.begin(),
                    Operations.context(MigrationContext.configure(waiter)),
                ):
                    migration.upgrade()
            assert caught.value.orig.sqlstate == "55P03"
            assert waiter.scalar(text("SHOW lock_timeout")) == original
        finally:
            held.rollback()
    engine.dispose()
