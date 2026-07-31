"""Least-privilege canary for the PLATFORM outbox relay dispatcher (WS3 platform relay).

Proves the boundary the platform relay design fixes: `platform_outbox_dispatcher`
may ONLY `EXECUTE` the `claim_platform_outbox_batch` / `settle_platform_outbox_event`
SECURITY DEFINER functions — it has NO direct privilege on `platform_outbox_events`
or any other table, it is DISTINCT from both `platform_api` and the tenant
`outbox_dispatcher`, and it is not BYPASSRLS/superuser. Requires Postgres (0012).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def _dispatcher_url() -> str:
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set — dispatcher grant tests need Postgres")
    scheme_userhost, _, db = base.rpartition("/")
    scheme, _, userhost = scheme_userhost.partition("://")
    host = userhost.rpartition("@")[2]
    return f"{scheme}://platform_outbox_dispatcher@{host}/{db}"


@pytest.fixture(scope="module")
def dispatcher_engine() -> Generator[Engine, None, None]:
    engine = create_engine(_dispatcher_url(), future=True)
    yield engine
    engine.dispose()


def _seed_pending(admin_session: Session) -> uuid.UUID:
    eid = uuid.uuid4()
    admin_session.execute(
        text(
            "INSERT INTO platform_outbox_events (id, event_type, status) "
            "VALUES (:id, 'contract.activated', 'pending')"
        ),
        {"id": str(eid)},
    )
    admin_session.commit()
    return eid


def test_dispatcher_can_claim_and_settle_but_only_via_functions(
    admin_session: Session, dispatcher_engine: Engine
) -> None:
    eid = _seed_pending(admin_session)
    try:
        with dispatcher_engine.connect() as conn:
            claimed = conn.execute(
                text(
                    "SELECT id, status, leased_by "
                    "FROM claim_platform_outbox_batch(:w, :b, :s) WHERE id = :id"
                ),
                {"w": "w1", "b": 10, "s": 300, "id": str(eid)},
            ).fetchone()
            conn.commit()
            assert claimed is not None
            assert claimed.status == "claimed" and claimed.leased_by == "w1"

            ok = conn.execute(
                text(
                    "SELECT settle_platform_outbox_event("
                    ":id, :w, 'sent', NULL, 0, NULL)"
                ),
                {"id": str(eid), "w": "w1"},
            ).scalar()
            conn.commit()
            assert ok is True

            # A different worker cannot settle a row it doesn't hold.
            eid2 = _seed_pending(admin_session)
            conn.execute(
                text(
                    "SELECT id FROM claim_platform_outbox_batch(:w, 10, 300) "
                    "WHERE id = :id"
                ),
                {"w": "w1", "id": str(eid2)},
            ).fetchone()
            conn.commit()
            not_owner = conn.execute(
                text(
                    "SELECT settle_platform_outbox_event(:id, 'other', 'sent', "
                    "NULL, 0, NULL)"
                ),
                {"id": str(eid2)},
            ).scalar()
            conn.commit()
            assert not_owner is False
    finally:
        admin_session.execute(text("DELETE FROM platform_outbox_events"))
        admin_session.commit()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM platform_outbox_events",
        "UPDATE platform_outbox_events SET status = 'sent'",
        "SELECT count(*) FROM outbox_events",
        "SELECT count(*) FROM platform_inbox_records",
        "SELECT count(*) FROM parties",
    ],
)
def test_dispatcher_has_no_direct_table_access(
    dispatcher_engine: Engine, sql: str
) -> None:
    with dispatcher_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text(sql)).fetchone()


def test_platform_dispatcher_role_is_not_bypassrls_or_super(
    admin_session: Session,
) -> None:
    row = admin_session.execute(
        text(
            "SELECT rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname = 'platform_outbox_dispatcher'"
        )
    ).one()
    assert row.rolbypassrls is False
    assert row.rolsuper is False


def test_platform_dispatcher_cannot_execute_the_tenant_relay_functions(
    dispatcher_engine: Engine,
) -> None:
    # The two dispatchers are distinct: the platform one has NO EXECUTE on the
    # tenant relay functions.
    with dispatcher_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(
                text("SELECT id FROM claim_outbox_batch('w', 1, 300)")
            ).fetchone()
