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
from dotmac_kernel.models import Party, Tenant
from sqlalchemy import create_engine, text
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
            cause = str(exc.__cause__) if exc.__cause__ else ""
            session.rollback()
            return {
                "outcome": "conflict",
                "cause": cause,
                "tenant_scope_intact": (
                    scope is not None
                    and str(scope) == str(tenant_id)
                    and readable is not None
                ),
            }

        session.commit()
        return {"outcome": "bound"}
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
            )
            for party_id, subject in zip(parties, subjects, strict=True)
        ]
        return [f.result(timeout=_FUTURE_TIMEOUT) for f in futures]


def _assert_one_won(results: list[dict[str, object]], *, constraint: str) -> None:
    outcomes = sorted(str(r["outcome"]) for r in results)
    assert outcomes == [
        "bound",
        "conflict",
    ], f"expected exactly one winner and one refusal, got {outcomes}: {results}"
    loser = next(r for r in results if r["outcome"] == "conflict")
    assert constraint in str(
        loser["cause"]
    ), f"a different constraint arbitrated: {loser['cause']}"
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
