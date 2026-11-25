"""The event-type-filtered outbox claim (review R2 F1) on real PostgreSQL.

Foreign events must never be leased by a consumer that names its own types, in
either claim arm (pending-and-due or stale-lease reclaim), and the one new grant
(`0036_typed_claim_grant`) must reach `outbox_dispatcher` only.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.messaging.relay import RelayPolicy, claim_batch
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

SIG = "public.claim_outbox_batch_by_type(text, integer, integer, text[])"


def _dispatcher_url() -> str:
    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set — these tests need Postgres")
    scheme_userhost, _, db = base.rpartition("/")
    scheme, _, userhost = scheme_userhost.partition("://")
    host = userhost.rpartition("@")[2]
    return f"{scheme}://outbox_dispatcher@{host}/{db}"


@pytest.fixture(scope="module")
def dispatcher_engine() -> Generator[Engine, None, None]:
    engine = create_engine(_dispatcher_url(), future=True)
    yield engine
    engine.dispose()


def _seed(
    admin_session: Session, tenant_id, event_type: str, *, stale=False
) -> uuid.UUID:
    eid = uuid.uuid4()
    if stale:
        admin_session.execute(
            text(
                "INSERT INTO outbox_events (id, tenant_id, event_type, status, "
                "leased_by, leased_at) VALUES (:id, :t, :e, 'claimed', 'crashed', "
                "now() - interval '1 hour')"
            ),
            {"id": str(eid), "t": str(tenant_id), "e": event_type},
        )
    else:
        admin_session.execute(
            text(
                "INSERT INTO outbox_events (id, tenant_id, event_type, status) "
                "VALUES (:id, :t, :e, 'pending')"
            ),
            {"id": str(eid), "t": str(tenant_id), "e": event_type},
        )
    admin_session.commit()
    return eid


def _status(admin_session: Session, eid: uuid.UUID) -> tuple[str, str | None]:
    admin_session.rollback()
    row = admin_session.execute(
        text("SELECT status, leased_by FROM outbox_events WHERE id = :i"),
        {"i": str(eid)},
    ).one()
    return row.status, row.leased_by


@pytest.fixture
def cleanup(admin_session: Session, tenant_a):
    yield
    admin_session.rollback()
    admin_session.execute(
        text("DELETE FROM outbox_events WHERE tenant_id = :t"), {"t": str(tenant_a.id)}
    )
    admin_session.commit()


def test_foreign_events_are_never_claimed_in_either_arm(
    admin_session: Session, tenant_a, dispatcher_engine: Engine, cleanup
) -> None:
    mine = f"typed.mine.{uuid.uuid4().hex}"
    foreign = f"typed.foreign.{uuid.uuid4().hex}"
    own_pending = _seed(admin_session, tenant_a.id, mine)
    own_stale = _seed(admin_session, tenant_a.id, mine, stale=True)
    foreign_pending = _seed(admin_session, tenant_a.id, foreign)
    foreign_stale = _seed(admin_session, tenant_a.id, foreign, stale=True)

    with Session(dispatcher_engine) as db:
        claimed = claim_batch(
            db,
            worker_id="typed-w1",
            policy=RelayPolicy(batch_size=50),
            event_types=(mine,),
        )
        db.commit()

    assert {c.id for c in claimed} == {own_pending, own_stale}
    assert {c.event_type for c in claimed} == {mine}
    assert _status(admin_session, own_pending) == ("claimed", "typed-w1")
    assert _status(admin_session, own_stale) == ("claimed", "typed-w1")
    assert _status(admin_session, foreign_pending) == ("pending", None)
    # A stale foreign lease is not reclaimed either: still the crashed worker's.
    assert _status(admin_session, foreign_stale) == ("claimed", "crashed")


def test_several_types_and_the_unfiltered_claim_is_unchanged(
    admin_session: Session, tenant_a, dispatcher_engine: Engine, cleanup
) -> None:
    a, b, c = (f"typed.{n}.{uuid.uuid4().hex}" for n in "abc")
    ids = {t: _seed(admin_session, tenant_a.id, t) for t in (a, b, c)}
    with Session(dispatcher_engine) as db:
        got = claim_batch(db, worker_id="typed-w2", event_types=(a, b))
        db.commit()
    assert {x.id for x in got if x.event_type in (a, b, c)} == {ids[a], ids[b]}
    assert _status(admin_session, ids[c]) == ("pending", None)
    with Session(dispatcher_engine) as db:
        rest = claim_batch(db, worker_id="typed-w3")  # no filter: historic claim
        db.commit()
    assert ids[c] in {x.id for x in rest}


@pytest.mark.parametrize("empty", ((), [], ""))
def test_an_empty_type_list_is_refused_not_read_as_no_filter(
    dispatcher_engine: Engine, empty
) -> None:
    with Session(dispatcher_engine) as db, pytest.raises(ValueError):
        claim_batch(db, worker_id="typed-w4", event_types=empty)


def test_the_sql_function_claims_nothing_for_a_null_or_empty_array(
    admin_session: Session, tenant_a, dispatcher_engine: Engine, cleanup
) -> None:
    eid = _seed(admin_session, tenant_a.id, f"typed.null.{uuid.uuid4().hex}")
    statements = (
        "SELECT id FROM claim_outbox_batch_by_type('w', 50, 300, NULL::text[])",
        "SELECT id FROM claim_outbox_batch_by_type('w', 50, 300, ARRAY[]::text[])",
    )
    with dispatcher_engine.connect() as conn:
        for statement in statements:
            rows = conn.execute(text(statement)).all()
            conn.commit()
            assert eid not in {r.id for r in rows}
    assert _status(admin_session, eid) == ("pending", None)


def test_the_grant_reaches_the_dispatcher_only(admin_session: Session) -> None:
    def may(role: str) -> bool:
        return bool(
            admin_session.execute(
                text("SELECT has_function_privilege(:r, :s, 'EXECUTE')"),
                {"r": role, "s": SIG},
            ).scalar()
        )

    assert may("outbox_dispatcher") is True
    assert may("app_user") is False
    assert may("platform_api") is False
    public = admin_session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(COALESCE(p.proacl, "
            "acldefault('f', p.proowner))) a WHERE p.oid = to_regprocedure(:s) "
            "AND a.grantee = 0 AND a.privilege_type = 'EXECUTE')"
        ),
        {"s": SIG},
    ).scalar()
    assert public is False
    facts = admin_session.execute(
        text(
            "SELECT prosecdef, proconfig, pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE oid = to_regprocedure(:s)"
        ),
        {"s": SIG},
    ).one()
    assert facts[0] is True and facts[2] == "app_admin"
    assert any(str(c).startswith("search_path=") for c in facts[1])


def test_the_dispatcher_still_has_no_table_access(dispatcher_engine: Engine) -> None:
    with dispatcher_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("SELECT count(*) FROM outbox_events")).fetchone()
