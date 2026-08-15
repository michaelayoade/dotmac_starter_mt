"""The login/disable race, on real Postgres, with every wait bounded.

`finalize_external_login` exists because a login that RESOLVES and then issues
its own session leaves a window: between those two statements an administrator
can disable the binding, and the result is a live session derived from an
identity revoked before that session existed. Both halves look successful — the
row is inactive, later resolutions refuse — and only the ordering makes them
incompatible.

That defect cannot be observed in the unit lane at all. SQLAlchemy's SQLite
compiler drops `FOR UPDATE` (sqlite has no such clause), so every unit test in
`tests/unit/test_external_identity.py` passes identically against an
implementation that never locks anything — `test_the_unit_lane_cannot_see_the_lock`
asserts exactly that, so the gap is disclosed rather than assumed. This file is
where the lock is actually load-bearing.

## Why these races are deterministic rather than hopeful

A canary that starts two threads and hopes for the interesting interleaving is
green most of the time for the wrong reason. Both directional canaries here are
forced instead, using one device: **the worker that must win takes the row lock
BEFORE the barrier, and the barrier is what releases the other worker.** Since a
`Barrier(2)` cannot release until both arrive, the loser provably starts its
locking statement with the lock already held by the winner, and the outcome is
determined rather than raced.

The first canary is also the one that DISCRIMINATES: the login has already read
the binding as active before it blocks, which is precisely the interleaving the
old resolve-then-issue pair would have admitted a session on.

## Requires real Postgres

`make test-db-up` / `make test-integration` (`TEST_DATABASE_URL`). Row locks and
RLS both need it.

## Bounded, because an unbounded canary in this repo cost twelve CI hours

Every wait has a ceiling: `SET LOCAL lock_timeout`, `SET LOCAL
statement_timeout`, `Barrier.wait(timeout=)` and `Future.result(timeout=)`.
Workers RETURN their outcome instead of asserting, so a failure reads as "these
two things both happened" rather than an opaque exception inside a future. Setup
happens in its own short-lived session, never on a racing one: `_as_tenant` sets
`app.current_tenant` TRANSACTION-locally, so a `commit()` discards it and the
next statement on that session is refused by RLS.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.external_identity import (
    disable_external_identity_binding,
    finalize_external_login,
)
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_BARRIER_TIMEOUT = 30.0
_FUTURE_TIMEOUT = 90.0
_LOCK_TIMEOUT = "15s"
_STATEMENT_TIMEOUT = "30s"

PROVIDER = "race-idp"
ISSUER = "https://idp.race.example.com"


@pytest.fixture(scope="module")
def tenant_engine() -> Generator[Engine, None, None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def tenant_sessionmaker(tenant_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=tenant_engine, autocommit=False, autoflush=False)


def _as_tenant(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Session:
    session = factory()
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    return session


def _bound_waits(session: Session) -> None:
    """No statement in a worker may wait forever, including the one that is
    SUPPOSED to block on the other worker's row lock."""
    session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    session.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))


