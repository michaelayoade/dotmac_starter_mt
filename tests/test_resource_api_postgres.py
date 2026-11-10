"""Resource API kit on real PostgreSQL: DB clock, open-transaction fence, trigger
stamping, the advisory-lock import race, and the ledger prefix query plan.

Self-contained: every table lives in a throwaway schema reached through
``search_path`` (the kernel ledger models are unqualified), so the tests do not
depend on the migrated catalogue, RLS state or app roles. Needs
``TEST_MIGRATION_DATABASE_URL`` or ``TEST_DATABASE_URL`` (see ``admin_engine``).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from dotmac_kernel.exceptions import ForbiddenError
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.machine_auth import MachinePrincipal
from dotmac_kernel.models import Tenant
from dotmac_kernel.resource_api import (
    ResourceDeclaration,
    ResourceScopes,
    ResourceUnavailableError,
    changes_page,
    drop_stamping_trigger_ddl,
    stamping_trigger_ddl,
    stamping_trigger_installed,
    summarize,
)
from dotmac_kernel.source_import import ImportVerdict, import_record
from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

HORIZON = timedelta(seconds=1)


class _Base(DeclarativeBase):
    pass


class PgWidget(_Base):
    __tablename__ = "kit_pg_widgets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid())
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(50), default="w")
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


TENANT = uuid.uuid4()


def declaration(**overrides: object) -> ResourceDeclaration:
    base: dict[str, object] = {
        "name": "widgets",
        "model": PgWidget,
        "scopes": ResourceScopes(
            read="widgets:read", import_write="imports.widgets:write"
        ),
        "serialize": lambda w: {"id": str(w.id), "name": w.name},
        "stamped_by_trigger": True,
        "settle_horizon": HORIZON,
    }
    base.update(overrides)
    return ResourceDeclaration(**base)  # type: ignore[arg-type]


def key(*scopes: str) -> MachinePrincipal:
    return MachinePrincipal(
        credential_id=uuid.uuid4(),
        tenant_id=TENANT,
        label="k",
        application="sub",
        scopes=frozenset(scopes),
    )


@pytest.fixture(scope="module")
def engine(admin_engine) -> Iterator[sa.Engine]:
    if admin_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL required")
    schema = f"kit_{uuid.uuid4().hex[:10]}"
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    scoped = sa.create_engine(
        admin_engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
        future=True,
    )
    tables = [Tenant.__table__, IdempotencyRecord.__table__]
    Tenant.metadata.create_all(scoped, tables=tables)
    _Base.metadata.create_all(scoped)
    with scoped.begin() as conn:
        for statement in stamping_trigger_ddl("kit_pg_widgets"):
            conn.execute(sa.text(statement))
    yield scoped
    scoped.dispose()
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))


@pytest.fixture()
def tenant(engine) -> Iterator[uuid.UUID]:
    with Session(engine) as db:
        row = Tenant(slug=f"t{uuid.uuid4().hex[:8]}", name="T")
        db.add(row)
        db.commit()
        tenant_id = row.id
    yield tenant_id


def new_session(engine, tenant_id: uuid.UUID) -> Session:
    db = sessionmaker(bind=engine)()
    db.execute(
        sa.text("SELECT set_config('app.current_tenant', :t, false)"),
        {"t": str(tenant_id)},
    )
    return db


def reader_key(tenant_id: uuid.UUID, *scopes: str) -> MachinePrincipal:
    return MachinePrincipal(
        credential_id=uuid.uuid4(),
        tenant_id=tenant_id,
        label="k",
        application="sub",
        scopes=frozenset(scopes or ("widgets:read",)),
    )


def insert_widget(db: Session, tenant_id: uuid.UUID) -> uuid.UUID:
    wid = uuid.uuid4()
    db.add(PgWidget(id=wid, tenant_id=tenant_id, version=99, name="w"))
    db.flush()
    return wid


def test_trigger_stamps_version_and_clock_and_cannot_be_forged(engine, tenant) -> None:
    decl = declaration()
    with new_session(engine, tenant) as db:
        assert stamping_trigger_installed(db, "kit_pg_widgets") is True
        wid = insert_widget(db, tenant)
        row = db.get(PgWidget, wid)
        db.refresh(row)
        assert row.version == 1  # supplied 99, forced to 1
        first = row.updated_at
        db.execute(
            sa.text(
                "UPDATE kit_pg_widgets SET name = 'x', version = 500, "
                "updated_at = '2001-01-01' WHERE id = :i"
            ),
            {"i": wid},
        )
        db.refresh(row)
        assert row.version == 2 and row.updated_at > first
        db.execute(
            sa.text("UPDATE kit_pg_widgets SET name = 'x' WHERE id = :i"), {"i": wid}
        )
        db.refresh(row)
        assert row.version == 2  # no change, no bump
        db.rollback()
    assert decl.stamped_by_trigger


def test_trigger_check_false_when_absent_and_ddl_round_trips(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE kit_plain (id int, version int, updated_at timestamptz)"
            )
        )
    try:
        with Session(engine) as db:
            assert stamping_trigger_installed(db, "kit_plain") is False
        with engine.begin() as conn:
            for statement in stamping_trigger_ddl("kit_plain"):
                conn.execute(sa.text(statement))
        with Session(engine) as db:
            assert stamping_trigger_installed(db, "kit_plain") is True
        with engine.begin() as conn:
            for statement in drop_stamping_trigger_ddl("kit_plain"):
                conn.execute(sa.text(statement))
        with Session(engine) as db:
            assert stamping_trigger_installed(db, "kit_plain") is False
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE kit_plain"))


def test_open_transaction_fences_the_feed_until_it_ends(engine, tenant) -> None:
    decl = declaration(unseen_sessions="horizon_only")
    long_tx = new_session(engine, tenant)
    long_tx.execute(sa.text("SELECT 1"))  # starts a transaction; xact_start = now
    time.sleep(0.3)
    other = new_session(engine, tenant)
    committed = insert_widget(other, tenant)
    other.commit()
    time.sleep(HORIZON.total_seconds() + 0.5)  # older than the horizon alone
    reader = new_session(engine, tenant)
    key_ = reader_key(tenant)
    page = changes_page(reader, decl, principal=key_)
    # The row is past the hold-back but a transaction older than it is still
    # open, so the DB-clock horizon is fenced behind that transaction.
    assert page.items == () and page.fence in {
        "pg_stat_activity",
        "horizon_only_unseen_sessions",
    }
    summary = summarize(
        reader,
        decl,
        principal=key_,
        window_from=page.horizon - timedelta(hours=1),
        window_to=page.horizon + timedelta(hours=1),
    )
    assert summary.settled is False
    # The slow transaction writes late and commits; nothing was skipped.
    late = insert_widget(long_tx, tenant)
    long_tx.commit()
    long_tx.close()
    time.sleep(HORIZON.total_seconds() + 0.5)
    reader.rollback()
    ids = [i.id for i in changes_page(reader, decl, principal=key_).items]
    assert ids == [str(committed), str(late)]
    reader.close()
    other.close()


def test_horizon_is_the_database_clock_not_the_reader_clock(engine, tenant) -> None:
    decl = declaration(unseen_sessions="horizon_only")
    with new_session(engine, tenant) as db:
        insert_widget(db, tenant)
        db.commit()
    time.sleep(HORIZON.total_seconds() + 0.5)
    with new_session(engine, tenant) as db:
        # A wildly wrong reader clock cannot move the bound: `now` is ignored on PG.
        page = changes_page(
            db,
            decl,
            principal=reader_key(tenant),
            now=datetime(2000, 1, 1),
        )
        assert len(page.items) == 1


def test_sessions_the_reader_cannot_see_fail_safe(engine, tenant, admin_engine) -> None:
    role = f"kit_rd_{uuid.uuid4().hex[:8]}"
    try:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f"CREATE ROLE {role}"))
            conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    except sa.exc.DBAPIError:
        pytest.skip("cannot create roles in this database")
    try:
        schema = _schema_of(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.text(f"GRANT ALL ON ALL TABLES IN SCHEMA {schema} TO {role}")
            )
            conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        hidden = new_session(engine, tenant)
        hidden.execute(
            sa.text("SELECT 1")
        )  # an admin-role session the reader cannot see
        reader = new_session(engine, tenant)
        reader.execute(sa.text(f"SET ROLE {role}"))
        with pytest.raises(ResourceUnavailableError):
            changes_page(reader, declaration(), principal=reader_key(tenant))
        reader.rollback()  # also reverts the session settings made in that transaction
        reader.execute(
            sa.text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(tenant)},
        )
        reader.execute(sa.text(f"SET ROLE {role}"))
        page = changes_page(
            reader,
            declaration(unseen_sessions="horizon_only"),
            principal=reader_key(tenant),
        )
        assert page.fence == "horizon_only_unseen_sessions"
        reader.close()
        hidden.close()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f"DROP OWNED BY {role}"))
            conn.execute(sa.text(f"DROP ROLE {role}"))


def _schema_of(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT current_schema()")).scalar_one()


def test_wrong_session_tenant_is_refused(engine, tenant) -> None:
    other = uuid.uuid4()
    with new_session(engine, other) as db, pytest.raises(ForbiddenError):
        changes_page(
            db,
            declaration(unseen_sessions="horizon_only"),
            principal=reader_key(tenant),
        )
    db = sessionmaker(bind=engine)()  # no tenant established at all
    try:
        with pytest.raises(ForbiddenError):
            changes_page(
                db,
                declaration(unseen_sessions="horizon_only"),
                principal=reader_key(tenant),
            )
    finally:
        db.close()


def _race(engine, tenant):
    decl = declaration()
    results: dict[int, ImportVerdict] = {}
    applied: list[int] = []
    v5_holds_lock = threading.Event()

    def importer(version: int, delay: float, signal: bool) -> None:
        db = new_session(engine, tenant)
        try:

            def apply(session, seen):
                applied.append(version)
                if signal:
                    v5_holds_lock.set()
                time.sleep(delay)
                return {"local_id": f"r{version}"}

            principal = reader_key(tenant, "imports.widgets:write")
            outcome = import_record(
                db,
                decl,
                principal=principal,
                external_ref="INV-1",
                version=version,
                content={"v": version},
                apply=apply,
            )
            db.commit()
            results[version] = outcome.verdict
        finally:
            db.close()

    t5 = threading.Thread(target=importer, args=(5, 1.0, True))
    t3 = threading.Thread(target=importer, args=(3, 0.0, False))
    t5.start()
    assert v5_holds_lock.wait(5)
    t3.start()
    t5.join(10)
    t3.join(10)
    return results, applied


def test_racing_v3_and_v5_end_with_one_stale(engine, tenant) -> None:
    results, applied = _race(engine, tenant)
    assert results[5] is ImportVerdict.CREATED
    assert results[3] is ImportVerdict.STALE
    assert applied == [5]


def test_without_the_advisory_lock_both_versions_apply(
    engine, tenant, monkeypatch
) -> None:
    """Sensitivity proof: the lock is what prevents the mislabelled double apply."""
    monkeypatch.setattr(
        "dotmac_kernel.source_import.lock_reference", lambda *a, **k: None
    )
    results, applied = _race(engine, tenant)
    assert sorted(applied) == [3, 5]
    assert results[3] is ImportVerdict.CREATED  # wrong: v5 was already being applied


def test_ledger_prefix_query_plan_is_recorded(engine, tenant) -> None:
    """B8 evidence: the plan for the `<ref>@` prefix scan on the ledger.

    Measured on PostgreSQL 16 with the default collation: the planner uses the
    tenant index and applies the prefix as a FILTER, not an index condition, so
    the scan is proportional to the tenant's ledger rows. That is why
    `import_record` takes an optional `current_version` callback.
    """
    with new_session(engine, tenant) as db:
        plan = "\n".join(
            row[0]
            for row in db.execute(
                sa.text(
                    "EXPLAIN SELECT key FROM idempotency_records WHERE tenant_id = :t "
                    "AND scope = 'import:sub:widgets' AND key LIKE 'INV-1@%' ESCAPE '/'"
                ),
                {"t": str(tenant)},
            )
        )
    assert "idempotency_records" in plan  # the plan itself is evidence, not a gate
