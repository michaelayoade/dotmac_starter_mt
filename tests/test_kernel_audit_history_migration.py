"""Hosted PostgreSQL proofs for the additive audit-history shape prerequisite.

Each test applies the complete canonical Kernel chain as app_admin in the
split-rehearsal scratch database. No reconstructed baseline or ORM create_all.
This does not implement a product history importer or claim lineage adoption.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_migration_split_rehearsals as split_rehearsals
from tests.conftest import TEST_HOST_APPLICATION
from tests.test_migration_split_rehearsals import (
    _downgrade,
    _kernel_only_locations,
    _upgrade,
    _versions,
)

# Re-export the real fixture through a used module alias. Test parameters can
# request it without shadowing an unused fixture import (Ruff F811).
scratch_db = split_rehearsals.scratch_db

PREVIOUS = "0028_machine_attribution"
CURRENT = "0029_audit_history_shape"
KNOWN_CREATED = datetime(2025, 3, 4, 5, 6, 7, tzinfo=UTC)
KNOWN_OCCURRED = datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC)


def _migrate(url: str, target: str) -> None:
    _upgrade(url, target, version_locations=_kernel_only_locations())


def _retreat(url: str) -> None:
    _downgrade(url, PREVIOUS, version_locations=_kernel_only_locations())


def _rows(conn):
    return (
        conn.execute(sa.text("SELECT * FROM public.audit_events ORDER BY id"))
        .mappings()
        .all()
    )


def _shape(conn):
    return conn.execute(
        sa.text(
            "SELECT column_name, is_nullable, character_maximum_length, column_default "
            "FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='audit_events' "
            "AND column_name IN ('created_at', 'entity_type') ORDER BY column_name"
        )
    ).all()


def _security(conn):
    table = conn.execute(
        sa.text(
            "SELECT relrowsecurity, relforcerowsecurity, relacl::text "
            "FROM pg_class WHERE oid='public.audit_events'::regclass"
        )
    ).one()
    policies = conn.execute(
        sa.text(
            "SELECT policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname='public' "
            "AND tablename='audit_events' ORDER BY policyname"
        )
    ).all()
    return table, policies


def _seed(conn, tenant_id=None, **overrides):
    tenant_id = tenant_id or uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO public.tenants (id, slug, name) "
            "VALUES (:id, :slug, 'History') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "slug": f"history-{tenant_id}"},
    )
    table = sa.Table("audit_events", sa.MetaData(), schema="public", autoload_with=conn)
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "action": "legacy.undeclared.history",
        "entity_type": "legacy.entity",
        "entity_id": "historical-id",
        "actor_type": "service",
        "actor_id": "historical-service",
        "actor_label": "Historical service",
        "actor_party_id": uuid4(),
        "source_application": "dotmac_sub",
        "request_id": "historical-request",
        "status_code": 207,
        "is_success": False,
        "ip_address": "192.0.2.1",
        "user_agent": "history-fixture",
        "details": {"original": [1, "preserved"]},
        "created_at": KNOWN_CREATED,
        "occurred_at": KNOWN_OCCURRED,
        **overrides,
    }
    # Core INSERT explicitly binds None; ORM server-default omission must not
    # turn an unknowable historical timestamp into a plausible current time.
    conn.execute(table.insert().values(**values))
    return values


def test_upgrade_preserves_rows_security_and_historical_unknowns(scratch_db: str):
    _migrate(scratch_db, PREVIOUS)
    engine = sa.create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            assert (
                conn.execute(sa.text("SELECT current_user")).scalar_one() == "app_admin"
            )
            _seed(conn)
            before_rows = _rows(conn)
            before_shape = _shape(conn)
            before_security = _security(conn)
        _migrate(scratch_db, CURRENT)
        assert _versions(scratch_db) == {CURRENT}
        with engine.begin() as conn:
            assert _rows(conn) == before_rows
            assert _security(conn) == before_security
            shape = _shape(conn)
            assert shape[0] == ("created_at", "YES", None, before_shape[0][3])
            assert shape[0][3] == "now()"
            assert shape[1] == ("entity_type", "NO", 160, before_shape[1][3])
            for width in (121, 160):
                values = _seed(
                    conn, entity_type="e" * width, created_at=None, occurred_at=None
                )
                stored = (
                    conn.execute(
                        sa.text("SELECT * FROM public.audit_events WHERE id=:id"),
                        {"id": values["id"]},
                    )
                    .mappings()
                    .one()
                )
                assert dict(stored) == values
            # Known historical persistence/domain instants remain distinct.
            values = _seed(conn, entity_type="e" * 160)
            stored = (
                conn.execute(
                    sa.text("SELECT * FROM public.audit_events WHERE id=:id"),
                    {"id": values["id"]},
                )
                .mappings()
                .one()
            )
            assert dict(stored) == values
            table = sa.Table(
                "audit_events", sa.MetaData(), schema="public", autoload_with=conn
            )
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(
                    table.insert().values(
                        **{**values, "id": uuid4(), "entity_type": "e" * 161}
                    )
                )
    finally:
        engine.dispose()


def test_online_app_user_keeps_tenant_isolation_and_append_only_acl(scratch_db: str):
    _migrate(scratch_db, CURRENT)
    admin = sa.create_engine(scratch_db)
    try:
        with admin.begin() as conn:
            own = _seed(conn, created_at=None, entity_type="e" * 160)
            other = _seed(conn, created_at=None)
        online = sa.create_engine(
            sa.engine.make_url(scratch_db).set(username="app_user", password=None)
        )
        try:
            with online.begin() as conn:
                assert (
                    conn.execute(sa.text("SELECT current_user")).scalar_one()
                    == "app_user"
                )
                conn.execute(
                    sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                    {"tid": str(own["tenant_id"])},
                )
                assert [row["id"] for row in _rows(conn)] == [own["id"]]
                table = sa.Table(
                    "audit_events", sa.MetaData(), schema="public", autoload_with=conn
                )
                own_insert = {**own, "id": uuid4()}
                conn.execute(table.insert().values(**own_insert))
                with pytest.raises(DBAPIError), conn.begin_nested():
                    conn.execute(table.insert().values(**{**other, "id": uuid4()}))
                for statement in (
                    "UPDATE public.audit_events SET entity_type='changed'",
                    "DELETE FROM public.audit_events",
                ):
                    with pytest.raises(DBAPIError), conn.begin_nested():
                        conn.execute(sa.text(statement))
        finally:
            online.dispose()
    finally:
        admin.dispose()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"created_at": None}, "created_at.*NULL"),
        ({"entity_type": "e" * 121}, "entity_type.*120"),
    ],
)
def test_downgrade_refuses_loss_without_modifying_anything(
    scratch_db: str, overrides, reason
):
    _migrate(scratch_db, CURRENT)
    engine = sa.create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            _seed(conn, **overrides)
            before = _rows(conn), _shape(conn), _security(conn)
        with pytest.raises(DBAPIError, match=reason):
            _retreat(scratch_db)
        assert _versions(scratch_db) == {CURRENT}
        with engine.connect() as conn:
            assert (_rows(conn), _shape(conn), _security(conn)) == before
    finally:
        engine.dispose()


def test_safe_downgrade_restores_previous_shape_without_rewriting_rows(
    scratch_db: str,
):
    _migrate(scratch_db, PREVIOUS)
    engine = sa.create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            _seed(conn, entity_type="e" * 120)
            before = _rows(conn), _shape(conn), _security(conn)
        _migrate(scratch_db, CURRENT)
        _retreat(scratch_db)
        assert _versions(scratch_db) == {PREVIOUS}
        with engine.connect() as conn:
            assert (_rows(conn), _shape(conn), _security(conn)) == before
    finally:
        engine.dispose()


def test_ordinary_writer_keeps_gates_and_server_assigned_time(
    scratch_db: str, monkeypatch
):
    from dotmac_kernel import audit_actions
    from dotmac_kernel.audit import (
        MissingAuditActorError,
        UnattributedAuditEventError,
        write_audit_event,
    )
    from dotmac_kernel.audit_actions import (
        AuditActionRegistry,
        UndeclaredAuditActionError,
    )
    from dotmac_kernel.source_applications import (
        clear_host_application,
        install_host_application,
    )

    _migrate(scratch_db, CURRENT)
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        AuditActionRegistry((("history_test", "history_test.write"),)),
    )
    assert "created_at" not in inspect.signature(write_audit_event).parameters
    engine = sa.create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            seed = _seed(conn)
            lower = conn.execute(sa.text("SELECT now()")).scalar_one()
            with Session(bind=conn) as db:
                params = {
                    "tenant_id": seed["tenant_id"],
                    "actor_type": "service",
                    "actor_id": "current-service",
                    "action": "history_test.write",
                    "entity_type": "e" * 160,
                }
                for changes, error in (
                    (
                        {"action": "legacy.undeclared.history"},
                        UndeclaredAuditActionError,
                    ),
                    ({"actor_type": None}, MissingAuditActorError),
                    ({"actor_id": None}, MissingAuditActorError),
                ):
                    with pytest.raises(error):
                        write_audit_event(db, **{**params, **changes})
                clear_host_application()
                with pytest.raises(UnattributedAuditEventError):
                    write_audit_event(db, **params)
                install_host_application(TEST_HOST_APPLICATION)
                event = write_audit_event(db, **params, occurred_at=KNOWN_OCCURRED)
                assert event.created_at == lower
                assert event.occurred_at == KNOWN_OCCURRED
                assert event.source_application == TEST_HOST_APPLICATION
                assert event.actor_type == "service"
                assert event.actor_id == "current-service"
                assert len(_rows(conn)) == 2
    finally:
        engine.dispose()