def _seed(
    factory: sessionmaker[Session], tenant_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """An active person party and its active binding, in their OWN session.

    Deliberately not on a racing session: the commit here would discard that
    session's transaction-local `app.current_tenant`, and every later statement
    on it would be refused by RLS with no tenant context.
    """
    party_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    session = _as_tenant(factory, tenant_id)
    try:
        session.execute(
            text(
                "INSERT INTO parties (id, tenant_id, party_type, display_name, "
                "email, is_active) VALUES (:id, :tenant_id, 'person', :name, "
                ":email, true)"
            ),
            {
                "id": str(party_id),
                "tenant_id": str(tenant_id),
                "name": f"Race {party_id}",
                "email": f"race-{party_id}@example.com",
            },
        )
        session.execute(
            text(
                "INSERT INTO external_identity_bindings "
                "(id, tenant_id, party_id, provider_binding, issuer, subject, "
                " is_active, bound_by, bind_reason) "
                "VALUES (:id, :tenant_id, :party_id, :provider, :issuer, "
                " :subject, true, :bound_by, :reason)"
            ),
            {
                "id": str(binding_id),
                "tenant_id": str(tenant_id),
                "party_id": str(party_id),
                "provider": PROVIDER,
                "issuer": ISSUER,
                "subject": f"race-{binding_id}",
                "bound_by": "canary@example.com",
                "reason": "login race canary",
            },
        )
        session.commit()
        return party_id, binding_id
    finally:
        session.close()


def _subject_of(admin_session: Session, binding_id: uuid.UUID) -> str:
    row = admin_session.execute(
        text("SELECT subject FROM external_identity_bindings WHERE id = :id"),
        {"id": str(binding_id)},
    ).scalar_one()
    return str(row)


def _issue_session(session: Session, *, tenant_id: uuid.UUID, party_id: uuid.UUID):
    """What the CALLER does with a finalized login: mint its own session row.

    In the same transaction as the finalization, which is the whole contract —
    the commit that makes this row visible is the commit that releases the
    binding's lock, so the session and the stamp appear together or not at all.

    Raw SQL rather than the ORM `AuthSession`, matching this lane's idiom: these
    canaries are about what the DATABASE permits two transactions to do, and an
    ORM identity map between the test and the rows would be one more thing to
    reason about.
    """
    session.execute(
        text(
            "INSERT INTO auth_sessions (id, tenant_id, party_id, token_hash, "
            "expires_at) VALUES (:id, :tenant_id, :party_id, :token_hash, "
            "now() + interval '1 hour')"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "party_id": str(party_id),
            "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
        },
    )


def _login_worker(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    subject: str,
    barrier: threading.Barrier,
    lock_before_barrier: bool,
) -> dict[str, object]:
    """Finalize a login and, if it is granted, issue the session for it.

    `lock_before_barrier` decides which side of the barrier the locking call
    sits on, and that is the whole forcing device — with it True this worker
    provably holds the binding's row lock before the other worker is released.
    """
    session = _as_tenant(factory, tenant_id)
    try:
        _bound_waits(session)
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None

        # The UNLOCKED read the retired resolve-then-issue pair trusted. It runs
        # before the barrier on purpose: it is what makes this the racy
        # interleaving rather than a well-ordered one, and the assertion on
        # `seen_active_before` is what proves the canary reproduced it.
        seen_active = session.execute(
            text(
                "SELECT is_active FROM external_identity_bindings "
                "WHERE provider_binding = :p AND issuer = :i AND subject = :s"
            ),
            {"p": PROVIDER, "i": ISSUER, "s": subject},
        ).scalar()

        def finalize():
            return finalize_external_login(
                session,
                tenant=tenant,
                provider_binding=PROVIDER,
                issuer=ISSUER,
                subject=subject,
            )

        if lock_before_barrier:
            finalized = finalize()
            barrier.wait(timeout=_BARRIER_TIMEOUT)
        else:
            barrier.wait(timeout=_BARRIER_TIMEOUT)
            finalized = finalize()

        if finalized is None:
            session.rollback()
            return {"outcome": "login_refused", "seen_active_before": seen_active}

        _issue_session(session, tenant_id=tenant_id, party_id=party_id)
        session.commit()
        return {
            "outcome": "session_issued",
            "seen_active_before": seen_active,
            "binding_id": str(finalized.binding_id),
        }
    finally:
        session.close()


def _disable_worker(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    binding_id: uuid.UUID,
    barrier: threading.Barrier,
    lock_before_barrier: bool,
) -> dict[str, object]:
    """Disable the binding — the administrative act the login must serialize
    against. `disable_external_identity_binding` flushes an `UPDATE`, which
    takes the same row lock `finalize_external_login` holds."""
    session = _as_tenant(factory, tenant_id)
    try:
        _bound_waits(session)
        tenant = session.get(Tenant, tenant_id)
        assert tenant is not None

        def disable() -> None:
            disable_external_identity_binding(
                session, tenant=tenant, binding_id=binding_id
            )

        if lock_before_barrier:
            disable()
            barrier.wait(timeout=_BARRIER_TIMEOUT)
        else:
            barrier.wait(timeout=_BARRIER_TIMEOUT)
            disable()

        session.commit()
        return {"outcome": "disabled"}
    finally:
        session.close()


def _race(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    binding_id: uuid.UUID,
    subject: str,
    login_locks_first: bool,
) -> dict[str, dict[str, object]]:
    barrier = threading.Barrier(2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        login = pool.submit(
            _login_worker,
            factory,
            tenant_id=tenant_id,
            party_id=party_id,
            subject=subject,
            barrier=barrier,
            lock_before_barrier=login_locks_first,
        )
        disable = pool.submit(
            _disable_worker,
            factory,
            tenant_id=tenant_id,
            binding_id=binding_id,
            barrier=barrier,
            lock_before_barrier=not login_locks_first,
        )
        return {
            "login": login.result(timeout=_FUTURE_TIMEOUT),
            "disable": disable.result(timeout=_FUTURE_TIMEOUT),
        }


def _observe(
    admin_session: Session, *, party_id: uuid.UUID, binding_id: uuid.UUID
) -> dict[str, object]:
    """What survived, read as `app_admin` so RLS cannot hide a row from the
    assertion — a canary that could not see the wrong outcome would pass."""
    row = admin_session.execute(
        text(
            "SELECT is_active, last_authenticated_at IS NOT NULL AS stamped "
            "FROM external_identity_bindings WHERE id = :id"
        ),
        {"id": str(binding_id)},
    ).one()
    sessions = admin_session.execute(
        text("SELECT count(*) FROM auth_sessions WHERE party_id = :id"),
        {"id": str(party_id)},
    ).scalar_one()
    admin_session.rollback()
    return {
        "binding_active": row.is_active,
        "stamped": row.stamped,
        "sessions": sessions,
    }


def _cleanup(
    admin_session: Session, *, party_id: uuid.UUID, binding_id: uuid.UUID
) -> None:
    admin_session.execute(
        text("DELETE FROM auth_sessions WHERE party_id = :id"), {"id": str(party_id)}
    )
    admin_session.execute(
        text("DELETE FROM external_identity_bindings WHERE id = :id"),
        {"id": str(binding_id)},
    )
    admin_session.execute(
        text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
    )
    admin_session.commit()


def test_a_disable_that_holds_the_lock_first_forces_the_login_to_refuse(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """THE canary. This is the interleaving the defect admitted a session on.

    The login worker reads the binding as active, and only then does the
    disabling transaction — which already holds the row lock — commit. A
    resolve-then-issue login would carry that stale `active` across the commit
    and mint a session for an identity that was revoked before the session
    existed. `finalize_external_login` cannot: its locking read blocks until the
    disable commits, re-reads `is_active = False` under the lock, and refuses.

    The assertions are therefore three, and each is doing work:
    `seen_active_before` proves the racy precondition was actually reproduced
    (without it a refusal could just mean the login looked after the commit),
    zero session rows proves nothing was minted, and an unset
    `last_authenticated_at` proves the refusal left no evidence of an
    authentication that never happened.
    """
    party_id, binding_id = _seed(tenant_sessionmaker, tenant_a.id)
    subject = _subject_of(admin_session, binding_id)
    admin_session.rollback()
    try:
        results = _race(
            tenant_sessionmaker,
            tenant_id=tenant_a.id,
            party_id=party_id,
            binding_id=binding_id,
            subject=subject,
            login_locks_first=False,
        )
        assert results["disable"]["outcome"] == "disabled"
        assert results["login"]["outcome"] == "login_refused", (
            "a session was issued from a binding whose disable had already "
            f"committed — the window is open: {results}"
        )
        assert results["login"]["seen_active_before"] is True, (
            "the canary did not reproduce the racy interleaving: the login never "
            "read the binding as active, so its refusal proves nothing"
        )

        state = _observe(admin_session, party_id=party_id, binding_id=binding_id)
        assert state == {"binding_active": False, "stamped": False, "sessions": 0}
    finally:
        _cleanup(admin_session, party_id=party_id, binding_id=binding_id)


def test_a_login_that_holds_the_lock_first_makes_the_disable_wait_behind_it(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The other direction, and it must NOT refuse.

    A lock that closed the window by making every contested login fail would be
    a denial of service dressed as a fix. Here the login takes the lock first,
    so the disable blocks until the login's transaction commits and the session
    is legitimately issued from a binding that was active throughout.

    The disable then succeeds — and the session it was meant to prevent is
    already there, unrevoked. That is not a defect of this fix and the canary
    asserts it deliberately: serializing the DECISION cannot retract a session
    that already exists. Retracting it needs the session to record which binding
    produced it, which is the deferred provenance contract in the module
    docstring. This assertion is what will have to change when that lands.
    """
    party_id, binding_id = _seed(tenant_sessionmaker, tenant_a.id)
    subject = _subject_of(admin_session, binding_id)
    admin_session.rollback()
    try:
        results = _race(
            tenant_sessionmaker,
            tenant_id=tenant_a.id,
            party_id=party_id,
            binding_id=binding_id,
            subject=subject,
            login_locks_first=True,
        )
        assert results["disable"]["outcome"] == "disabled"
        assert results["login"]["outcome"] == "session_issued", (
            "a login that held the lock first was refused anyway — the fix has "
            f"turned a contested login into a failed one: {results}"
        )
        assert results["login"]["binding_id"] == str(binding_id)

        state = _observe(admin_session, party_id=party_id, binding_id=binding_id)
        assert state == {"binding_active": False, "stamped": True, "sessions": 1}
    finally:
        _cleanup(admin_session, party_id=party_id, binding_id=binding_id)


@pytest.mark.parametrize("attempt", range(4))
def test_an_unforced_race_never_lands_between_the_two_legal_outcomes(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
    attempt: int,
) -> None:
    """Neither worker is forced; the database decides, and both answers are fine.

    What must never happen is a THIRD outcome, and the invariant that rules it
    out is the coupling: the stamp and the session row are written in one
    transaction, so `a session exists` and `the binding is stamped` are the same
    fact. A login that decided in one transaction and issued in another could
    break that pairing in either direction — a session with no stamp, or a stamp
    with no session — and the equality below is what would catch it.

    Repeated a few times because an unforced race is a sample, not a proof; the
    two forced canaries above are the proof. Every repetition is still bounded
    by the same four timeouts.
    """
    party_id, binding_id = _seed(tenant_sessionmaker, tenant_a.id)
    subject = _subject_of(admin_session, binding_id)
    admin_session.rollback()
    try:
        barrier = threading.Barrier(2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            login = pool.submit(
                _login_worker,
                tenant_sessionmaker,
                tenant_id=tenant_a.id,
                party_id=party_id,
                subject=subject,
                barrier=barrier,
                lock_before_barrier=False,
            )
            disable = pool.submit(
                _disable_worker,
                tenant_sessionmaker,
                tenant_id=tenant_a.id,
                binding_id=binding_id,
                barrier=barrier,
                lock_before_barrier=False,
            )
            results = {
                "login": login.result(timeout=_FUTURE_TIMEOUT),
                "disable": disable.result(timeout=_FUTURE_TIMEOUT),
            }

        assert results["disable"]["outcome"] == "disabled"
        assert results["login"]["outcome"] in {"session_issued", "login_refused"}

        state = _observe(admin_session, party_id=party_id, binding_id=binding_id)
        issued = results["login"]["outcome"] == "session_issued"
        assert state["sessions"] == (1 if issued else 0)
        assert state["stamped"] is issued, (
            "the stamp and the session parted company — they are written in one "
            f"transaction, so this means the decision and the write did not: {state}"
        )
        assert state["binding_active"] is False
    finally:
        _cleanup(admin_session, party_id=party_id, binding_id=binding_id)
