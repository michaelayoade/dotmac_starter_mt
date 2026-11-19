"""K-6 grant canary: `communication_deliveries` is append-only for `app_user`.

Connects as `app_user` (`TEST_DATABASE_URL`) and proves UPDATE and DELETE are
refused by the privilege system (not by RLS, not by a trigger) while INSERT and
SELECT still work. Also pins the migration's up/down/up on a scratch database.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

# ruff: noqa: S608  (SQL interpolates only the module-constant table name)

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.test_migration_split_rehearsals import (
    _both_locations,
    _downgrade,
    _upgrade,
    _versions,
    scratch_db,  # noqa: F401  (fixture)
)

TABLE = "communication_deliveries"
REVISION = "0033_receipts_append_only"
PREVIOUS = "0032_communication_dispatches"


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


def _as_tenant(engine: Engine, tenant_id: uuid.UUID) -> Session:
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    return session


def _insert(session: Session, tenant_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            f"INSERT INTO {TABLE} (id, tenant_id, dispatch_id, channel, address, "
            "provider, provider_message_id, status) VALUES (:id, :t, :d, 'email', "
            "'jane@example.com', 'smtp', 'm-1', 'accepted')"
        ),
        {"id": str(row_id), "t": str(tenant_id), "d": str(uuid.uuid4())},
    )
    return row_id


def _privileges(session: Session) -> dict[str, bool]:
    assert session.execute(text("SELECT current_user")).scalar() == "app_user"
    return {
        priv: bool(
            session.execute(
                text("SELECT has_table_privilege(current_user, :t, :p)"),
                {"t": TABLE, "p": priv},
            ).scalar()
        )
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def test_app_user_can_insert_and_select_but_not_update_or_delete(
    tenant_a, app_engine: Engine
) -> None:
    db = _as_tenant(app_engine, tenant_a.id)
    try:
        assert _privileges(db) == {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": False,
            "DELETE": False,
        }
        row_id = _insert(db, tenant_a.id)
        db.commit()
    finally:
        db.close()

    # SELECT still works under the tenant context.
    db = _as_tenant(app_engine, tenant_a.id)
    try:
        assert (
            db.execute(
                text(f"SELECT status FROM {TABLE} WHERE id = :id"), {"id": str(row_id)}
            ).scalar()
            == "accepted"
        )
    finally:
        db.close()

    for statement in (
        f"UPDATE {TABLE} SET status = 'failed' WHERE id = :id",
        f"UPDATE {TABLE} SET response_body = 'x'",
        f"DELETE FROM {TABLE} WHERE id = :id",
        f"DELETE FROM {TABLE}",
    ):
        db = _as_tenant(app_engine, tenant_a.id)
        try:
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement), {"id": str(row_id)})
        finally:
            db.rollback()
            db.close()


def test_the_row_survives_the_refused_writes(
    tenant_a, app_engine: Engine, admin_session: Session
) -> None:
    db = _as_tenant(app_engine, tenant_a.id)
    try:
        row_id = _insert(db, tenant_a.id)
        db.commit()
    finally:
        db.close()
    db = _as_tenant(app_engine, tenant_a.id)
    try:
        with pytest.raises(DBAPIError):
            db.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": str(row_id)})
    finally:
        db.rollback()
        db.close()
    assert (
        admin_session.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE id = :id"), {"id": str(row_id)}
        ).scalar()
        == 1
    )


def test_tenant_deletion_still_cascades(app_engine, admin_session: Session) -> None:
    """The FK cascade runs with the owner's rights, not `app_user`'s grants."""
    tenant = uuid.uuid4()
    admin_session.execute(
        text("INSERT INTO tenants (id, slug, name) VALUES (:i, :s, 'T')"),
        {"i": str(tenant), "s": f"t{tenant.hex[:8]}"},
    )
    admin_session.commit()
    db = _as_tenant(app_engine, tenant)
    try:
        _insert(db, tenant)
        db.commit()
    finally:
        db.close()
    admin_session.execute(
        text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant)}
    )
    admin_session.commit()
    assert (
        admin_session.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE tenant_id = :t"),
            {"t": str(tenant)},
        ).scalar()
        == 0
    )


def test_migration_up_down_up_restores_and_removes_the_grants(
    scratch_db: str,  # noqa: F811
) -> None:
    _upgrade(scratch_db, "heads")
    assert REVISION in _versions(scratch_db)
    eng = create_engine(scratch_db)
    sql = text(
        "SELECT has_table_privilege('app_user', :t, 'UPDATE'), "
        "has_table_privilege('app_user', :t, 'DELETE'), "
        "has_table_privilege('app_user', :t, 'INSERT'), "
        "has_table_privilege('app_user', :t, 'SELECT')"
    )
    try:
        with eng.connect() as conn:
            assert tuple(conn.execute(sql, {"t": TABLE}).one()) == (
                False,
                False,
                True,
                True,
            )
        _downgrade(scratch_db, PREVIOUS, version_locations=_both_locations())
        assert REVISION not in _versions(scratch_db)
        with eng.connect() as conn:
            # The exact 0020 grant is back.
            assert tuple(conn.execute(sql, {"t": TABLE}).one()) == (
                True,
                True,
                True,
                True,
            )
        _upgrade(scratch_db, "heads")
        with eng.connect() as conn:
            assert tuple(conn.execute(sql, {"t": TABLE}).one()) == (
                False,
                False,
                True,
                True,
            )
    finally:
        eng.dispose()
