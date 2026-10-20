"""Tenant-isolation canary for `external_identity_bindings`.

This is the canary that had to be written first. The table decides *who a login
is*, so its two failure modes are the worst in the system:

- a cross-tenant READ enumerates another tenant's workforce and the exact
  external subjects that authenticate them — a target list for the IdP;
- a cross-tenant WRITE binds an attacker-controlled subject to somebody else's
  party, which is silent account takeover with no password involved.

Both source products would permit exactly that. ERP's `federated_identities` has
no tenant or organization column at all, so its `(issuer, subject)` uniqueness
is global and the boundary is only transitive through the person FK; Sub's
migration 527 says `- no NOT NULL, no RLS` in as many words, and its own tests
assert the absence. Kernel migration `0024` adds both, and this file is the
proof they are armed rather than merely written down.

Requires real Postgres (`make test-db-up` / `make test-integration`); RLS does
not exist in SQLite, so nothing here is meaningful in the unit lane.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel.exceptions import ConflictError
from dotmac_kernel.external_identity import bind_external_identity
from dotmac_kernel.models import ExternalIdentityBinding, Party, Tenant
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

# Every wait in the race below is bounded. The first version of these
# canaries inserted twice from ONE thread: the second INSERT blocked on the
# first transaction's row lock, and the commit that would have released it
# was the next statement — so it could never run. CI ran for six hours
# before being cancelled.
_BARRIER_TIMEOUT = 30.0
_FUTURE_TIMEOUT = 90.0

PROVIDER = "canary-idp"
ISSUER = "https://idp.canary.example.com"


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


def _insert_party(session: Session, *, tenant_id: uuid.UUID) -> uuid.UUID:
    party_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO parties (id, tenant_id, party_type, display_name, "
            "email, is_active) VALUES (:id, :tenant_id, 'person', :name, "
            ":email, true)"
        ),
        {
            "id": str(party_id),
            "tenant_id": str(tenant_id),
            "name": f"Canary {party_id}",
            "email": f"canary-{party_id}@example.com",
        },
    )
    return party_id


def _insert_binding(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    subject: str,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO external_identity_bindings "
            "(id, tenant_id, party_id, provider_binding, issuer, subject, "
            " is_active, bound_by, bind_reason) "
            "VALUES (:id, :tenant_id, :party_id, :provider, :issuer, :subject, "
            " true, :bound_by, :reason)"
        ),
        {
            "id": str(row_id),
            "tenant_id": str(tenant_id),
            "party_id": str(party_id),
            "provider": PROVIDER,
            "issuer": ISSUER,
            "subject": subject,
            "bound_by": "canary@example.com",
            "reason": "isolation canary",
        },
    )
    return row_id


def test_binding_in_tenant_a_invisible_to_tenant_b(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    subject = f"subject-{uuid.uuid4()}"
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_party(a, tenant_id=tenant_a.id)
        row_id = _insert_binding(
            a, tenant_id=tenant_a.id, party_id=party_id, subject=subject
        )
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            assert (
                b.execute(
                    text("SELECT id FROM external_identity_bindings WHERE id = :id"),
                    {"id": str(row_id)},
                ).fetchall()
                == []
            )
            # The lookup a resolver actually performs must also miss — proving
            # isolation on the QUERY SHAPE that matters, not only by row id.
            assert (
                b.execute(
                    text(
                        "SELECT id FROM external_identity_bindings "
                        "WHERE provider_binding = :p AND issuer = :i "
                        "AND subject = :s AND is_active"
                    ),
                    {"p": PROVIDER, "i": ISSUER, "s": subject},
                ).fetchall()
                == []
            )
        finally:
            b.rollback()
            b.close()

        a2 = _as_tenant(tenant_sessionmaker, tenant_a.id)
        try:
            assert (
                a2.execute(
                    text("SELECT id FROM external_identity_bindings WHERE id = :id"),
                    {"id": str(row_id)},
                ).fetchone()
                is not None
            )
        finally:
            a2.rollback()
            a2.close()
    finally:
        admin_session.execute(
            text("DELETE FROM external_identity_bindings WHERE id = :id"),
            {"id": str(row_id)},
        )
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def test_with_check_denies_binding_a_subject_into_another_tenant(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The takeover case. Tenant B, in its own context, must not be able to
    write a binding stamped with tenant A's id — the policy's `WITH CHECK`
    rejects it, and without FORCE the table owner would sail past this."""
    a = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        party_id = _insert_party(a, tenant_id=tenant_a.id)
        a.commit()
    finally:
        a.close()

    try:
        b = _as_tenant(tenant_sessionmaker, tenant_b.id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                _insert_binding(
                    b,
                    tenant_id=tenant_a.id,
                    party_id=party_id,
                    subject=f"attacker-{uuid.uuid4()}",
                )
        finally:
            b.rollback()
            b.close()
    finally:
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def _seed_party(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> uuid.UUID:
    """Create a party in its OWN short-lived session.

    Deliberately not reusing a racing session for setup. `_as_tenant` issues
    `set_config('app.current_tenant', ..., true)` — TRANSACTION-local — so a
    `commit()` discards it, and the next statement on that same session runs
    with no tenant context and is refused by RLS. That is the same mechanism
    `conflict_savepoint` exists for, and CI caught it here: the first version of
    these canaries created parties on the racing sessions, committed, and then
    could not insert a binding on them.
    """
    session = _as_tenant(factory, tenant_id)
    try:
        party_id = _insert_party(session, tenant_id=tenant_id)
        session.commit()
        return party_id
    finally:
        session.close()


def _binder(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    subject: str,
    barrier: threading.Barrier,
    force_last_read: bool,
) -> dict[str, object]:
    """Drive the REAL service, in this thread's own session, and report.

    Returns rather than asserts: a failed assertion inside a worker surfaces as
    an opaque future exception, and what the test needs to say is "exactly one
    of the two succeeded".
    """
    session = _as_tenant(factory, tenant_id)
    try:
        # BOUNDED WAITS, and they are the point. The first version of this
        # canary inserted twice from ONE thread — the second INSERT blocked on
        # the first transaction's unopened row lock and the commit that would
        # have released it was the next statement, so it could never run. CI ran
        # for six hours before it was cancelled. Every wait here has a ceiling:
        # the lock, the statement, the barrier and the future.
        session.execute(text("SET LOCAL lock_timeout = '15s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))

        tenant = session.get(Tenant, tenant_id)
        party = session.get(Party, party_id)
        assert tenant is not None and party is not None

        # The ADVISORY read `bind_external_identity` performs first. Doing it
        # here too, before the barrier, is what makes the race real: both
        # workers reach the insert believing the subject is unbound.
        session.execute(
            text(
                "SELECT 1 FROM external_identity_bindings "
                "WHERE provider_binding = :p AND issuer = :i AND subject = :s"
            ),
            {"p": PROVIDER, "i": ISSUER, "s": subject},
        ).fetchall()

        barrier.wait(timeout=_BARRIER_TIMEOUT)

        connection = session.connection()
        final_read_seen = []

        def final_advisory(conn, cursor, statement, parameters, context, executemany):
            compiled = context.compiled
            if compiled is None or not force_last_read or final_read_seen:
                return
            selected = tuple(getattr(compiled.statement, "selected_columns", ()))
            if len(selected) == 1 and selected[0].compare(
                ExternalIdentityBinding.__table__.c.id
            ):
                final_read_seen.append(1)
                # Both real SERVICE final advisory SELECTs have returned no
                # provider membership before either distinct-Party INSERT.
                barrier.wait(timeout=_BARRIER_TIMEOUT)

        event.listen(connection, "after_cursor_execute", final_advisory)
        try:
            bind_external_identity(
                session,
                tenant=tenant,
                party=party,
                provider_binding=PROVIDER,
                issuer=ISSUER,
                subject=subject,
                bound_by="canary@example.com",
                reason="concurrency canary",
            )
        except ConflictError as exc:
            # THE property `conflict_savepoint` exists for. The savepoint rolled
            # back, so the OUTER transaction — and its transaction-local `SET
            # LOCAL app.current_tenant` — must still be intact. A bare
            # `db.rollback()` would have discarded the GUC along with the whole
            # transaction, and every later read in that request would fail closed
            # under FORCE RLS.
            #
            # Read the GUC ITSELF rather than inferring it from row visibility.
            # An earlier version counted rows for this worker's own subject —
            # which the LOSER never committed, so in the two-subject canary it
            # saw zero and blamed the savepoint for the race outcome. The GUC is
            # the property under test and it does not depend on who won.
            scope = session.execute(text("SELECT app_current_tenant_id()")).scalar()
            # And the transaction must still be USABLE, not merely scoped.
            readable = session.execute(
                text("SELECT count(*) FROM external_identity_bindings")
            ).scalar()
            cause = exc.__cause__
            original = getattr(cause, "orig", None)
            diag = getattr(original, "diag", None)
            constraint = getattr(diag, "constraint_name", None)
            sqlstate = getattr(original, "sqlstate", None) or getattr(
                original, "pgcode", None
            )
            advisory = (
                "this party already holds an external identity at this provider — "
                "a party may hold at most one per provider registration"
            )
            # Return only trusted diagnostic identifiers and exact-message
            # classification. Never retain raw exception/SQL/parameter text.
            session.rollback()
            return {
                "outcome": "conflict",
                "cause_class": type(cause).__name__ if cause is not None else None,
                "sqlstate": sqlstate if sqlstate == "23505" else None,
                "constraint": constraint
                if constraint
                in (
                    "uq_external_identity_bindings_tenant_provider_subject",
                    "uq_external_identity_bindings_tenant_provider_party",
                )
                else None,
                "advisory_exact": str(exc) == advisory,
                "final_read_synchronized": bool(final_read_seen),
                "tenant_scope_intact": (
                    scope is not None
                    and str(scope) == str(tenant_id)
                    and readable is not None
                ),
            }

        finally:
            event.remove(connection, "after_cursor_execute", final_advisory)
        session.commit()
        return {"outcome": "bound", "final_read_synchronized": bool(final_read_seen)}
    finally:
        session.close()


def _race(
    factory: sessionmaker[Session],
    *,
    tenant_id: uuid.UUID,
    parties: tuple[uuid.UUID, uuid.UUID],
    subjects: tuple[str, str],
) -> list[dict[str, object]]:
    barrier = threading.Barrier(2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _binder,
                factory,
                tenant_id=tenant_id,
                party_id=party_id,
                subject=subject,
                barrier=barrier,
                force_last_read=parties[0] != parties[1] and subjects[0] == subjects[1],
            )
            for party_id, subject in zip(parties, subjects, strict=True)
        ]
        return [f.result(timeout=_FUTURE_TIMEOUT) for f in futures]


def _assert_one_won(
    results: list[dict[str, object]], *, constraint: str, advisory: bool = False
) -> None:
    outcomes = sorted(str(r["outcome"]) for r in results)
    assert outcomes == [
        "bound",
        "conflict",
    ], f"expected exactly one winner and one refusal, got {outcomes}: {results}"
    loser = next(r for r in results if r["outcome"] == "conflict")
    if advisory:
        assert loser["advisory_exact"] is True
        assert loser["cause_class"] is None
        assert loser["sqlstate"] is None and loser["constraint"] is None
    else:
        assert all(r["final_read_synchronized"] is True for r in results)
        assert loser["cause_class"] == "IntegrityError"
        assert loser["sqlstate"] == "23505"
        assert loser["constraint"] == constraint
    assert loser["tenant_scope_intact"], (
        "the loser's outer transaction lost its tenant scope — the conflict was "
        "not contained in a SAVEPOINT, so `SET LOCAL app.current_tenant` was "
        "discarded and every later read in that request would fail closed"
    )


def test_two_concurrent_binders_cannot_both_claim_one_subject(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The check-then-insert race, arbitrated by the database.

    `bind_external_identity` looks the subject up before inserting, but that
    lookup is ADVISORY: two binders can both see no row, both pass, and both
    insert. Only the unique constraint decides — so this drives the real service
    from two threads, synchronised after both advisory reads, and asserts the
    loser is refused rather than admitted.

    Through the SERVICE rather than raw DDL, deliberately: the production
    behaviour under test is that the loser receives `ConflictError` from a
    savepoint-contained failure, not an `IntegrityError` that would abort the
    whole request transaction.
    """
    subject = f"contested-{uuid.uuid4()}"
    alice = _seed_party(tenant_sessionmaker, tenant_a.id)
    bob = _seed_party(tenant_sessionmaker, tenant_a.id)
    try:
        results = _race(
            tenant_sessionmaker,
            tenant_id=tenant_a.id,
            parties=(alice, bob),
            subjects=(subject, subject),
        )
        _assert_one_won(
            results,
            constraint="uq_external_identity_bindings_tenant_provider_subject",
        )
    finally:
        admin_session.execute(
            text("DELETE FROM external_identity_bindings WHERE subject = :s"),
            {"s": subject},
        )
        for party_id in (alice, bob):
            admin_session.execute(
                text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
            )
        admin_session.commit()


def test_one_party_cannot_hold_two_identities_at_one_provider_concurrently(
    admin_session: Session,
    tenant_a,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The second unique constraint, raced the same way.

    One party, one provider, two DIFFERENT subjects. The advisory lookup for
    "does this party already hold one here" is equally racy, and this is the
    constraint that has to catch it.
    """
    party_id = _seed_party(tenant_sessionmaker, tenant_a.id)
    subjects = (f"one-{uuid.uuid4()}", f"two-{uuid.uuid4()}")
    try:
        results = _race(
            tenant_sessionmaker,
            tenant_id=tenant_a.id,
            parties=(party_id, party_id),
            subjects=subjects,
        )
        _assert_one_won(
            results,
            constraint="uq_external_identity_bindings_tenant_provider_party",
            advisory=True,
        )
    finally:
        for subject in subjects:
            admin_session.execute(
                text("DELETE FROM external_identity_bindings WHERE subject = :s"),
                {"s": subject},
            )
        admin_session.execute(
            text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
        )
        admin_session.commit()


def test_the_same_subject_may_be_a_different_person_in_each_tenant(
    admin_session: Session,
    tenant_a,
    tenant_b,
    tenant_sessionmaker: sessionmaker[Session],
) -> None:
    """The reason uniqueness is tenant-partitioned rather than global.

    ERP's `(issuer, subject)` unique pair is global, so one subject can exist
    once across the whole deployment. Here two tenants may each know the same
    external subject as their own person — a genuine case for a contractor or a
    shared corporate IdP — and neither can see the other's binding.
    """
    subject = f"shared-{uuid.uuid4()}"
    created: list[tuple[uuid.UUID, uuid.UUID]] = []
    try:
        for tenant in (tenant_a, tenant_b):
            session = _as_tenant(tenant_sessionmaker, tenant.id)
            try:
                party_id = _insert_party(session, tenant_id=tenant.id)
                row_id = _insert_binding(
                    session,
                    tenant_id=tenant.id,
                    party_id=party_id,
                    subject=subject,
                )
                session.commit()
                created.append((row_id, party_id))
            finally:
                session.close()

        assert len(created) == 2, "the second tenant's binding was refused"
    finally:
        for row_id, party_id in created:
            admin_session.execute(
                text("DELETE FROM external_identity_bindings WHERE id = :id"),
                {"id": str(row_id)},
            )
            admin_session.execute(
                text("DELETE FROM parties WHERE id = :id"), {"id": str(party_id)}
            )
        admin_session.commit()


@pytest.mark.parametrize("pending_kind", ("new", "dirty", "deleted"))
@pytest.mark.parametrize("command", ("bind", "finalize", "disable", "stamp"))
def test_binding_writers_refuse_all_pending_before_io_without_clobber(
    tenant_sessionmaker, tenant_a, pending_kind, command
):
    from dotmac_kernel.external_identity import (
        disable_external_identity_binding,
        finalize_external_login,
        record_external_authentication,
    )
    from dotmac_kernel.models import PartyType
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event
    from sqlalchemy import inspect as sa_inspect

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        pid = _insert_party(session, tenant_id=tenant_a.id)
        bid = _insert_binding(
            session,
            tenant_id=tenant_a.id,
            party_id=pid,
            subject="pending-" + uuid.uuid4().hex,
        )
        tenant = session.get(Tenant, tenant_a.id)
        party = session.get(Party, pid)
        connection = session.connection()
        root = session.get_transaction()
        if pending_kind == "new":
            changed = Party(
                id=uuid.uuid4(),
                tenant_id=tenant_a.id,
                party_type=PartyType.person,
                display_name="Caller pending",
            )
            session.add(changed)
        elif pending_kind == "dirty":
            changed = party
            changed.display_name = "Caller pending"
        else:
            changed = party
            session.delete(changed)
        pending = (tuple(session.new), tuple(session.dirty), tuple(session.deleted))
        history = sa_inspect(changed).attrs.display_name.history
        calls = []

        def observe(*args):
            calls.append(1)

        event.listen(connection, "before_cursor_execute", observe)
        try:
            with pytest.raises(
                ConflictSavepointProtocolError, match="clean pending state"
            ):
                if command == "bind":
                    bind_external_identity(
                        session,
                        tenant=tenant,
                        party=party,
                        provider_binding=PROVIDER,
                        issuer=ISSUER,
                        subject="new",
                        bound_by="canary",
                        reason="pending canary",
                    )
                elif command == "finalize":
                    finalize_external_login(
                        session,
                        tenant=tenant,
                        provider_binding=PROVIDER,
                        issuer=ISSUER,
                        subject="unknown",
                    )
                elif command == "disable":
                    disable_external_identity_binding(
                        session, tenant=tenant, binding_id=bid
                    )
                else:
                    record_external_authentication(
                        session, tenant=tenant, binding_id=bid
                    )
            assert calls == []
            assert (
                tuple(session.new),
                tuple(session.dirty),
                tuple(session.deleted),
            ) == pending
            assert sa_inspect(changed).attrs.display_name.history == history
            assert session.get_transaction() is root
        finally:
            event.remove(connection, "before_cursor_execute", observe)
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize(
    "phase", ("open", "query", "write", "flush", "release", "cancel")
)
def test_binding_callback_or_cancellation_rolls_back_only_materialized_operation(
    tenant_sessionmaker, tenant_a, phase
):
    from dotmac_kernel.models import ExternalIdentityBinding, PartyType
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import event, select

    class Cancelled(BaseException):
        pass

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    listener = None
    target = None
    name = None
    try:
        pid = _insert_party(session, tenant_id=tenant_a.id)
        party = session.get(Party, pid)
        tenant = session.get(Tenant, tenant_a.id)
        root = session.get_transaction()
        connection = session.connection()
        pending = Party(
            id=uuid.uuid4(),
            tenant_id=tenant_a.id,
            party_type=PartyType.person,
            display_name="Hook pending",
        )
        subject = "callback-" + uuid.uuid4().hex
        invoked = []

        def inject():
            invoked.append(1)
            if phase == "cancel":
                raise Cancelled()
            session.add(pending)
            if phase == "flush":
                # The hook catches the rejection and cleans up, but the issued
                # owner's failed latch must still prevent operation release.
                with pytest.raises(ConflictSavepointProtocolError):
                    session.flush()
                session.expunge(pending)

        if phase == "cancel":
            # An exit exception raised inside cursor execution invalidates
            # the connection by SQLAlchemy design (is_exit_exception), after
            # which no savepoint rollback can preserve the root; raise at
            # the ORM execute boundary so cancellation itself is exercised.
            target, name = session, "do_orm_execute"

            def listener(state):
                if not invoked and state.is_insert:
                    inject()
                    raise AssertionError("unreachable: inject cancels")
                return None

        elif phase == "open":
            target, name = session, "after_transaction_create"

            def listener(db, transaction):
                if transaction.nested:
                    inject()
        elif phase == "release":
            target, name = session, "before_commit"

            def listener(db):
                inject()
        else:
            target, name = connection, "after_cursor_execute"

            def listener(conn, cursor, statement, parameters, context, executemany):
                compiled = context.compiled
                if compiled is None:
                    return
                is_read = getattr(compiled.statement, "is_select", False)
                if not invoked and (
                    (phase == "write" and context.isinsert)
                    or (phase in ("query", "flush") and is_read)
                ):
                    inject()

        event.listen(target, name, listener)
        with pytest.raises(
            Cancelled if phase == "cancel" else ConflictSavepointProtocolError
        ):
            bind_external_identity(
                session,
                tenant=tenant,
                party=party,
                provider_binding=PROVIDER,
                issuer=ISSUER,
                subject=subject,
                bound_by="canary",
                reason="callback rollback canary",
            )
        event.remove(target, name, listener)
        listener = None
        assert invoked == [1]
        assert session.get_transaction() is root
        assert session.scalar(select(Party.id).where(Party.id == pid)) == pid
        assert (
            session.scalar(
                select(ExternalIdentityBinding.id).where(
                    ExternalIdentityBinding.subject == subject
                )
            )
            is None
        )
        assert session.scalar(select(Party.id).where(Party.id == pending.id)) is None
        assert str(session.scalar(text("SELECT app_current_tenant_id()"))) == str(
            tenant_a.id
        )
        assert not session.new and not session.dirty and not session.deleted
        from dotmac_kernel.transactions import conflict_savepoint

        with conflict_savepoint(session):
            session.connection()
        assert session.get_transaction() is root
    finally:
        if listener is not None:
            event.remove(target, name, listener)
        session.rollback()
        session.close()


def test_preexisting_same_party_lower_child_and_distinct_party_composition_keep_root(
    admin_session, tenant_a, tenant_sessionmaker
):
    from datetime import UTC, datetime, timedelta

    from dotmac_kernel import identity_storage as store
    from dotmac_kernel.external_identity import disable_external_identity_binding
    from dotmac_kernel.models import AuthSession
    from dotmac_kernel.transactions import conflict_savepoint
    from sqlalchemy import insert, select

    parties = tuple(_seed_party(tenant_sessionmaker, tenant_a.id) for _ in range(2))
    low, high = sorted((uuid.uuid4(), uuid.uuid4()))
    setup = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        for bid, provider in ((high, PROVIDER), (low, PROVIDER + "-second")):
            setup.execute(
                insert(ExternalIdentityBinding.__table__).values(
                    id=bid,
                    tenant_id=tenant_a.id,
                    party_id=parties[0],
                    provider_binding=provider,
                    issuer=ISSUER,
                    subject="preexisting-" + bid.hex,
                    is_active=True,
                    bound_by="canary",
                    bind_reason="committed fixture",
                )
            )
        setup.commit()
    finally:
        setup.close()
    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        root = session.get_transaction()
        tenant = session.get(Tenant, tenant_a.id)
        party = session.get(Party, parties[0])
        first = bind_external_identity(
            session,
            tenant=tenant,
            party=party,
            provider_binding=PROVIDER,
            issuer=ISSUER,
            subject="preexisting-" + high.hex,
            bound_by="canary",
            reason="retained first child",
        )
        disable_external_identity_binding(session, tenant=tenant, binding_id=first.id)
        second = bind_external_identity(
            session,
            tenant=tenant,
            party=party,
            provider_binding=PROVIDER + "-second",
            issuer=ISSUER,
            subject="preexisting-" + low.hex,
            bound_by="canary",
            reason="later lower child",
        )
        assert second.id == low and first.id == high
        sid = uuid.uuid4()
        session.execute(
            insert(AuthSession.__table__).values(
                id=sid,
                tenant_id=tenant_a.id,
                party_id=party.id,
                token_hash=uuid.uuid4().hex * 2,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                external_identity_binding_id=second.id,
            )
        )
        with conflict_savepoint(session):
            session.connection()
            ids = tuple(
                session.scalars(
                    select(AuthSession.id)
                    .where(
                        AuthSession.tenant_id == tenant_a.id,
                        AuthSession.party_id == party.id,
                    )
                    .order_by(AuthSession.id)
                    .with_for_update()
                )
            )
            assert store.revoke_party_sessions(
                session,
                tenant_id=tenant_a.id,
                party_id=party.id,
                session_ids=ids,
                revoked_at=datetime.now(UTC),
            ) == (sid,)
        # No automatic root-closure rejection: successful different-Party
        # sequential calls remain callable in either UUID order. This is not
        # a claim about unknown future concurrent multi-Party composition.
        for ordered in (tuple(sorted(parties)), tuple(sorted(parties, reverse=True))):
            for pid in ordered:
                target = session.get(Party, pid)
                bind_external_identity(
                    session,
                    tenant=tenant,
                    party=target,
                    provider_binding="ordered-" + pid.hex,
                    issuer=ISSUER,
                    subject=pid.hex,
                    bound_by="canary",
                    reason="caller composition",
                )
        assert session.get_transaction() is root
        assert str(session.scalar(text("SELECT app_current_tenant_id()"))) == str(
            tenant_a.id
        )
    finally:
        session.rollback()
        session.close()
        for pid in parties:
            admin_session.execute(
                text("DELETE FROM auth_sessions WHERE party_id=:id"), {"id": str(pid)}
            )
            admin_session.execute(
                text("DELETE FROM external_identity_bindings WHERE party_id=:id"),
                {"id": str(pid)},
            )
            admin_session.execute(
                text("DELETE FROM parties WHERE id=:id"), {"id": str(pid)}
            )
        admin_session.commit()


def test_fresh_other_owner_collision_ignores_stale_map_and_never_locks_proposed_party(
    admin_session, tenant_a, tenant_sessionmaker
):
    from sqlalchemy import select, update

    owner, proposed = tuple(
        _seed_party(tenant_sessionmaker, tenant_a.id) for _ in range(2)
    )
    setup = _as_tenant(tenant_sessionmaker, tenant_a.id)
    subject = "stale-" + uuid.uuid4().hex
    try:
        bid = _insert_binding(
            setup, tenant_id=tenant_a.id, party_id=proposed, subject=subject
        )
        setup.commit()
    finally:
        setup.close()
    caller = _as_tenant(tenant_sessionmaker, tenant_a.id)
    locker = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        stale = caller.get(ExternalIdentityBinding, bid)
        assert stale.party_id == proposed
        # A deliberate physical fault fixture, not a product repointing API.
        locker.execute(
            update(ExternalIdentityBinding.__table__)
            .where(ExternalIdentityBinding.id == bid)
            .values(party_id=owner)
        )
        locker.commit()
        locker.close()
        locker = _as_tenant(tenant_sessionmaker, tenant_a.id)
        locker.scalar(select(Party.id).where(Party.id == proposed).with_for_update())
        caller.execute(text("SET LOCAL lock_timeout = '250ms'"))
        earlier = _insert_party(caller, tenant_id=tenant_a.id)
        root = caller.get_transaction()
        tenant, party = caller.get(Tenant, tenant_a.id), caller.get(Party, proposed)
        with pytest.raises(ConflictError) as raised:
            bind_external_identity(
                caller,
                tenant=tenant,
                party=party,
                provider_binding=PROVIDER,
                issuer=ISSUER,
                subject=subject,
                bound_by="canary",
                reason="fresh collision",
            )
        assert str(raised.value) == (
            "this external subject is already bound to a different party at "
            "this provider. Disabling that binding does NOT release the "
            "subject: the row keeps occupying the unique key, so the only "
            "paths are to re-enable it for the same party or to delete it "
            "deliberately, which discards the evidence of who bound it."
        )
        assert raised.value.__cause__ is None
        assert caller.get_transaction() is root
        assert caller.scalar(select(Party.id).where(Party.id == earlier)) == earlier
        assert str(caller.scalar(text("SELECT app_current_tenant_id()"))) == str(
            tenant_a.id
        )
        assert (
            stale.party_id == proposed
        )  # no populate-existing clobber of caller state
    finally:
        caller.rollback()
        caller.close()
        locker.rollback()
        locker.close()
        admin_session.execute(
            text("DELETE FROM external_identity_bindings WHERE id=:id"),
            {"id": str(bid)},
        )
        for pid in (owner, proposed):
            admin_session.execute(
                text("DELETE FROM parties WHERE id=:id"), {"id": str(pid)}
            )
        admin_session.commit()


@pytest.mark.parametrize("command", ("login", "logout"))
@pytest.mark.parametrize("fault", ("pending", "cancel"))
def test_reference_auth_actual_app_role_post_write_fault_recovers_same_root(
    tenant_sessionmaker, tenant_a, command, fault
):
    from dotmac_kernel.models import AuthSession, PartyType, UserCredential
    from dotmac_kernel.security import hash_password, hash_token
    from dotmac_kernel.transactions import ConflictSavepointProtocolError
    from sqlalchemy import insert, select

    from app.features.auth import service as reference_auth
    from app.features.auth.schemas import LoginRequest
    from tests.unit.test_security_baseline import _legacy_pbkdf2_hash

    class Cancelled(BaseException):
        pass

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    listener = None
    try:
        assert session.scalar(text("SELECT current_user")) == "app_user"
        pid = _insert_party(session, tenant_id=tenant_a.id)
        email = session.scalar(select(Party.email).where(Party.id == pid))
        password = "reference-auth-physical-rollback-canary"
        seed_hash = (
            _legacy_pbkdf2_hash(password)
            if command == "login"
            else hash_password(password)
        )
        credential_id = session.scalar(
            insert(UserCredential.__table__)
            .values(
                tenant_id=tenant_a.id,
                party_id=pid,
                password_hash=seed_hash,
            )
            .returning(UserCredential.id)
        )
        cached_credential = session.get(UserCredential, credential_id)
        tenant = session.get(Tenant, tenant_a.id)
        token = None
        if command == "logout":
            token = reference_auth.login(
                session, tenant, LoginRequest(email=email, password=password)
            ).access_token
            cached = session.scalar(
                select(AuthSession).where(AuthSession.token_hash == hash_token(token))
            )
            assert cached.revoked_at is None
        root, connection = session.get_transaction(), session.connection()
        injected = Party(
            id=uuid.uuid4(),
            tenant_id=tenant_a.id,
            party_type=PartyType.person,
            display_name="Reference callback pending",
        )
        invoked = []

        if fault == "cancel":
            # An exit exception raised inside cursor execution invalidates
            # the connection by SQLAlchemy design (is_exit_exception), after
            # which no savepoint rollback can preserve the root; raise at
            # the ORM execute boundary so cancellation itself is exercised.
            target, name = session, "do_orm_execute"

            def listener(state):
                if not invoked and (
                    (command == "login" and state.is_insert)
                    or (command == "logout" and state.is_update)
                ):
                    invoked.append(1)
                    raise Cancelled()
                return None

        else:
            target, name = connection, "after_cursor_execute"

            def listener(conn, cursor, statement, parameters, context, executemany):
                if not invoked and (
                    (command == "login" and context.isinsert)
                    or (command == "logout" and context.isupdate)
                ):
                    invoked.append(1)
                    session.add(injected)

        event.listen(target, name, listener)
        with pytest.raises(
            Cancelled if fault == "cancel" else ConflictSavepointProtocolError
        ):
            if command == "login":
                reference_auth.login(
                    session, tenant, LoginRequest(email=email, password=password)
                )
            else:
                reference_auth.web_logout(session, tenant, token)
        event.remove(target, name, listener)
        listener = None
        assert invoked == [1]
        assert session.get_transaction() is root
        assert session.scalar(select(Party.id).where(Party.id == pid)) == pid
        assert str(session.scalar(text("SELECT app_current_tenant_id()"))) == str(
            tenant_a.id
        )
        rows = tuple(
            session.execute(
                select(AuthSession.id, AuthSession.revoked_at).where(
                    AuthSession.party_id == pid
                )
            )
        )
        assert len(rows) == (1 if command == "logout" else 0)
        if command == "logout":
            assert rows[0].revoked_at is None
            assert cached.revoked_at is None
        assert cached_credential.password_hash == seed_hash
        assert not session.new and not session.dirty and not session.deleted
        from dotmac_kernel.transactions import conflict_savepoint

        with conflict_savepoint(session):
            session.connection()
        assert session.get_transaction() is root
    finally:
        if listener is not None:
            event.remove(target, name, listener)
        session.rollback()
        session.close()


@pytest.mark.parametrize("command", ("finalize", "disable", "rebind", "stamp"))
def test_binding_release_cancellation_restores_known_cached_fields_and_prior_root(
    tenant_sessionmaker, tenant_a, command
):
    from datetime import UTC, datetime, timedelta

    from dotmac_kernel.external_identity import (
        disable_external_identity_binding,
        finalize_external_login,
        record_external_authentication,
    )
    from dotmac_kernel.models import AuthSession
    from sqlalchemy import insert, update

    class Cancelled(BaseException):
        pass

    session = _as_tenant(tenant_sessionmaker, tenant_a.id)
    try:
        pid = _insert_party(session, tenant_id=tenant_a.id)
        subject = "cached-" + uuid.uuid4().hex
        bid = _insert_binding(
            session, tenant_id=tenant_a.id, party_id=pid, subject=subject
        )
        if command == "rebind":
            session.execute(
                update(ExternalIdentityBinding.__table__)
                .where(ExternalIdentityBinding.id == bid)
                .values(is_active=False)
            )
        sid = session.scalar(
            insert(AuthSession.__table__)
            .values(
                tenant_id=tenant_a.id,
                party_id=pid,
                token_hash=uuid.uuid4().hex * 2,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                external_identity_binding_id=bid,
            )
            .returning(AuthSession.id)
        )
        tenant, party = session.get(Tenant, tenant_a.id), session.get(Party, pid)
        binding, cached_session = (
            session.get(ExternalIdentityBinding, bid),
            session.get(AuthSession, sid),
        )
        before = (
            binding.is_active,
            binding.bound_by,
            binding.bind_reason,
            binding.updated_at,
            cached_session.updated_at,
        )
        root = session.get_transaction()
        calls = []

        def cancel(db):
            calls.append(1)
            raise Cancelled()

        event.listen(session, "before_commit", cancel)
        try:
            with pytest.raises(Cancelled):
                if command == "finalize":
                    finalize_external_login(
                        session,
                        tenant=tenant,
                        provider_binding=PROVIDER,
                        issuer=ISSUER,
                        subject=subject,
                    )
                elif command == "disable":
                    disable_external_identity_binding(
                        session, tenant=tenant, binding_id=bid
                    )
                elif command == "stamp":
                    record_external_authentication(
                        session, tenant=tenant, binding_id=bid
                    )
                else:
                    bind_external_identity(
                        session,
                        tenant=tenant,
                        party=party,
                        provider_binding=PROVIDER,
                        issuer=ISSUER,
                        subject=subject,
                        bound_by="changed",
                        reason="changed",
                    )
        finally:
            event.remove(session, "before_commit", cancel)
        assert calls == [1]
        assert session.get_transaction() is root
        # No manual refresh: recovered Core effects must not survive as stale
        # clean identity-map proof. Known affected attributes were expired.
        assert (
            binding.is_active,
            binding.bound_by,
            binding.bind_reason,
            binding.updated_at,
            cached_session.updated_at,
        ) == before
        assert binding.last_authenticated_at is None
        assert cached_session.revoked_at is None
        assert str(session.scalar(text("SELECT app_current_tenant_id()"))) == str(
            tenant_a.id
        )
        assert not session.new and not session.dirty and not session.deleted
        from dotmac_kernel.transactions import conflict_savepoint

        with conflict_savepoint(session):
            session.connection()
        assert session.get_transaction() is root
    finally:
        session.rollback()
        session.close()
